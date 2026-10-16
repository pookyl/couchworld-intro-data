"""Algorithm checks using synthetic fingerprints, with no network or media."""
import importlib.util
from pathlib import Path
import unittest

try:
    import numpy as np
except ImportError:
    np = None

if np is not None:
    spec = importlib.util.spec_from_file_location('analyze_intro', Path(__file__).resolve().parents[1] / 'scripts/analyze_intro.py')
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)


@unittest.skipIf(np is None, 'Optional analysis dependency NumPy is not installed')
class AudioMatchingTest(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(20261015)

    def random(self, count):
        return self.rng.integers(0, 2**32, count, dtype=np.uint32)

    def test_shifted_shared_segment_with_bit_noise(self):
        shared = self.random(400)
        first = np.r_[self.random(50), shared, self.random(100)]
        second = np.r_[self.random(130), shared ^ np.uint32(1), self.random(30)]
        result = analysis.repeated_audio(first, second)
        self.assertTrue(result)
        match = result[0]
        self.assertAlmostEqual(match['offset_seconds'], -80 * analysis.ITEM_SECONDS, places=2)
        self.assertGreater(match['duration_seconds'], 45)
        self.assertFalse(match['touches_window_end'])

    def test_unrelated_and_constant_signals_rejected(self):
        self.assertEqual(analysis.repeated_audio(self.random(500), self.random(500)), [])
        self.assertEqual(analysis.repeated_audio(np.zeros(500), np.zeros(500)), [])

    def test_truncated_match_is_flagged(self):
        shared = self.random(400)
        result = analysis.repeated_audio(shared, shared)
        self.assertTrue(result[0]['touches_window_end'])

    def test_short_shared_jingle_rejected(self):
        shared = self.random(60)
        self.assertEqual(analysis.repeated_audio(np.r_[shared, self.random(400)],
                                               np.r_[shared, self.random(400)]), [])


if __name__ == '__main__':
    unittest.main()
