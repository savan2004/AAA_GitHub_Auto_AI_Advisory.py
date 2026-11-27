"""Synthetic chart fixtures, never real prices or claims about profitability."""
import unittest
import numpy as np
from msk_target_levels import calculate_targets, target_quality


def chart():
    h = np.full(61, 103.)
    l = np.full(61, 99.5)
    h[20], h[35] = 110.2, 120.2
    l[40] = 99.2
    return h, l


class TargetTests(unittest.TestCase):
    def plan(self, h, l, side="LONG", i=60):
        return calculate_targets(h, l, i, entry=100., atr=2., side=side)

    def test_chart_allows_one_to_ten_without_reward_cap(self):
        h,l = chart()
        p = self.plan(h,l)
        self.assertAlmostEqual(p["sl"],99.)
        self.assertAlmostEqual(p["t1"],110.)
        self.assertAlmostEqual(p["t2"],120.)
        self.assertAlmostEqual(p["rr1"],10.)
        self.assertAlmostEqual(p["rr2"],20.)
        self.assertTrue(target_quality(dict(p,entry=100.,atr=2.)))

    def test_near_resistance_is_not_skipped_for_prettier_ratio(self):
        h,l = chart()
        h[45] = 101.
        h[43:48] = [100.6,100.7,101.,100.7,100.6]
        p = self.plan(h,l)
        self.assertAlmostEqual(p["t1"],100.8)
        self.assertFalse(target_quality(dict(p,entry=100.,atr=2.)))

    def test_one_resistance_no_invented_second_target(self):
        h,l = chart()
        h[35] = 103.
        p = self.plan(h,l)
        self.assertIsNone(p["t2"])
        self.assertTrue(target_quality(dict(p,entry=100.,atr=2.)))

    def test_no_resistance_or_support_no_plan(self):
        h,l = chart()
        h[:] = 103.
        self.assertIsNone(self.plan(h,l))
        h,l = chart()
        l[:] = 99.5
        self.assertIsNone(self.plan(h,l))

    def test_short_mirrored_support_resistance(self):
        h,l = chart()
        p = self.plan(200-l,200-h,side="SHORT")
        self.assertAlmostEqual(p["sl"],101.)
        self.assertAlmostEqual(p["t1"],90.)
        self.assertAlmostEqual(p["rr1"],10.)
        self.assertTrue(target_quality(dict(p,entry=100.,atr=2.,side="SHORT")))

    def test_no_future_data_and_invalid_inputs(self):
        h,l = chart()
        p = self.plan(h,l)
        self.assertEqual(p,self.plan(np.r_[h,150.,151.],np.r_[l,70.,71.]))
        l[10] = np.nan
        self.assertIsNone(self.plan(h,l))
        self.assertFalse(target_quality({"target_model":"structure-v1"}))

    def test_too_tight_stop_is_rejected_not_inflated(self):
        h,l = chart()
        l[:] = 99.95
        l[40] = 99.9
        self.assertIsNone(self.plan(h,l))

    def test_target_quality_rejects_nonfinite_second_target(self):
        h,l = chart()
        p = self.plan(h,l)
        self.assertFalse(target_quality(dict(p,entry=100.,atr=2.,t2=float("nan"),t2_anchor=120.)))

    def test_unverified_metadata_rejected(self):
        h,l = chart()
        p = self.plan(h,l)
        self.assertFalse(target_quality(dict(p,entry=100.,atr=2.,stop_anchor=90.)))

if __name__ == "__main__":
    unittest.main()
