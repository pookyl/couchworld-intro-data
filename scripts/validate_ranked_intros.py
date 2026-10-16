#!/usr/bin/env python3
"""Detect intro candidates for a live ranking, saved ranking, or single series.

Media and source URLs stay in the ignored work directory. This does not modify
api/, publish a report, or create a scheduled task. NumPy 2 and fpcalc required.
"""
import argparse
import concurrent.futures
from datetime import datetime
import gzip
import hashlib
import json
import math
import platform
import re
import shutil
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.parse
import wave
from zoneinfo import ZoneInfo

from analyze_intro import analyze
from episode_cards import verify_row


def signature(stamp):
    rows = [0, 0, 0, 0]
    for char in stamp:
        for bit in range(4):
            rows[bit] = rows[bit] * 2 + ((int(char) >> (3 - bit)) & 1)
    parts = [format(row, '03x') for row in rows]
    digest = hashlib.md5(stamp.encode()).hexdigest()
    return digest[:3] + parts[0] + digest[6:11] + parts[1] + digest[14:19] + parts[2] + digest[22:27] + parts[3] + digest[30:]


def api(path):
    for attempt in range(3):
        try:
            request = urllib.request.Request('https://api.olelive.com' + path + '?_vv=' + signature(str(int(time.time()))),
                headers={'User-Agent': 'node', 'Accept': 'application/json', 'Accept-Encoding': 'gzip'})
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read(8 * 1024 * 1024)
                if response.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
            envelope = json.loads(body)
            if envelope.get('code') != 0:
                raise ValueError('service_response_code_' + str(envelope.get('code')))
            return envelope['data']
        except (OSError, ValueError, KeyError):
            if attempt == 2:
                raise ValueError('metadata_unavailable') from None
            time.sleep(1)


def duration(path):
    with wave.open(str(path)) as wav:
        return wav.getnframes() / wav.getframerate()


def collect(job, root, previous):
    vod, episode, url = job
    stem = root / f'{vod}-{episode}'
    media, audio = stem.with_suffix('.mkv'), stem.with_suffix('.wav')
    stats_path = stem.with_suffix('.stats.json')
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    base = {'vod_id': vod, 'episode': episode, 'source': 'standard_hd', 'url_hash': url_hash}
    if stats_path.exists() and audio.exists() and media.exists():
        old = json.loads(stats_path.read_text())
        if old.get('status') == 'complete' and old.get('url_hash') == url_hash and 179.8 <= duration(audio) <= 180.01:
            return {**old, 'cached_this_invocation': True}
    # Reuse the earlier samples only if this fresh API response has the same URL.
    prior_detail = previous / f'{vod}-detail.json'
    if prior_detail.exists():
        data = json.loads(prior_detail.read_text())
        prior = next((x for x in data.get('urls', []) if int(x['index']) == episode), {})
        old_stem = previous / stem.name
        if prior.get('url') == url and old_stem.with_suffix('.wav').exists() and old_stem.with_suffix('.mkv').exists():
            seconds = duration(old_stem.with_suffix('.wav'))
            if 179.8 <= seconds <= 180.01:
                for extension in ('.mkv', '.wav'):
                    if not stem.with_suffix(extension).exists():
                        stem.with_suffix(extension).hardlink_to(old_stem.with_suffix(extension))
                old_stats = json.loads(old_stem.with_suffix('.stats.json').read_text())
                result = {**base, 'status': 'complete', 'reused': True, 'audio_seconds': seconds,
                          'bytes': media.stat().st_size, 'fetch_seconds': old_stats['fetch_seconds']}
                stats_path.write_text(json.dumps(result, indent=2))
                print(json.dumps({'vod':vod,'episode':episode,'status':'reused'}), flush=True)
                return result
    started = time.monotonic()
    error = 'download_failed'
    for attempt in range(2):
        try:
            with stem.with_suffix('.fetch.log').open('w') as log:
                result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning',
                    '-rw_timeout', '15000000', '-i', url, '-t', '180', '-map', '0:v:0?', '-map', '0:a:0',
                    '-c', 'copy', str(media)], stdout=log, stderr=log, timeout=120)
            log_text = stem.with_suffix('.fetch.log').read_text()
            if result.returncode or any(x in log_text for x in ('Failed to open segment', 'Error when loading', 'HTTP error')):
                error = 'media_transfer_error'
                continue
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(media), '-t', '180',
                            '-vn', '-ac', '1', '-ar', '11025', '-af',
                            'aresample=11025,atrim=end_sample=1984500', str(audio)], capture_output=True, check=True, timeout=30)
            seconds = duration(audio)
            if not 179.8 <= seconds <= 180.01:
                error = 'incomplete_180_second_sample'
                continue
            result = {**base, 'status': 'complete', 'reused': False, 'audio_seconds': seconds,
                      'bytes': media.stat().st_size, 'fetch_seconds': round(time.monotonic() - started, 2), 'attempts': attempt+1}
            stats_path.write_text(json.dumps(result, indent=2))
            print(json.dumps({'vod':vod,'episode':episode,'status':'complete','seconds':result['fetch_seconds']}), flush=True)
            return result
        except subprocess.TimeoutExpired:
            error = 'download_timeout'
        except (OSError, subprocess.CalledProcessError, wave.Error):
            error = 'media_processing_failed'
    result = {**base, 'status': 'failed', 'reason': error, 'fetch_seconds': round(time.monotonic()-started,2)}
    stats_path.write_text(json.dumps(result, indent=2))
    print(json.dumps({'vod':vod,'episode':episode,'status':error}), flush=True)
    return result


def conservative_candidates(report):
    """Require a mutually overlapping, non-truncated match across all 3 pairs.

    Use the earliest pairwise end per episode, floor it, and move back 3 s.
    These are review targets, never confidence scores or publishable markers.
    """
    names = [sample['file'] for sample in report['samples']]
    if len(names) != 3 or len(report['pairs']) != 3:
        return {'status': 'insufficient_samples', 'episodes': []}
    intervals = {name: [] for name in names}
    for pair in report['pairs']:
        candidates = [c for c in pair['candidates'] if not c['touches_window_end']]
        if not candidates:
            return {'status': 'no_complete_three_way_match', 'episodes': []}
        top = candidates[0]
        intervals[pair['first']].append(top['first_index_span_seconds'])
        intervals[pair['second']].append(top['second_index_span_seconds'])
    output = []
    for name, spans in intervals.items():
        start, end = max(s[0] for s in spans), min(s[1] for s in spans)
        if end - start < 20:
            return {'status': 'inconsistent_pair_matches', 'episodes': []}
        target = max(0, math.floor(end) - 3)
        output.append({'file': name, 'common_index_span': [round(start,3), round(end,3)],
                       'review_target_seconds': target,
                       'pair_end_spread_seconds': round(max(s[1] for s in spans)-end,3),
                       'preserve_cold_open': bool(start > 3)})
    return {'status': 'review_required', 'episodes': output}


def select_episodes(detail):
    """Use episodes 2–4; never substitute episode 1 for missing samples."""
    indexed = {int(e['index']): e for e in detail.get('urls', [])
               if str(e.get('index', '')).isdigit()}
    return [indexed[index] for index in (2, 3, 4) if index in indexed]


REPO_ROOT = Path(__file__).resolve().parents[1]
PERIOD_LABELS = {'day': '日榜', 'week': '周榜', 'month': '月榜', 'all': '总榜'}


def positive_int(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('必须是大于 0 的整数')


def series_link(value):
    # Accept copied Chinese sentence punctuation after an otherwise valid URL.
    value = value.strip().rstrip('，。；,;')
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == 'https' and url.hostname in {'olevod.com', 'www.olevod.com'}
                 and url.username is None and url.password is None and url.port in {None, 443})
    except ValueError:
        valid = False
    match = re.fullmatch(r'/(?:details-2-([1-9][0-9]*)|player/vod/2-([1-9][0-9]*)-[1-9][0-9]*)\.html', url.path) if valid else None
    if not match:
        raise argparse.ArgumentTypeError('请提供欧乐连续剧详情或播放链接，如 https://www.olevod.com/details-2-85528.html')
    return int(match[1] or match[2])


def build_parser():
    parser = argparse.ArgumentParser(description='检测欧乐连续剧片头：默认周榜前 10 名，取第 2–4 集；已有标注默认跳过。只生成复核结果，不发布。')
    parser.add_argument('link', nargs='?', type=series_link, help='仅处理这个连续剧的详情或播放链接')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--url', type=series_link, help='与位置参数 link 等价')
    source.add_argument('--snapshot', type=Path, help='使用保存的榜单 JSON，代替在线抓榜')
    parser.add_argument('--period', choices=PERIOD_LABELS, help='榜单周期：day/week/month/all，默认 week')
    parser.add_argument('--limit', type=positive_int, help='榜单前 N 名，默认 10；已有标注跳过后不递补')
    parser.add_argument('--force', action='store_true', help='即使标注 JSON 已存在也重新分析；仍可复用媒体缓存')
    parser.add_argument('--markers-dir', type=Path, default=REPO_ROOT/'api/v1/intros/olevod', help='检查已有 {剧集ID}.json 的目录')
    parser.add_argument('--work-dir', type=Path, default=REPO_ROOT/'work/ranked-validation', help='样本与 results.json 目录')
    parser.add_argument('--previous-dir', type=Path, default=REPO_ROOT/'work/intro-validation', help='可复用的旧样本目录')
    parser.add_argument('--fpcalc', help='fpcalc 路径；默认从 PATH 或本地已下载版本查找')
    parser.add_argument('--analyze-only', action='store_true', help='离线分析已有样本，须提供 --snapshot 或单剧链接')
    parser.add_argument('--ocr-backend', choices=['auto', 'vision', 'tesseract'], default='auto')
    parser.add_argument('--skip-ocr', action='store_true', help='跳过集数 OCR（默认开启）')
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.link and (args.url or args.snapshot):
        parser.error('link 不能与 --url 或 --snapshot 同时使用')
    args.vod_id = args.link or args.url
    if args.vod_id and (args.period is not None or args.limit is not None):
        parser.error('单剧链接不能与榜单参数 --period/--limit 同时使用')
    if args.analyze_only and not (args.snapshot or args.vod_id):
        parser.error('--analyze-only 需要 --snapshot 或单剧链接，不能在线抓榜')
    args.period = args.period or 'week'
    args.limit = args.limit or 10
    return args


def ranked_items(data, limit):
    if not isinstance(data, list):
        raise ValueError('排行榜返回格式不是列表')
    items, seen = [], set()
    for item in data:
        if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0 or not isinstance(item.get('name'), str):
            raise ValueError('排行榜条目缺少有效 id/name')
        hits = item.get('hits', 0)
        if type(hits) not in (int, float) or not math.isfinite(hits):
            raise ValueError('排行榜 hits 无效')
    for item in sorted(data, key=lambda x: x.get('hits', 0), reverse=True):
        if item['id'] not in seen:
            items.append(item)
            seen.add(item['id'])
    return items[:limit]


def select_source(args):
    now = datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
    if args.vod_id:
        return {'mode': 'single', 'fetched_at': now, 'period': None,
                'data': [{'id': args.vod_id, 'name': f'连续剧 {args.vod_id}'}]}
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
        period = snapshot.get('period', 'week')  # Historical snapshots were weekly.
        if period != args.period:
            raise ValueError(f'快照周期为 {period}，与 --period {args.period} 不符')
        datetime.fromisoformat(snapshot['fetched_at'])
        data = snapshot['data']
        now = snapshot['fetched_at']
    else:
        data = api(f'/v1/pub/index/vod/data/rank/{args.period}/2/{args.limit}')
    selected = ranked_items(data, args.limit)
    if not selected:
        raise ValueError('榜单为空')
    if len(selected) < args.limit:
        print(f'榜单仅返回 {len(selected)} 部不同的连续剧，请求前 {args.limit} 名；按实际数量处理。', flush=True)
    return {'mode': 'ranking', 'fetched_at': now, 'period': args.period, 'data': selected}


def find_fpcalc(explicit=None):
    if explicit:
        return explicit
    installed = shutil.which('fpcalc')
    if installed:
        return installed
    # Only use the locally downloaded binary on its matching platform.
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        local = REPO_ROOT/'work/intro-validation/chromaprint-fpcalc-1.6.2-macos-arm64/fpcalc'
        if local.is_file():
            return str(local)
    raise ValueError('未找到 fpcalc，请安装 Chromaprint 或用 --fpcalc 指定路径')


def load_saved_sample(vod, episode, url, root):
    base = {'vod_id': vod, 'episode': episode, 'source': 'standard_hd', 'status': 'failed', 'reason': 'missing_or_invalid_cached_sample'}
    try:
        stem = root/f'{vod}-{episode}'
        sample = json.loads(stem.with_suffix('.stats.json').read_text())
        if (sample.get('status') == 'complete' and
                sample.get('url_hash') == hashlib.sha256(url.encode()).hexdigest() and
                stem.with_suffix('.mkv').is_file() and
                179.8 <= duration(stem.with_suffix('.wav')) <= 180.01):
            return {**sample, 'cached_this_invocation': True}
    except (OSError, ValueError, wave.Error, EOFError):
        pass
    return base


def run(args):
    args.work_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    snapshot = select_source(args)
    if snapshot['mode'] == 'ranking':
        (args.work_dir/'ranking-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
    series, jobs = [], []
    for rank, item in enumerate(snapshot['data'], 1):
        row = {'rank': rank, 'vod_id': item['id'], 'title': item['name'], 'hits': item.get('hits'),
               'samples': [], 'selected_episodes': [], 'conservative_review': {'status': 'not_processed', 'episodes': []}}
        marker_path = args.markers_dir/f'{item["id"]}.json'
        if marker_path.is_file() and not args.force:
            row['processing_status'] = 'skipped_existing'
            row['reason_note'] = '已有标注 JSON，本次跳过；使用 --force 可重新分析。'
            # Existence is the skip criterion, even for malformed files.
            try:
                marker = json.loads(marker_path.read_text())
                if isinstance(marker, dict) and isinstance(marker.get('title'), str):
                    row['title'] = marker['title']
            except (OSError, ValueError):
                pass
            series.append(row)
            print(f'跳过 {item["id"]}：已有 {marker_path.name}', flush=True)
            continue
        row['processing_status'] = 'pending'
        try:
            detail_path = args.work_dir/f'{item["id"]}-detail.json'
            detail = json.loads(detail_path.read_text()) if args.analyze_only else api(f'/v1/pub/vod/detail/{item["id"]}/true')
            if not isinstance(detail, dict) or detail.get('lock'):
                raise ValueError('unavailable_or_restricted')
            if isinstance(detail.get('name'), str):
                row['title'] = detail['name']
            if not args.analyze_only:
                detail_path.write_text(json.dumps(detail, ensure_ascii=False))
            episodes = select_episodes(detail)
            row['selected_episodes'] = [int(e['index']) for e in episodes]
            for ep in episodes:
                if ep.get('url'):
                    jobs.append((item['id'], int(ep['index']), ep['url']))
        except (ValueError, OSError, KeyError):
            row['processing_status'] = 'metadata_error'
            row['metadata_error'] = 'metadata_unavailable_or_invalid'
        series.append(row)
    print(f'待取样：{len(jobs)} 段', flush=True)
    fpcalc = find_fpcalc(args.fpcalc) if jobs else None
    if args.analyze_only:
        samples = [load_saved_sample(vod, ep, url, args.work_dir) for vod, ep, url in jobs]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            samples = list(pool.map(lambda job: collect(job, args.work_dir, args.previous_dir), jobs))
    for row in series:
        if row['processing_status'] in {'skipped_existing', 'metadata_error'}:
            continue
        row['samples'] = [s for s in samples if s['vod_id'] == row['vod_id']]
        good = [args.work_dir/f'{s["vod_id"]}-{s["episode"]}.wav' for s in row['samples'] if s['status'] == 'complete']
        try:
            if len(good) >= 2:
                row['audio_analysis'] = analyze(good, fpcalc)
                row['conservative_review'] = conservative_candidates(row['audio_analysis'])
            else:
                row['conservative_review'] = {'status': 'insufficient_samples', 'episodes': []}
            row['episode_card_review'] = ({'status': 'skipped', 'episodes': []} if args.skip_ocr
                                          else verify_row(row, args.work_dir, args.ocr_backend))
            row['processing_status'] = 'analyzed' if len(good) == 3 else 'incomplete_samples'
        except (ValueError, OSError, subprocess.SubprocessError):
            row['processing_status'] = 'analysis_error'
            row['conservative_review'] = {'status': 'analysis_error', 'episodes': []}
        print(json.dumps({'rank': row['rank'], 'title': row['title'], 'result': row['conservative_review']}, ensure_ascii=False), flush=True)
    skipped = sum(r['processing_status'] == 'skipped_existing' for r in series)
    report = {'ranking': f'Olevod continuous-series {args.period} top {args.limit}' if not args.vod_id else 'Olevod single series',
              'selection_mode': snapshot['mode'], 'ranking_period': snapshot['period'],
              'requested_limit': args.limit if not args.vod_id else None,
              'selected_count': len(series), 'skipped_count': skipped, 'force': args.force,
              'ranking_fetched_at': snapshot['fetched_at'], 'finished_at': datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
              'sample_policy': 'Episodes 2, 3 and 4 only; skip episode 1; first 180 seconds; standard HD source only. Missing episodes are not substituted.',
              'requested_episodes': [2, 3, 4], 'application_scope': 'series_default',
              'scope_note': 'Sample episodes 2–4; apply accepted results as defaultIntro for the entire series, including episode 1. Preserve explicit episode overrides.',
              'conservative_policy': 'Earliest common pairwise fingerprint end per episode, rounded down, minus 3 seconds. Review candidates only.',
              'source_urls_excluded': True, 'production_markers_changed': False, 'scheduled_task_created': False,
              'wall_seconds': round(time.monotonic()-started, 2), 'series': series}
    fresh = [s for s in samples if s['status'] == 'complete' and not s.get('reused') and not s.get('cached_this_invocation')]
    if fresh:
        ends = [(args.work_dir/f'{s["vod_id"]}-{s["episode"]}.stats.json').stat().st_mtime for s in fresh]
        starts = [end-s['fetch_seconds'] for end, s in zip(ends, fresh)]
        report['acquisition_span_seconds_approx'] = round(max(ends)-min(starts), 2)
    (args.work_dir/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f'完成：选中 {len(series)} 部，跳过 {skipped} 部。结果：{args.work_dir / "results.json"}', flush=True)
    return report


def main(argv=None):
    args = parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(f'错误：{error}') from None


if __name__ == '__main__':
    main()
