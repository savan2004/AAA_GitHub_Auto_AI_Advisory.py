import unittest
import numpy as np
from msk_math_reference_audit import compare_indicators, fixture, reference_values, smooth, KINDS


class ReferenceAudit(unittest.TestCase):
    def test_all_indicators_on_five_distinct_fixtures(self):
        for kind in KINDS:
            rows=compare_indicators(fixture(kind))
            self.assertEqual(len(rows),13)
            for name,row in rows.items():
                with self.subTest(kind=kind,indicator=name):
                    self.assertEqual(row["status"],"MATCH",row)

    def test_reference_known_values(self):
        self.assertEqual(smooth([2,4,6,8],2,.5),[3,4.5,6.25])
        flat=reference_values([101]*300,[99]*300,[100]*300)
        for key in ("EMA20","SMA50","BB_mid","BB_upper","BB_lower"):
            self.assertEqual(flat[key],100)
        self.assertEqual(flat["RSI14"],50)
        self.assertEqual(flat["ATR14"],2)
        for key in ("MACD","MACD_signal","MACD_hist","ADX14","PLUS_DI14","MINUS_DI14"):
            self.assertEqual(flat[key],0)
        for kind,want in (("rising",100),("falling",0)):
            self.assertAlmostEqual(compare_indicators(fixture(kind))["RSI14"]["reference"],want)

    def test_bad_bars_rejected(self):
        frame=fixture("volatile")
        for bad in (frame.iloc[:10],frame.iloc[::-1],frame.assign(Close=np.nan),
                    frame.assign(High=0),frame.assign(Low=10000),frame.assign(Open=0)):
            with self.assertRaises(ValueError):
                compare_indicators(bad)

    def test_reference_does_not_hide_changed_production(self):
        from unittest.mock import patch
        with patch("technical_indicators.calc_sma",return_value=1):
            result=compare_indicators(fixture("volatile"))
        self.assertEqual(result["SMA50"]["status"],"DIFFERENCE")
