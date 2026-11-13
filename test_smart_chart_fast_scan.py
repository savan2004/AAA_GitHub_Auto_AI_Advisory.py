"""Offline regression: chart auto scan never guesses prices or ranks 500 unscanned names."""
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

from smart_chart_fast_scan import choose_auto_chart, ChartScanUnavailable


class FastSmartChartTests(unittest.TestCase):
    SESSION = "2026-11-13"

    @staticmethod
    def history(session="2026-11-13"):
        index = pd.bdate_range(end=session, periods=75)
        close = np.linspace(100, 112, len(index))
        return pd.DataFrame({
            "Open": close - .4,
            "High": close + .8,
            "Low": close - .9,
            "Close": close,
            "Volume": [50000] * len(index),
        }, index=index)

    def test_single_real_batch_and_reproducible_sample(self):
        universe = [(f"STOCK{i}.NS", f"Stock {i}") for i in range(100)]
        stock_data = self.history()
        calls = []

        def bulk_download(*, tickers, **kwargs):
            calls.append((tickers, kwargs))
            return pd.concat({s: stock_data for s in tickers}, axis=1)

        def score(sym, name, *, df):
            self.assertEqual(str(df.index[-1].date()), self.SESSION)
            return {"sym": sym, "name": name, "close": float(df["Close"].iloc[-1]),
                    "score": float(sym[5:-3]), "cross_dir": 1,
                    "reason": "Verified bar"}
        first = choose_auto_chart(universe, download=bulk_download,
                                  scorer=score, session_key=self.SESSION,
                                  sample_limit=60)
        second = choose_auto_chart(universe, download=bulk_download,
                                   scorer=score, session_key=self.SESSION,
                                   sample_limit=60)
        self.assertEqual(first[0]["sym"], second[0]["sym"])
        self.assertEqual(first[1:], (60, 60, 100))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0][0]), 60)
        self.assertEqual(calls[0][1]["timeout"], 12)
        self.assertEqual(calls[0][1]["period"], "1y")
        self.assertFalse(first[0]["_daily_history"].empty)
        self.assertEqual(str(first[0]["_daily_history"].index[-1].date()), self.SESSION)
        self.assertTrue(calls[0][1]["threads"])

    def test_stale_or_missing_quote_fails_closed(self):
        def scorer(sym, name, *, df):
            return {"sym": sym, "close": 150., "score": 9, "cross_dir": 1}
        universe = [("SBIN.NS", "SBI"), ("TCS.NS", "TCS")]
        stale = pd.concat({s: self.history("2026-11-12") for s,_ in universe},
                          axis=1)
        with self.assertRaises(ChartScanUnavailable):
            choose_auto_chart(universe, download=lambda **kw: stale,
                              scorer=scorer, session_key=self.SESSION)
        with self.assertRaises(ChartScanUnavailable):
            choose_auto_chart(universe, download=lambda **kw: pd.DataFrame(),
                              scorer=scorer, session_key=self.SESSION)

    def test_bad_symbol_is_excluded_no_fake_candidate(self):
        good = self.history()
        data = pd.concat({"SBIN.NS": good}, axis=1)
        universe = [("SBIN.NS", "SBI"), ("FAKE.NS", "Fake")]
        def scorer(sym, name, *, df):
            return {"sym": sym, "close": float(df["Close"].iloc[-1]),
                    "score": 3., "cross_dir": 0, "reason": "Real"}
        winner, sampled, valid, total = choose_auto_chart(
            universe, download=lambda **kw: data, scorer=scorer,
            session_key=self.SESSION)
        self.assertEqual(winner["sym"], "SBIN.NS")
        self.assertEqual((sampled, valid, total), (2, 1, 2))


    def test_all_nan_yahoo_ticker_is_skipped(self):
        """Real provider returned all-NaN rows, previously crashing at index[-1]."""
        good = self.history()
        empty = good.copy()
        empty.loc[:, :] = np.nan
        data = pd.concat({"SBIN.NS": good, "FAKE.NS": empty}, axis=1)
        universe = [("SBIN.NS", "SBI"), ("FAKE.NS", "Fake")]

        def scorer(sym, name, *, df):
            self.assertFalse(df.empty)
            return {"sym": sym, "close": float(df["Close"].iloc[-1]),
                    "score": 2.0, "cross_dir": 0, "reason": "Fresh"}

        chosen, sampled, valid, universe_count = choose_auto_chart(
            universe, download=lambda **kw: data,
            scorer=scorer, session_key=self.SESSION)
        self.assertEqual(chosen["sym"], "SBIN.NS")
        self.assertEqual((sampled, valid, universe_count), (2, 1, 2))


if __name__ == "__main__":
    unittest.main()
