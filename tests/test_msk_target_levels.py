"""Synthetic target fixtures: no live market prices or profit claims."""
import unittest
import numpy as np
from msk_target_levels import calculate_targets, target_quality


class TargetTests(unittest.TestCase):
    def plan(self, high, low=None, i=60, side="LONG", entry=100., stop=95.):
        if low is None:
            low = np.full(len(high), 90.)
        return calculate_targets(high, low, i, entry=entry, stop=stop,
                                 atr=2., side=side, first_distance=7.5,
                                 second_distance=15.)

    def test_nearest_resistance_limits_first_target(self):
        high = np.full(61, 98.)
        high[30] = 104.
        high[40] = 112.
        result = self.plan(high)
        self.assertAlmostEqual(result["t1"], 103.8)
        self.assertAlmostEqual(result["t2"], 111.8)
        self.assertEqual(result["t1_basis"], "resistance")
        self.assertFalse(target_quality(dict(result, entry=100., sl=95.)))

    def test_second_target_cannot_skip_first_barrier_after_projection(self):
        high = np.full(61, 98.)
        high[30] = 110.
        result = self.plan(high)
        self.assertAlmostEqual(result["t1"], 107.5)
        self.assertAlmostEqual(result["t2"], 109.8)
        self.assertEqual(result["t1_basis"], "projection")
        self.assertEqual(result["t2_basis"], "resistance")
        self.assertTrue(target_quality(dict(result, entry=100., sl=95.)))

    def test_short_targets_use_support_and_correct_direction(self):
        high = np.full(61, 108.)
        low = np.full(61, 102.)
        low[30], low[40] = 96., 88.
        result = self.plan(high, low, side="SHORT", stop=105.)
        self.assertAlmostEqual(result["t1"], 96.2)
        self.assertAlmostEqual(result["t2"], 88.2)
        self.assertEqual(result["t1_basis"], "support")
        self.assertFalse(target_quality(dict(result, entry=100., sl=105., side="SHORT")))

    def test_no_future_leakage_and_projection_label(self):
        high = np.full(70, 98.)
        before = self.plan(high)
        high[60:] = 105.
        after = self.plan(high)
        self.assertEqual(before, after)
        self.assertEqual(before["t1_basis"], "projection")
        self.assertEqual(before["t2_basis"], "projection")

    def test_invalid_inputs_and_legacy_cache_rejected(self):
        high = np.full(61, 98.)
        self.assertIsNone(self.plan(high, stop=100.))
        high[20] = float("nan")
        self.assertIsNone(self.plan(high))
        self.assertFalse(target_quality({"entry":100., "sl":95., "t1":107.5, "t2":115.}))
        high[20] = 100.1
        self.assertIsNone(self.plan(high))


if __name__ == "__main__":
    unittest.main()
