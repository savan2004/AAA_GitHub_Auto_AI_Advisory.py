"""Synthetic unit fixtures only; not actual market prices."""
import unittest
import pandas as pd
from msk_history_incremental_pilot import merge_verified_history


class IncrementalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.old = pd.DataFrame(
            {"Open": 100., "High": 105., "Low": 95., "Close": 102., "Volume": 15000.},
            index=pd.bdate_range(end="2026-11-20", periods=60))
        self.delta = self.old.tail(1).copy()
        self.delta = pd.concat([self.delta, self.old.tail(1).set_axis(
            pd.DatetimeIndex(["2026-11-23"]))])

    def merge(self, delta):
        return merge_verified_history(self.old, delta, "2026-11-20",
                                      "2026-11-23", ["2026-11-23"])

    def test_append(self):
        result = self.merge(self.delta)
        self.assertEqual(len(result), 61)
        pd.testing.assert_frame_equal(result.iloc[:-1], self.old, check_freq=False)

    def test_missing_session(self):
        self.assertIsNone(self.merge(self.delta.iloc[:1]))

    def test_conflicting_overlap(self):
        delta = self.delta.copy()
        delta.iloc[0, 0] = 101.
        self.assertIsNone(self.merge(delta))

    def test_invalid_new_candle(self):
        delta = self.delta.copy()
        delta.iloc[-1, 1] = 90.
        self.assertIsNone(self.merge(delta))

    def test_duplicate(self):
        self.assertIsNone(self.merge(pd.concat([self.delta, self.delta.tail(1)])))

    def test_missing_overlap(self):
        self.assertIsNone(self.merge(self.delta.tail(1)))

    def test_future_row(self):
        future = self.delta.tail(1).set_axis(pd.DatetimeIndex(["2026-11-24"]))
        self.assertIsNone(self.merge(pd.concat([self.delta, future])))



# Incremental cases above recovered from sample/verified-history-incremental.
# Additional cases below are rebuilt regression tests, not live market evidence.
from unittest.mock import patch
import market_data as md
from central_incremental_probe import run_probe


class MarketDataRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {"Open": 100., "High": 105., "Low": 95., "Close": 102., "Volume": 15000.},
            index=pd.bdate_range(end="2026-11-20", periods=60))

    def test_recovery_only_for_missing_symbol(self):
        with patch.object(md, "_download_chunk", return_value={"TCS.NS": self.frame}), \
             patch.object(md, "_recover_missing_symbol", return_value=self.frame.copy()) as recovery:
            result = md.bulk_history(["TCS", "INFY"], retries=0, max_fallbacks=1)
        self.assertEqual(set(result), {"TCS", "INFY"})
        recovery.assert_called_once_with("INFY", "1y")

    def test_bounded_fallback_does_not_fabricate_missing(self):
        with patch.object(md, "_download_chunk", return_value={}), \
             patch.object(md, "_recover_missing_symbol", return_value=None) as recovery:
            result = md.bulk_history(["TCS", "INFY", "SBIN"], retries=0, max_fallbacks=1)
        self.assertEqual(result, {})
        self.assertEqual(recovery.call_count, 1)

    def test_adjusted_data_has_no_unadjusted_fallback(self):
        with patch.object(md, "_download_chunk", return_value={}), \
             patch.object(md, "_recover_missing_symbol") as recovery:
            self.assertEqual(md.bulk_history(["TCS"], adjusted=True, retries=0), {})
        recovery.assert_not_called()

    def test_probe_matches_same_provider_and_no_fallback(self):
        full = pd.concat([self.frame, self.frame.tail(1).set_axis(
            pd.DatetimeIndex(["2026-11-23"]))])
        with patch("central_incremental_probe.bulk_history") as unused:
            from unittest.mock import Mock
            fetch = Mock(side_effect=[{"TCS": full}, {"TCS": full.tail(5)}])
            report = run_probe(["TCS"], "2026-11-20", ["2026-11-23"], fetch=fetch)
        unused.assert_not_called()
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["withheld"], 0)
        self.assertEqual([c.kwargs["period"] for c in fetch.call_args_list], ["1y", "5d"])
        for call in fetch.call_args_list:
            self.assertEqual(call.kwargs["max_fallbacks"], 0)
            self.assertEqual(call.kwargs["cache_ttl"], 0)
            self.assertFalse(call.kwargs["adjusted"])

    def test_probe_withholds_missing_or_changed_delta(self):
        full = pd.concat([self.frame, self.frame.tail(1).set_axis(
            pd.DatetimeIndex(["2026-11-23"]))])
        delta = full.tail(5).copy()
        delta.iloc[0, delta.columns.get_loc("Close")] = 101.
        from unittest.mock import Mock
        for incoming in ({}, {"TCS": delta}, {"TCS": full.tail(5).iloc[:-1]}):
            with self.subTest(incoming=bool(incoming)):
                report = run_probe(["TCS"], "2026-11-20", ["2026-11-23"],
                                   fetch=Mock(side_effect=[{"TCS": full}, incoming]))
                self.assertEqual(report["matched"], 0)
                self.assertEqual(report["withheld"], 1)

    def test_central_official_recovery_only_requested_equities(self):
        with patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy",
                   return_value={"TCS": self.frame}) as official:
            got = md.recover_verified_eod_frames({"TCS": self.frame}, ["TCS"], now=None)
            self.assertEqual(set(got), {"TCS"})
            official.assert_called_once_with({"TCS": self.frame}, ["TCS"], now=None)
            for invalid in (["TCS.BO"], ["^NSEI"], ["BAD/ID"], []):
                self.assertEqual(md.recover_verified_eod_frames({}, invalid, now=None), {})
            self.assertEqual(official.call_count, 1)

    def test_missing_public_history_bounded_and_does_not_replace_existing(self):
        from unittest.mock import Mock
        with patch.object(md, "_download_chunk", return_value={"TCS.NS": self.frame}) as dl, \
             patch("smart_chart_eod_recovery.yahoo_host_history") as alt:
            got = md.recover_missing_public_histories(["INFY","TCS"],
                         {"INFY":self.frame}, session="2026-11-20")
            self.assertEqual(set(got), {"TCS"})
            dl.assert_called_once_with(["TCS.NS"], "1y", retries=0,
                                       adjusted=False, timeout=6)
            alt.assert_not_called()
        with patch.object(md, "_download_chunk", return_value={}) as dl, \
             patch("smart_chart_eod_recovery.yahoo_host_history", return_value=self.frame.iloc[:-1]) as alt:
            self.assertEqual(md.recover_missing_public_histories(
                ["TCS","INFY","DMART","SBIN"], {}, session="2026-11-20"), {})
            self.assertEqual(dl.call_count, 3)
            self.assertEqual(alt.call_count, 3)
        self.assertEqual(md.recover_missing_public_histories(["TCS.BO"], {},
                         session="2026-11-20"), {})

    def test_probe_rejects_alias_duplicates_and_bad_sessions(self):
        from unittest.mock import Mock
        fetch = Mock()
        for symbols, sessions in ((["TCS", "TCS.NS"], ["2026-11-23"]),
                                  (["TCS"], ["2026-11-20"]),
                                  (["TCS"], ["2026-11-24", "2026-11-23"])):
            with self.assertRaises(ValueError):
                run_probe(symbols, "2026-11-20", sessions, fetch=fetch)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
