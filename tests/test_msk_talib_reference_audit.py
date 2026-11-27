import unittest
import numpy as np
from msk_talib_reference_audit import compare_indicators, fixture


class ReferenceAudit(unittest.TestCase):
    def test_comparison_records_real_reference_and_known_formula_difference(self):
        result=compare_indicators(fixture("volatile"))
        self.assertEqual(len(result),13)
        self.assertEqual(result["SMA50"]["status"],"MATCH")
        self.assertEqual(result["BB_upper"]["status"],"DIFFERENCE")
        self.assertEqual(result["BB_lower"]["status"],"DIFFERENCE")
        self.assertGreater(result["BB_upper"]["existing"],result["BB_upper"]["reference"])

    def test_flat_and_monotonic_edges(self):
        for name in ("flat","rising","falling"):
            self.assertEqual(len(compare_indicators(fixture(name))),13)
        self.assertAlmostEqual(compare_indicators(fixture("rising"))["RSI14"]["reference"],100)
        self.assertAlmostEqual(compare_indicators(fixture("falling"))["RSI14"]["reference"],0)

    def test_bad_bars_rejected(self):
        frame=fixture("volatile")
        for bad in (frame.iloc[:10],frame.iloc[::-1],frame.assign(Close=np.nan),
                    frame.assign(High=0),frame.assign(Low=10000)):
            with self.assertRaises(ValueError):
                compare_indicators(bad)
