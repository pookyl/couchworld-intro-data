"""Conservative aggregation must not turn one matching pair into a series marker."""
from pathlib import Path
import json
import sys
import unittest

try:
    import numpy
except ImportError:
    numpy = None

if numpy is not None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from validate_ranked_intros import conservative_candidates, select_episodes


@unittest.skipIf(numpy is None, 'Optional NumPy dependency is not installed')
class ConservativeReviewTest(unittest.TestCase):
    def test_sampling_skips_premiere_and_orders_episodes(self):
        detail = {'urls': [{'index': str(i)} for i in (4, 1, 0, 3, 2, 5)]}
        self.assertEqual([e['index'] for e in select_episodes(detail)], ['2', '3', '4'])

    def test_missing_fourth_episode_does_not_fall_back_to_premiere(self):
        detail = {'urls': [{'index': str(i)} for i in (1, 2, 3, 5)]}
        self.assertEqual([e['index'] for e in select_episodes(detail)], ['2', '3'])

    def report(self):
        result = {'samples':[{'file':str(i)} for i in (1,2,3)], 'pairs':[]}
        for first, second, end in [('1','2',93.35),('1','3',93.35),('2','3',120.47)]:
            result['pairs'].append({'first':first,'second':second,'candidates':[{
                'first_index_span_seconds':[0,end], 'second_index_span_seconds':[0,end],
                'touches_window_end':False}]})
        return result

    def test_uses_earliest_shared_end_and_moves_back(self):
        result = conservative_candidates(self.report())
        self.assertEqual([e['review_target_seconds'] for e in result['episodes']],[90,90,90])
        self.assertAlmostEqual(result['episodes'][1]['pair_end_spread_seconds'],27.12)

    def test_one_pair_or_truncated_pair_is_not_enough(self):
        report = self.report()
        report['pairs'][2]['candidates'] = []
        self.assertEqual(conservative_candidates(report)['episodes'],[])
        report = self.report()
        report['pairs'][2]['candidates'][0]['touches_window_end'] = True
        self.assertEqual(conservative_candidates(report)['episodes'],[])

    def test_nonoverlapping_pair_matches_rejected(self):
        report = self.report()
        report['pairs'][1]['candidates'][0]['first_index_span_seconds'] = [120,160]
        self.assertEqual(conservative_candidates(report)['status'],'inconsistent_pair_matches')

    def test_cold_open_is_flagged(self):
        report = self.report()
        for pair in report['pairs']:
            for field in ('first_index_span_seconds','second_index_span_seconds'):
                pair['candidates'][0][field][0] = 10
        self.assertTrue(all(e['preserve_cold_open'] for e in conservative_candidates(report)['episodes']))

    def test_numpy_times_are_json_serializable(self):
        report = self.report()
        for pair in report['pairs']:
            for field in ('first_index_span_seconds','second_index_span_seconds'):
                pair['candidates'][0][field] = [numpy.float64(x) for x in pair['candidates'][0][field]]
        json.dumps(conservative_candidates(report))


if __name__ == '__main__':
    unittest.main()
