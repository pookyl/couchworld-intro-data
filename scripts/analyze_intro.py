#!/usr/bin/env python3
"""Offline experiment: find repeated audio, never write production intro markers.

Requires NumPy >= 2 and the official Chromaprint 1.6.2 fpcalc executable.
Algorithm 2 timing is taken from Chromaprint's fingerprinter_configuration.cpp/.h.
This is an independent matcher, not a port of Intro Skipper.
"""

import argparse
import itertools
import json
from pathlib import Path
import subprocess
import time

import numpy as np


ITEM_SECONDS = 1365 / 11025
BUFFER_SECONDS = (19 * 1365 + 2731) / 11025


def runs(mask):
    """Half-open runs of true values, including runs touching either edge."""
    changes = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))


def repeated_audio(first, second, minimum_seconds=20, maximum_bits=4):
    """Search time offsets for long spans with low 32-bit Hamming distance.

    Smooth over nine items (~1.1 s), bridge at most sixteen items (~2 s),
    and reject spans shorter than minimum_seconds. A candidate is evidence
    of repetition only: an ad, recap or shared silence can also repeat.
    """
    first = np.asarray(first, dtype=np.uint32)
    second = np.asarray(second, dtype=np.uint32)
    minimum_items = int(np.ceil(minimum_seconds / ITEM_SECONDS))
    if len(first) < minimum_items or len(second) < minimum_items:
        return []
    candidates = []
    for offset in range(-(len(second) - minimum_items), len(first) - minimum_items + 1):
        ai, bi = max(0, offset), max(0, -offset)
        count = min(len(first) - ai, len(second) - bi)
        distance = np.bitwise_count(first[ai:ai + count] ^ second[bi:bi + count]).astype(float)
        smoothed = np.convolve(np.pad(distance, (4, 4), mode='edge'), np.ones(9) / 9, mode='valid')
        mask = smoothed <= maximum_bits
        for start, end in runs(~mask):
            if start > 0 and end < count and end - start <= 16:
                mask[start:end] = True
        for start, end in runs(mask):
            if end - start < minimum_items:
                continue
            # Constant / near-constant fingerprints can be silence. Repetition
            # of a varied signal is necessary, but still does not prove an intro.
            if min(len(np.unique(first[ai+start:ai+end])),
                   len(np.unique(second[bi+start:bi+end]))) < 16:
                continue
            candidates.append({
                'first_index_span_seconds': [round((ai + start) * ITEM_SECONDS, 3),
                                             round((ai + end) * ITEM_SECONDS, 3)],
                'second_index_span_seconds': [round((bi + start) * ITEM_SECONDS, 3),
                                              round((bi + end) * ITEM_SECONDS, 3)],
                'duration_seconds': round((end - start) * ITEM_SECONDS, 3),
                'offset_seconds': round(offset * ITEM_SECONDS, 3),
                'mean_bit_errors': round(float(distance[start:end].mean()), 3),
                'touches_window_end': bool(ai + end == len(first) or bi + end == len(second)),
            })
    candidates.sort(key=lambda item: (-item['duration_seconds'], item['mean_bit_errors']))
    selected = []
    for candidate in candidates:
        a_start, a_end = candidate['first_index_span_seconds']
        if any(abs(candidate['offset_seconds'] - old['offset_seconds']) < 1 and
               min(a_end, old['first_index_span_seconds'][1]) >
               max(a_start, old['first_index_span_seconds'][0]) for old in selected):
            continue
        selected.append(candidate)
        if len(selected) == 5:
            break
    return selected


def analyze(paths, fpcalc, sample_seconds=180):
    started = time.monotonic()
    samples = []
    fingerprints = []
    for path in paths:
        result = subprocess.run([fpcalc, '-algorithm', '2', '-raw', '-json',
                                 '-length', str(sample_seconds), str(path)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError(f'fpcalc failed for {path.name}; exit {result.returncode}')
        data = json.loads(result.stdout)
        fingerprints.append(data['fingerprint'])
        samples.append({'file': path.name, 'duration_seconds': data['duration'],
                        'fingerprint_items': len(data['fingerprint'])})
    pairs = []
    for i, j in itertools.combinations(range(len(paths)), 2):
        pairs.append({'first': paths[i].name, 'second': paths[j].name,
                      'candidates': repeated_audio(fingerprints[i], fingerprints[j])})
    return {
        'status': 'experimental_candidates_only',
        'sample_limit_seconds': sample_seconds,
        'algorithm': {'fingerprint': 'Chromaprint algorithm 2',
                      'item_seconds': ITEM_SECONDS, 'buffer_seconds': BUFFER_SECONDS,
                      'maximum_smoothed_bit_errors': 4, 'minimum_span_seconds': 20},
        'timing_note': 'Spans use fingerprint index time, not exact video boundaries. '
                       'Fingerprint buffering, smoothing and gap bridging affect edges. '
                       'Window-edge candidates are incomplete. Visual verification is required.',
        'samples': samples, 'pairs': pairs,
        'analysis_seconds': round(time.monotonic() - started, 3),
        'production_markers_written': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples', type=Path, nargs='+', help='Local audio/video samples')
    parser.add_argument('--fpcalc', default='fpcalc')
    parser.add_argument('--sample-seconds', type=int, default=180)
    parser.add_argument('--output', type=Path, required=True, help='Experimental report, outside api/')
    args = parser.parse_args()
    if len(args.samples) < 2 or not 20 <= args.sample_seconds <= 180:
        parser.error('Provide at least two samples; sample-seconds must be between 20 and 180.')
    production = Path(__file__).resolve().parents[1] / 'api'
    if args.output.resolve() == production or production in args.output.resolve().parents:
        parser.error('Experimental reports must not be written to the production api directory.')
    if args.output.resolve() in [path.resolve() for path in args.samples]:
        parser.error('The output must not overwrite an input sample.')
    try:
        report = analyze(args.samples, args.fpcalc, args.sample_seconds)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Analysis failed: {error}\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Report: {args.output}; analysis: {report["analysis_seconds"]} s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
