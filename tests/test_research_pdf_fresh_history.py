"""No stale, synthetic, or mixed data in PDF source recovery."""
import unittest
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
from research_pdf_fresh_history import (
    normalize_real_history, verified_pdf_history, _alternate_history, _recover_official_day,
)

SESSION = "2026-11-13"


def bars():
    dates = pd.bdate_range(end=SESSION, periods=245)
    close = np.linspace(350, 445, len(dates))
    return pd.DataFrame({
        "Open": close - 1,
        "High": close + 2,
        "Low": close - 2,
        "Close": close,
        "Volume": [100000] * len(dates),
    }, index=dates)


class PdfCurrentEodRecovery(unittest.TestCase):
    def test_current_primary_does_not_download_again(self):
        alternate = Mock(side_effect=AssertionError("Should not refetch"))
        result = verified_pdf_history(
            "SBIN", lambda sym, period: bars(), SESSION, alternate)
        self.assertEqual(str(result.index[-1].date()), SESSION)
        alternate.assert_not_called()

    def test_stale_primary_uses_only_verified_complete_alternative(self):
        old = bars().iloc[:-1]
        alt = Mock(return_value=bars())
        result = verified_pdf_history(
            "DLF", lambda sym, period: old, SESSION, alt)
        self.assertEqual(str(result.index[-1].date()), SESSION)
        self.assertEqual(len(result), len(bars()))
        alt.assert_called_once_with("DLF")

    def test_both_sources_stale_fail_closed(self):
        old = bars().iloc[:-1]
        with self.assertRaisesRegex(ValueError, "stale or"):
            verified_pdf_history(
                "SBIN", lambda sym, period: old, SESSION,
                lambda sym: old)
    
    def test_current_but_too_short_or_invalid_history_rejected(self):
        short = bars().iloc[-50:]
        self.assertIsNone(normalize_real_history(short, SESSION))
        invalid = bars()
        invalid.loc[invalid.index[-1], "Close"] = -3
        self.assertIsNone(normalize_real_history(invalid, SESSION))
        invalid = bars()
        invalid.loc[invalid.index[-1], "High"] = 1
        self.assertIsNone(normalize_real_history(invalid, SESSION))

    def test_future_price_excluded_and_symbol_not_overridden(self):
        frame = bars()
        future = frame.iloc[-1:].copy()
        future.index = [pd.Timestamp("2026-11-16")]
        joined = pd.concat([frame, future])
        verified = normalize_real_history(joined, SESSION)
        self.assertIsNotNone(verified)
        self.assertEqual(str(verified.index[-1].date()), SESSION)
        other = Mock()
        result = verified_pdf_history(
            "TCS", lambda sym, period: frame, SESSION, other)
        pd.testing.assert_frame_equal(result, frame, check_freq=False)
        other.assert_not_called()

    def test_other_issuer_excludes_incomplete_current_day(self):
        completed = bars()
        unfinished = completed.iloc[-1:].copy()
        unfinished.index = [pd.Timestamp("2026-11-16")]
        alternate = Mock(side_effect=AssertionError("No alternate needed"))
        for symbol in ("DCXINDIA", "APLAPOLLO", "DMART"):
            with self.subTest(symbol=symbol):
                result = verified_pdf_history(
                    symbol, lambda sym, period: pd.concat([completed, unfinished]),
                    SESSION, alternate)
                pd.testing.assert_frame_equal(result, completed, check_freq=False)
        alternate.assert_not_called()

    def test_other_issuer_recovers_stale_primary_and_retains_minimum(self):
        complete = bars().iloc[-80:]
        alternate = Mock(return_value=complete)
        result = verified_pdf_history(
            "DCXINDIA", lambda sym, period: complete.iloc[:-1], SESSION, alternate)
        pd.testing.assert_frame_equal(result, complete, check_freq=False)
        alternate.assert_called_once_with("DCXINDIA")
        with self.assertRaisesRegex(ValueError, "stale or"):
            verified_pdf_history("APLAPOLLO", lambda sym, period: complete.iloc[:-1],
                                 SESSION, lambda sym: complete.iloc[:-1])
        with self.assertRaisesRegex(ValueError, "stale or"):
            verified_pdf_history("SBIN", lambda sym, period: complete,
                                 SESSION, lambda sym: complete)
        with self.assertRaisesRegex(ValueError, "stale or"):
            verified_pdf_history("DMART", lambda sym, period: complete.iloc[-59:],
                                 SESSION, lambda sym: complete.iloc[-59:])

    def test_other_issuer_minimum_accepts_exactly_60_completed_sessions(self):
        exactly = bars().iloc[-60:]
        alternate = Mock(side_effect=AssertionError("No alternate for valid 60-session primary"))
        result = verified_pdf_history(
            "DCXINDIA", lambda sym, period: exactly, SESSION, alternate)
        pd.testing.assert_frame_equal(result, exactly, check_freq=False)
        alternate.assert_not_called()

    def test_primary_exception_uses_one_bounded_alternate(self):
        primary = Mock(side_effect=RuntimeError("synthetic provider failure"))
        alternate = Mock(return_value=bars())
        result = verified_pdf_history("APLAPOLLO", primary, SESSION, alternate)
        self.assertEqual(str(result.index[-1].date()), SESSION)
        primary.assert_called_once_with("APLAPOLLO", "1y")
        alternate.assert_called_once_with("APLAPOLLO")

    def test_conflicting_duplicate_day_rejected(self):
        frame = bars()
        duplicate = frame.iloc[-1:].copy()
        duplicate["Close"] -= 1
        self.assertIsNone(normalize_real_history(pd.concat([frame, duplicate]), SESSION))

    def test_unadjusted_alternate_short_window_only_for_eligible_issuer(self):
        complete = bars().iloc[-80:]
        with patch("market_data._download_chunk", side_effect=[{}, {"DCXINDIA.NS":complete}]) as fetch, \
             patch("research_pdf_fresh_history._recover_official_day") as official:
            self.assertEqual(len(_alternate_history("DCXINDIA", SESSION, 60)), 80)
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(all(c.kwargs["adjusted"] is False for c in fetch.call_args_list))
            official.assert_not_called()

    def test_official_one_day_requires_matching_volume_and_prior_prices(self):
        from datetime import datetime
        from portfolio_doctor_live import IST
        full = bars()
        annual = full.iloc[:-1]
        previous = annual.iloc[-1].to_dict()
        latest = full.iloc[-1].to_dict()
        latest["PreviousClose"] = previous["Close"]
        now = datetime(2026,11,13,18,tzinfo=IST)
        with patch("portfolio_doctor_bhavcopy.fetch_nse_bhavcopy_rows",
                   side_effect=[{"SBIN":latest},{"SBIN":previous}]):
            recovered = _recover_official_day("SBIN", annual, SESSION, 200, now=now)
            self.assertIsNotNone(recovered)
            self.assertEqual(len(recovered), len(full))
            self.assertEqual(float(recovered.iloc[-1]["Close"]), latest["Close"])
        with patch("portfolio_doctor_bhavcopy.fetch_nse_bhavcopy_rows",
                   side_effect=[{"SBIN":latest},{"SBIN":dict(previous, Volume=1)}]):
            self.assertIsNone(_recover_official_day("SBIN", annual, SESSION, 200, now=now))

if __name__ == "__main__":
    unittest.main()
