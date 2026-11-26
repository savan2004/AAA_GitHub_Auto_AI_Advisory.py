"""Offline, synthetic fixtures only. No source, Telegram or database I/O."""
import unittest
import numpy as np
import pandas as pd
from msk_chart_release import validated_daily_history, wilder_rsi, rounded_levels, chart_plan

SESSION = "2026-11-13"


def history():
    h = np.full(75, 103.)
    l = np.full(75, 99.5)
    h[20], h[35] = 110.2, 120.2
    l[40] = 99.2
    return pd.DataFrame(dict(Open=100., High=h, Low=l, Close=100., Volume=1000.),
                        index=pd.bdate_range(end=SESSION, periods=75))


class ChartReleaseTests(unittest.TestCase):
    def test_current_history_sorted_and_future_excluded(self):
        data = history()
        future = data.tail(1).copy()
        future.index = pd.DatetimeIndex(["2026-11-16"])
        future["Close"] = 999.
        got = validated_daily_history(pd.concat([future, data.iloc[::-1]]), SESSION)
        pd.testing.assert_frame_equal(got, data, check_freq=False)

    def test_missing_invalid_and_conflicting_historical_bars_rejected(self):
        data = history()
        for column, value in [("Open", 999.), ("Volume", -1.), ("High", np.inf), ("Close", np.nan)]:
            invalid = data.copy()
            invalid.loc[invalid.index[30], column] = value
            self.assertIsNone(validated_daily_history(invalid, SESSION))
        self.assertIsNone(validated_daily_history(data.iloc[:-1], SESSION))
        duplicate = data.iloc[[30]].copy()
        duplicate["Close"] = 100.5
        self.assertIsNone(validated_daily_history(pd.concat([data, duplicate]), SESSION))
        pd.testing.assert_frame_equal(validated_daily_history(pd.concat([data, data.iloc[[30]]]), SESSION),
                                      data, check_freq=False)

    def test_leading_empty_bulk_slots_allowed_but_interior_empty_rejected(self):
        data = history()
        leading = data.copy()
        leading.iloc[:2] = np.nan
        self.assertEqual(len(validated_daily_history(leading, SESSION)), 73)
        leading.iloc[10] = np.nan
        self.assertIsNone(validated_daily_history(leading, SESSION))

    def test_adjustment_ulp_only_and_diagnostic_reasons(self):
        data = history()
        data.loc[data.index[20], "High"] = np.nextafter(100., 0.)
        got = validated_daily_history(data, SESSION)
        self.assertIsNotNone(got)
        self.assertEqual(got.High.iloc[20], 100.)
        self.assertEqual(got.Close.iloc[20], 100.)
        data.loc[data.index[20], "High"] = 99.99
        reasons = {}
        self.assertIsNone(validated_daily_history(data, SESSION, diagnostics=reasons))
        self.assertEqual(reasons, {"inconsistent_ohlc": 1})
        reasons = {}
        self.assertIsNone(validated_daily_history(history().iloc[:-1], SESSION, diagnostics=reasons))
        self.assertEqual(reasons, {"stale_last_date": 1})
        data = history()
        data.iloc[-1] = np.nan
        reasons = {}
        self.assertIsNone(validated_daily_history(data, SESSION, diagnostics=reasons))
        self.assertEqual(reasons, {"missing_expected_session": 1})

    def test_wilder_rsi_monotonic_flat_and_seed(self):
        self.assertEqual(wilder_rsi(np.arange(1., 40.))[-1], 100.)
        self.assertEqual(wilder_rsi(np.arange(40., 1., -1))[-1], 0.)
        self.assertEqual(wilder_rsi(np.full(40, 100.))[-1], 50.)
        values = wilder_rsi([1., 2., 1., 3., 2.], 3)
        self.assertTrue(np.isnan(values[:3]).all())
        self.assertAlmostEqual(values[3], 75.)
        self.assertAlmostEqual(values[4], 600/11)

    def test_chart_plan_uses_chart_levels_and_allows_one_to_ten(self):
        sl,t1,t2,rr1,rr2 = chart_plan(history(), "BUY", 2.)
        self.assertEqual((sl,t1,t2), (99.,110.,120.))
        self.assertEqual((rr1,rr2), (10.,20.))

    def test_no_resistance_or_second_target_never_invented(self):
        data = history()
        data.loc[data.index[35], "High"] = 103.
        self.assertIsNone(chart_plan(data, "BUY", 2.)[2])
        data["High"] = 103.
        self.assertIsNone(chart_plan(data, "BUY", 2.))
        self.assertIsNone(chart_plan(history(), "WAIT", 2.))

    def test_nearby_barrier_cannot_be_skipped(self):
        data = history()
        data.loc[data.index[43:48], "High"] = [100.6,100.7,101.,100.7,100.6]
        self.assertIsNone(chart_plan(data, "BUY", 2.))

    def test_wire_prices_reject_wrong_direction_and_zero_risk(self):
        self.assertIsNone(rounded_levels("BUY", 100, 0, 0, 0))
        self.assertEqual(rounded_levels("SELL",100,101,90,0), (101.,90.,None,10.,None))
        for args in [("BUY",100,100,110,120), ("BUY",100,99,100.5,120),
                     ("SELL",100,99,90,80), ("BUY",100,99,110,109),
                     ("WAIT",100,99,110,120), ("BUY",100,0,110,0)]:
            with self.assertRaises(ValueError):
                rounded_levels(*args)


if __name__ == "__main__":
    unittest.main()
