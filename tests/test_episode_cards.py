"""Episode labels must be stable, correct and consistent across sampled episodes."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from episode_cards import episode_number, summarize_frames, summarize_series, verify_row
from build_intro_report import episode_card_section


def frame(second, text, confidence=0.99):
    return {'seconds': second, 'lines': [{'text': text, 'confidence': confidence}]}


class EpisodeCardTest(unittest.TestCase):
    def test_chinese_arabic_full_width_and_spaces(self):
        for text, number in [('第 二 集', 2), ('第０３集', 3), ('第十二集', 12),
                             ('第两百零三集', 203), ('第一百二十三集', 123), ('第24話', 24)]:
            self.assertEqual(episode_number(text), number)

    def test_dialogue_partial_and_invalid_labels_are_rejected(self):
        for text in ['下一集', '第二', '我看过第二集', '第二集预告', '第0集', '第十十集', '第零十集', '第1二集']:
            self.assertIsNone(episode_number(text), text)

    def test_requires_adjacent_frames_not_duplicate_lines(self):
        one = frame(90, '第二集')
        one['lines'] *= 2
        self.assertEqual(summarize_frames([one, frame(92, '第二集')], 2)['status'], 'partial')
        review = summarize_frames([frame(90, '第二集'), frame(91, '第二集')], 2)
        self.assertEqual(review['status'], 'confirmed')
        self.assertEqual(review['first_confirmed_seconds'], 90)

    def test_low_confidence_and_wrong_number_do_not_confirm(self):
        self.assertEqual(summarize_frames([frame(90, '第二集', 0.3)], 2)['status'], 'not_found')
        self.assertEqual(summarize_frames([frame(90, '第三集'), frame(91, '第三集')], 2)['status'], 'conflict')

    def test_series_requires_three_distinct_aligned_confirmations(self):
        rows = [summarize_frames([frame(t, f'第{e}集'), frame(t+1, f'第{e}集')], e)
                for e,t in [(2,90),(3,91),(4,92)]]
        self.assertEqual(summarize_series(rows), 'confirmed')
        self.assertEqual(summarize_series(rows[:2]), 'insufficient_samples')
        self.assertEqual(summarize_series([rows[0]] * 3), 'insufficient_samples')
        rows[-1]['first_confirmed_seconds'] = 120
        self.assertEqual(summarize_series(rows), 'timing_mismatch')
        rows[-1]['status'] = 'conflict'
        self.assertEqual(summarize_series(rows), 'conflict')

    def test_no_candidate_does_not_run_ocr(self):
        with patch('episode_cards.OCR') as engine:
            result = verify_row({'conservative_review': {'episodes': []}}, Path('.'))
        engine.assert_not_called()
        self.assertEqual(result['status'], 'no_audio_candidate')

    def test_engine_errors_are_not_reported_as_missing_cards(self):
        rows = [summarize_frames([{'seconds': 90, 'lines': [], 'error': 'failed'}], e)
                for e in (2, 3, 4)]
        self.assertEqual(summarize_series(rows), 'unavailable')

    def test_unavailable_engine_preserves_audio_candidate(self):
        row = {'conservative_review': {'episodes': [{'file':'1-2.wav', 'review_target_seconds':88}]}}
        with tempfile.TemporaryDirectory() as root, patch('episode_cards.OCR', side_effect=RuntimeError('missing_engine')):
            result = verify_row(row, Path(root))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(row['conservative_review']['episodes'][0]['review_target_seconds'], 88)
        self.assertFalse(result['changes_jump_target'])

    def test_successful_scan_adds_evidence_without_moving_targets(self):
        row = {'conservative_review': {'episodes': [
            {'file': f'1-{e}.wav', 'review_target_seconds': 88} for e in (2, 3, 4)]}}
        next_episode = iter((2, 3, 4))

        def recognize(paths):
            episode = next(next_episode)
            return [{'file': str(p), 'lines': [{'text': f'第{episode}集', 'confidence': 1}]
                     if int(p.stem) in (91, 92) else []} for p in paths]

        def extract(command, **kwargs):
            Path(command[-1]).touch()

        with tempfile.TemporaryDirectory() as root, patch('episode_cards.OCR') as engine, \
                patch('episode_cards.subprocess.run', side_effect=extract):
            engine.return_value.backend = 'test'
            engine.return_value.read.side_effect = recognize
            result = verify_row(row, Path(root))
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual([e['review_target_seconds'] for e in row['conservative_review']['episodes']], [88]*3)
        self.assertEqual([e['first_confirmed_seconds'] for e in result['episodes']], [91]*3)

    def test_report_escapes_recognized_text(self):
        rendered = episode_card_section({'episode_card_review': {'status':'partial','episodes':[
            {'episode':2, 'first_confirmed_seconds':91, 'hits':[{'text':'<script>'}], 'mismatches':[]}]}})
        self.assertIn('1:31', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)


if __name__ == '__main__':
    unittest.main()
