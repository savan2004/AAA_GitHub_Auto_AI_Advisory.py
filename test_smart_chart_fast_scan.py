"""Offline regression: chart auto scan never guesses prices or ranks 500 unscanned names."""
import unittest
from pathlib import Path
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


    def test_full_universe_ranks_only_confirmed_twelve_plus_final_scores(self):
        data = self.history()
        universe = [(f"STOCK{i}.NS", f"Stock {i}") for i in range(85)]
        batches = []
        def fetch(*, tickers, **kwargs):
            batches.append(list(tickers))
            return pd.concat({sym: data for sym in tickers}, axis=1)
        def full_score(sym, name, *, df):
            number = int(sym.removeprefix("STOCK").removesuffix(".NS"))
            # 19/20 but UNCONFIRMED cannot beat a confirmed 13/20.
            score = 19 if number == 3 else (13 if number == 70 else 7)
            confirmed = number == 70
            return {"sym": sym, "score": score, "setup_confirmed": confirmed,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 1}
        winner, attempted, valid, whole = choose_auto_chart(
            universe, download=fetch, scorer=full_score,
            session_key=self.SESSION, sample_limit=60,
            min_score=12, require_confirmed_setup=True, scan_all=True)
        self.assertEqual(winner["sym"], "STOCK70.NS")
        self.assertEqual(winner["score"], 13)
        self.assertEqual((attempted, valid, whole), (85, 1, 85))
        self.assertEqual([len(x) for x in batches], [40, 40, 5])

    def test_individual_recovery_only_shortlisted_stock_then_rescores(self):
        universe = [("SBIN.NS","SBI"),("ASIANPAINT.NS","Asian Paints"),("TCS.NS","TCS")]
        fresh = self.history()
        stale = fresh.copy()
        stale.iloc[-1] = np.nan
        calls = []
        def download(**kw):
            return pd.concat({s:stale for s in kw["tickers"]},axis=1)
        # Higher preliminary rank for Asian Paints from the synthetic history.
        stale_by_symbol = {s:stale.copy() for s,_ in universe}
        stale_by_symbol["ASIANPAINT.NS"].iloc[-2,3] = 500.
        def bulk(**kw):
            return pd.concat({s:stale_by_symbol[s] for s in kw["tickers"]},axis=1)
        def individual(sym):
            calls.append(sym)
            return fresh
        def score(sym,name,*,df):
            self.assertEqual(str(df.index[-1].date()),self.SESSION)
            return {"sym":sym,"close":float(df.Close.iloc[-1]),"score":13,"setup_confirmed":True}
        result = choose_auto_chart(universe,download=bulk,scorer=score,session_key=self.SESSION,
            scan_all=True,min_score=12,require_confirmed_setup=True,individual_history=individual,
            shortlist_rank=lambda frame,session:float(frame.Close.iloc[-2]))
        self.assertEqual(calls,["ASIANPAINT.NS"])
        self.assertEqual(result[0]["sym"],"ASIANPAINT.NS")
        self.assertEqual(result[0]["_verified_count"],1)
        # A shortlist cannot be emitted if the individual history is still stale.
        with self.assertRaises(ChartScanUnavailable):
            choose_auto_chart(universe,download=download,scorer=score,session_key=self.SESSION,
                scan_all=True,min_score=12,individual_history=lambda sym:stale,
                shortlist_rank=lambda frame,session:10.)

    def test_full_scan_missing_completed_bars_uses_bounded_dated_retry(self):
        good = self.history()
        stale = good.copy()
        stale.iloc[-1] = np.nan
        universe = [(f"STOCK{i}.NS", f"Stock {i}") for i in range(85)]
        calls = []
        def fetch(*, tickers, **kwargs):
            calls.append((list(tickers), kwargs))
            data = good if "start" in kwargs else stale
            return pd.concat({sym: data for sym in tickers}, axis=1)
        def score(sym, name, *, df):
            self.assertEqual(str(df.index[-1].date()), self.SESSION)
            return {"sym": sym, "score": 13, "close": float(df["Close"].iloc[-1]),
                    "setup_confirmed": True}
        winner, attempted, valid, whole = choose_auto_chart(
            universe, download=fetch, scorer=score, session_key=self.SESSION,
            min_score=12, require_confirmed_setup=True, scan_all=True)
        self.assertEqual((attempted, valid, whole), (85, 3, 85))
        self.assertEqual([len(c[0]) for c in calls], [40, 40, 5, 3])
        self.assertEqual(calls[-1][1]["end"], "2026-11-14")
        self.assertFalse(calls[-1][1]["threads"])
        self.assertEqual(calls[-1][1]["timeout"], 6)

    def test_full_scan_still_missing_after_retry_fails_closed(self):
        stale = self.history("2026-11-12")
        calls = []
        def fetch(*, tickers, **kwargs):
            calls.append(list(tickers))
            return pd.concat({sym: stale for sym in tickers}, axis=1)
        def score(*args, **kwargs):
            self.fail("Stale history must never reach scorer")
        with self.assertRaisesRegex(ChartScanUnavailable, "No verified current-session histories"):
            choose_auto_chart(
                [("SBIN.NS", "SBI"), ("TCS.NS", "TCS")],
                download=fetch, scorer=score, session_key=self.SESSION,
                min_score=12, require_confirmed_setup=True, scan_all=True)
        self.assertEqual(len(calls), 2)  # initial and one bounded retry

    def test_no_confirmed_twelve_plus_setup_returns_unavailable(self):
        good = self.history()
        def weak(sym, name, *, df):
            return {"sym": sym, "score": 11, "setup_confirmed": True,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 1}
        with self.assertRaisesRegex(ChartScanUnavailable, "12\\+/20"):
            choose_auto_chart(
                [("SBIN.NS", "SBI"), ("TCS.NS", "TCS")],
                download=lambda **kw: pd.concat(
                    {s: good for s in kw["tickers"]}, axis=1),
                scorer=weak, session_key=self.SESSION,
                min_score=12, require_confirmed_setup=True, scan_all=True)

    def test_rendered_chart_readability_layout_guards(self):
        source = (Path(__file__).resolve().parent /
                  "gen_smart_stock_chart.py").read_text(encoding="utf-8")
        self.assertIn('color=price_col, va="center", zorder=8)', source)
        self.assertIn('header.text(0.17, 0.58, f"₹{last_close:,.2f}"', source)
        self.assertIn('sa.text(0.5, 0.968, "AUTOAI ADVISORY  v8.0"', source)
        self.assertIn('"WAIT — No Setup"', source)
        self.assertIn('"NO TRADE ZONE"', source)
        self.assertNotIn('"⏸  NO TRADE ZONE"', source)
        self.assertNotIn('"52-WEEK RANGE"', source)
        self.assertNotIn('fig.text(0.99, 0.005', source)


    def test_retry_smaller_serial_batch_recovers_current_completed_bar(self):
        good = self.history()
        calls = []
        def download(*, tickers, **kwargs):
            calls.append((list(tickers), kwargs))
            if len(calls) == 1:
                return pd.DataFrame()  # full-batch rate limit, no usable rows
            self.assertFalse(kwargs["threads"])
            self.assertEqual(kwargs["timeout"], 9)
            return pd.concat({s: good for s in tickers}, axis=1)

        def score(sym, name, *, df):
            return {"sym": sym, "score": 2.0,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 0}
        got, sampled, valid, whole = choose_auto_chart(
            [(f"SYM{i}.NS", f"Name {i}") for i in range(60)],
            download=download, scorer=score, session_key=self.SESSION)
        self.assertEqual((sampled, valid, whole), (60, 10, 60))
        self.assertEqual(len(calls), 2)
        self.assertIn(got["sym"], calls[-1][0])
        self.assertEqual(str(got["_daily_history"].index[-1].date()), self.SESSION)

    def test_both_yahoo_requests_fail_official_history_recovers_no_made_up_data(self):
        good = self.history()
        fallback_calls = []
        def official(sym):
            fallback_calls.append(sym)
            return good
        def score(sym, name, *, df):
            return {"sym": sym, "score": 1.0,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 0}
        got, sampled, valid, whole = choose_auto_chart(
            [("SBIN.NS", "SBI"), ("INFY.NS", "Infosys")],
            download=lambda **kw: pd.DataFrame(), scorer=score,
            session_key=self.SESSION, fallback_history=official)
        self.assertIn(got["sym"], fallback_calls)
        self.assertEqual((sampled, valid, whole), (2, 2, 2))
        self.assertEqual(len(fallback_calls), 2)
        self.assertEqual(str(got["_daily_history"].index[-1].date()), self.SESSION)

    def test_missing_or_stale_secondary_data_never_creates_chart(self):
        attempted = []
        def stale(sym):
            attempted.append(sym)
            return self.history("2026-11-12")
        def score(sym, name, *, df):
            return {"sym": sym, "score": 1,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 0}
        with self.assertRaisesRegex(ChartScanUnavailable, "session=2026-11-13"):
            choose_auto_chart(
                [("SBIN.NS", "SBI"), ("TCS.NS", "TCS")],
                download=lambda **kw: pd.DataFrame(), scorer=score,
                session_key=self.SESSION, fallback_history=stale)
        self.assertEqual(len(attempted), 2)

    def test_reversed_multiindex_and_incomplete_today_bar(self):
        good = self.history()
        later = good.iloc[-1:].copy()
        later.index = pd.to_datetime(["2026-11-14"])
        later["Close"] = 999.0
        data = pd.concat([good, later])
        reversed_columns = pd.concat({"SBIN.NS": data}, axis=1)
        reversed_columns = reversed_columns.swaplevel(0, 1, axis=1)
        def score(sym, name, *, df):
            self.assertEqual(str(df.index[-1].date()), self.SESSION)
            self.assertEqual(float(df["Close"].iloc[-1]), float(good["Close"].iloc[-1]))
            return {"sym": sym, "score": 1.0,
                    "close": float(df["Close"].iloc[-1]), "cross_dir": 0}
        result = choose_auto_chart(
            [("SBIN.NS", "SBI")],
            download=lambda **kw: reversed_columns,
            scorer=score, session_key=self.SESSION)
        self.assertEqual(result[2], 1)

    def test_invalid_candles_and_price_mismatch_cannot_pass(self):
        bad = self.history()
        bad["Low"] = bad["High"] + 50
        def score(sym, name, *, df):
            return {"sym": sym, "score": 5,
                    "close": float(df["Close"].iloc[-1]) + 1,
                    "cross_dir": 1}
        with self.assertRaises(ChartScanUnavailable):
            choose_auto_chart(
                [("SBIN.NS", "SBI")], download=lambda **kw: bad,
                scorer=score, session_key=self.SESSION)
        with self.assertRaises(ChartScanUnavailable):
            choose_auto_chart(
                [("SBIN.NS", "SBI")], download=lambda **kw: self.history(),
                scorer=score, session_key=self.SESSION)


if __name__ == "__main__":
    unittest.main()
