#!/usr/bin/env python3
"""Verify episode-number cards near audio candidates; never publish or move forward."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata

DIGITS = dict(zip('零一二三四五六七八九', range(10)))
DIGITS.update({'〇': 0, '两': 2, '兩': 2})


def episode_number(text):
    """Accept a complete card label, not dialogue containing an episode reference."""
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
    match = re.fullmatch(r'第([0-9零〇一二三四五六七八九十百两兩]+)[集话話]', text)
    if not match:
        return None
    value = match[1]
    if value.isascii() and value.isdigit():
        number = int(value)
    elif all(c in DIGITS for c in value):
        number = int(''.join(str(DIGITS[c]) for c in value))
    else:
        # Standard Chinese numerals below 1000; reject malformed OCR strings.
        match = re.fullmatch(r'([一二三四五六七八九两兩]百)?(零)?([一二三四五六七八九两兩]?十)?([一二三四五六七八九两兩])?', value)
        if not match or not (match[1] or match[3]):
            return None
        hundreds, zero, tens, units = match.groups()
        if zero and (not hundreds or tens):
            return None
        number = (DIGITS[hundreds[0]] * 100 if hundreds else 0)
        number += ((DIGITS[tens[0]] if len(tens) == 2 else 1) * 10 if tens else 0)
        number += DIGITS[units] if units else 0
    return number if 0 < number < 1000 else None


def summarize_frames(frames, expected):
    hits, mismatches, failed = [], [], 0
    for frame in sorted(frames, key=lambda f: f['seconds']):
        if frame.get('error'):
            failed += 1
        for line in frame.get('lines', []):
            number = episode_number(line['text'])
            if number is None or line['confidence'] < 0.5:
                continue
            item = {'seconds': frame['seconds'], 'text': line['text'],
                    'confidence': line['confidence'], 'recognized_episode': number}
            (hits if number == expected else mismatches).append(item)
    seconds = sorted({h['seconds'] for h in hits})
    # Two adjacent one-second samples limit single-frame OCR false positives.
    runs = []
    for second in seconds:
        if runs and second == runs[-1][-1] + 1:
            runs[-1].append(second)
        else:
            runs.append([second])
    stable = next((run for run in runs if len(run) >= 2), None)
    status = ('conflict' if mismatches else 'confirmed' if stable else
              'partial' if hits else 'error' if failed else 'not_found')
    return {'episode': expected, 'status': status, 'hits': hits, 'mismatches': mismatches,
            'first_confirmed_seconds': stable[0] if stable else None,
            'failed_frames': failed, 'frames_scanned': len(frames)}


def summarize_series(episodes):
    if len(episodes) != 3 or len({e['episode'] for e in episodes}) != 3:
        return 'insufficient_samples'
    if any(e['status'] == 'conflict' for e in episodes):
        return 'conflict'
    if any(e['status'] == 'error' for e in episodes):
        return 'unavailable'
    if all(e['status'] == 'confirmed' for e in episodes):
        starts = [e['first_confirmed_seconds'] for e in episodes]
        return 'confirmed' if max(starts) - min(starts) <= 5 else 'timing_mismatch'
    if any(e['status'] == 'confirmed' for e in episodes):
        return 'partial'
    return 'not_confirmed'


class OCR:
    def __init__(self, backend, cache):
        self.backend = ('vision' if platform.system() == 'Darwin' else 'tesseract') if backend == 'auto' else backend
        if self.backend == 'vision':
            if platform.system() != 'Darwin' or not shutil.which('swiftc'):
                raise RuntimeError('vision_requires_macos_and_swift')
            source = Path(__file__).with_name('recognize_text.swift')
            cache.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            self.binary = cache / ('vision-ocr-' + digest)
            if not self.binary.exists():
                subprocess.run(['swiftc', '-module-cache-path', str(cache / 'modules'),
                                str(source), '-o', str(self.binary)], check=True,
                               capture_output=True, timeout=120)
        else:
            if not shutil.which('tesseract'):
                raise RuntimeError('install_tesseract_with_chi_sim_and_eng')
            result = subprocess.run(['tesseract', '--list-langs'], check=True, capture_output=True, text=True, timeout=10)
            if not {'chi_sim', 'eng'}.issubset(set(result.stdout.split())):
                raise RuntimeError('install_tesseract_chi_sim_and_eng_language_data')

    def read(self, paths):
        if self.backend == 'vision':
            result = subprocess.run([str(self.binary), *map(str, paths)], check=True,
                                    capture_output=True, text=True, timeout=120)
            return json.loads(result.stdout)
        results = []
        for path in paths:
            result = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'chi_sim+eng',
                                     '--psm', '11', 'tsv'], check=True, capture_output=True,
                                    text=True, timeout=30)
            grouped = {}
            for word in csv.DictReader(io.StringIO(result.stdout), delimiter='\t'):
                if word['level'] != '5' or not word['text'].strip():
                    continue
                key = tuple(word[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
                grouped.setdefault(key, []).append(word)
            lines = [{'text': ''.join(w['text'] for w in words),
                      'confidence': min(float(w['conf']) for w in words) / 100}
                     for words in grouped.values()]
            results.append({'file': str(path), 'lines': lines})
        return results


def verify_row(row, root, backend='auto'):
    started = time.monotonic()
    candidates = row['conservative_review']['episodes']
    result = {'status': 'no_audio_candidate', 'episodes': [],
              'policy': 'Scan each second from candidate -8 to +20 seconds, bounded by the first 180 seconds. Require the correct episode number in 2 consecutive frames and all 3 card times within 5 seconds. Confirmation only; never move the audio target forward.',
              'changes_jump_target': False}
    if not candidates:
        return result
    try:
        ocr = OCR(backend, root / 'ocr-cache')
        result['backend'] = ocr.backend
        for candidate in candidates:
            stem = Path(candidate['file']).stem
            episode = int(stem.rsplit('-', 1)[1])
            media = root / (stem + '.mkv')
            target = candidate['review_target_seconds']
            start, end = max(0, math.floor(target) - 8), min(179, math.floor(target) + 20)
            with tempfile.TemporaryDirectory(prefix='episode-frames-', dir=root) as temp:
                paths, frames = [], []
                for second in range(start, end + 1):
                    path = Path(temp) / f'{second}.png'
                    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(second),
                                    '-i', str(media), '-frames:v', '1', '-vf', 'scale=1280:-2',
                                    str(path)], check=True, capture_output=True, timeout=20)
                    if not path.exists():
                        raise RuntimeError('frame_unavailable')
                    paths.append(path)
                for item in ocr.read(paths):
                    frames.append({**item, 'seconds': int(Path(item['file']).stem)})
                review = summarize_frames(frames, episode)
                review['search_window_seconds'] = [start, end]
                result['episodes'].append(review)
        result['status'] = summarize_series(result['episodes'])
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        result['status'] = 'unavailable'
        # Do not expose raw subprocess stderr or media paths in public reports.
        result['reason'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Single-series or ranking report JSON')
    parser.add_argument('--work-dir', type=Path, required=True, help='Directory containing sampled MKV files')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=['auto', 'vision', 'tesseract'], default='auto')
    args = parser.parse_args()
    if 'api' in args.output.resolve().parts:
        parser.error('OCR outputs are reports, not production markers')
    report = json.loads(args.input.read_text())
    for row in report.get('series', [report]):
        row['episode_card_review'] = verify_row(row, args.work_dir, args.backend)
        print(json.dumps({'vod_id': row['vod_id'], **row['episode_card_review']}, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
