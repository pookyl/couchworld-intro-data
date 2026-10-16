#!/usr/bin/env python3
"""Write usable detected intro candidates to the app's static JSON API."""
import argparse
import json
import math
from pathlib import Path

from intro import save_marker_to_directory

DEFAULT_MARKERS = Path(__file__).resolve().parents[1] / 'api/v1/intros/olevod'
REASONS = {
    'incomplete_audio_evidence': '没有完整的三集音频候选',
    'invalid_candidate': '候选格式或时间无效',
    'unmatched_opening_content': '片头前有未匹配内容，需确认起点后再写入',
    'episode_time_difference': '各集候选相差超过 3 秒，需复核统一时间',
    'episode_number_conflict': 'OCR 识别到与样本不符的集数，需复核',
    'existing_marker': '已有标注，未使用 --force',
    'report_only': '本次仅生成报告',
}


def proposed_marker(row):
    review = row.get('conservative_review', {})
    candidates = review.get('episodes', [])
    if review.get('status') != 'review_required' or len(candidates) != 3:
        return None, 'incomplete_audio_evidence'
    try:
        vod = row['vod_id']
        if type(vod) is not int or vod <= 0:
            raise ValueError()
        # Supplementary runs may use another explicitly selected three episodes.
        # Historical reports without this field used episodes 2–4.
        episodes = row.get('selected_episodes', [2, 3, 4])
        if (not isinstance(episodes, list) or len(episodes) != 3
                or any(type(e) is not int or e <= 0 for e in episodes)
                or len(set(episodes)) != 3):
            raise ValueError()
        names = {c['file'] for c in candidates}
        if names != {f'{vod}-{e}.wav' for e in episodes}:
            raise ValueError()
        ends = [c['review_target_seconds'] for c in candidates]
        spans = [c['common_index_span'] for c in candidates]
        if any(type(end) not in (int, float) or not math.isfinite(end) or not 0 < end < 180 for end in ends):
            raise ValueError()
        for start, end in spans:
            if (any(type(n) not in (int, float) or not math.isfinite(n) for n in (start, end))
                    or not 0 <= start < end <= 180 or end-start < 20):
                raise ValueError()
        if any(c.get('preserve_cold_open') or span[0] > 3 for c, span in zip(candidates, spans)):
            return None, 'unmatched_opening_content'
        if max(ends)-min(ends) > 3:
            return None, 'episode_time_difference'
        if row.get('episode_card_review', {}).get('status') == 'conflict':
            return None, 'episode_number_conflict'
        # Earliest sampled target is the conservative whole-series default.
        target = math.floor(min(ends))
        if target <= 0:
            raise ValueError()
        return {'start': 0, 'end': target}, None
    except (KeyError, TypeError, ValueError):
        return None, 'invalid_candidate'


def apply_results(report, directory=DEFAULT_MARKERS, force=False, report_only=False):
    counts = {key: 0 for key in ('written', 'unchanged', 'skipped_existing', 'deferred', 'report_only', 'error')}
    for row in report.get('series', [report]):
        vod = row.get('vod_id')
        target = directory / f'{vod}.json'
        if row.get('processing_status') == 'skipped_existing' or (target.exists() and not force):
            result = {'status': 'skipped_existing', 'reason': 'existing_marker'}
        else:
            marker, reason = proposed_marker(row)
            if marker is None:
                result = {'status': 'deferred', 'reason': reason}
            elif report_only:
                result = {'status': 'report_only', 'reason': 'report_only', 'marker': marker}
            else:
                try:
                    before = target.read_bytes() if target.exists() else None
                    path, _ = save_marker_to_directory(directory, vod, 2, marker,
                                                       series=True, title=row.get('title'))
                    result = {'status': 'unchanged' if before == path.read_bytes() else 'written',
                              'file': path.name, 'marker': marker, 'scope': 'series_default'}
                except (OSError, ValueError) as error:
                    result = {'status': 'error', 'reason': 'marker_write_failed', 'detail': type(error).__name__}
        if result.get('reason') in REASONS:
            result['reason_note'] = REASONS[result['reason']]
        row['marker_write'] = result
        counts[result['status']] += 1
    report['marker_write_summary'] = counts
    report['production_markers_changed'] = counts['written'] > 0
    report['marker_write_policy'] = ('Default: write complete 3-episode audio candidates as a whole-series default; '
                                    'choose the earliest target; defer unmatched opening content, >3s target differences '
                                    'or OCR episode-number conflicts. Preserve explicit episode overrides. No automatic Git push.')
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Saved results.json; no downloads or OCR reruns')
    parser.add_argument('--markers-dir', type=Path, default=DEFAULT_MARKERS)
    parser.add_argument('--force', action='store_true', help='Allow replacing existing defaults; preserve episode overrides')
    parser.add_argument('--report-only', action='store_true', help='Preview without writing markers')
    args = parser.parse_args(argv)
    if args.markers_dir.resolve() in args.input.resolve().parents:
        parser.error('Input report must not be in the marker directory')
    report = json.loads(args.input.read_text())
    counts = apply_results(report, args.markers_dir, args.force, args.report_only)
    args.input.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(counts, ensure_ascii=False))
    if counts['error']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
