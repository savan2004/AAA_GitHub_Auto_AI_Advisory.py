"""Offline regression for Analysis and two bullish investment profiles.

Synthetic historical fixtures are NOT proof of actual NSE trading accuracy.
"""
import ast
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
import threading
import investment_scan_runner as runner
import numpy as np
import pandas as pd
import analysis_quality as aq
import investment_screener as inv

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=IST)


def make_bars(up=True, last="2026-10-28", count=170, volume=1500000):
    x = np.arange(count, dtype=float)
    slope = 0.31 if up else -0.31
    close = 130 + slope * x + 2.2 * np.sin(x * 0.72)
    dates = pd.bdate_range(end=last, periods=count)
    return pd.DataFrame({
        "Close": close, "High": close + 1.5, "Low": close - 1.5,
        "Volume": np.repeat(volume, count),
    }, index=dates)


class AnalysisHistoryTests(unittest.TestCase):
    def test_healthy_1yr_snapshot_with_correct_52_week_bounds(self):
        s = aq.inspect_history(make_bars(count=260), now=NOW)
        self.assertIsNotNone(s)
        self.assertEqual(s["asof"], "2026-10-28")
        self.assertIsNotNone(s["high52"])
        self.assertIn(s["trend"], ("BULLISH", "NEUTRAL", "BEARISH"))
        body = aq.format_snapshot("SBIN", "Bank <Tester>", s)
        self.assertIn("&lt;Tester&gt;", body)
        self.assertNotIn("<Tester>", body)
        self.assertIn("Technical", body)

    def test_six_months_not_labeled_full_year(self):
        s = aq.inspect_history(make_bars(count=125), now=NOW)
        self.assertIsNotNone(s)
        self.assertIsNone(s["high52"])
        self.assertIn("insufficient full-year", aq.format_snapshot("SBIN", "Bank", s))

    def test_fail_closed_for_stale_short_or_corrupted_data(self):
        self.assertIsNone(aq.inspect_history(make_bars(last="2026-10-01"), now=NOW))
        self.assertIsNone(aq.inspect_history(make_bars(count=30), now=NOW))
        bad = make_bars()
        bad.iloc[-1, bad.columns.get_loc("High")] = 0
        self.assertIsNone(aq.inspect_history(bad, now=NOW))

    def test_forming_intraday_bar_ignored(self):
        bars = make_bars(count=260)
        contaminated = bars.copy()
        contaminated.loc[pd.Timestamp("2026-10-29")] = [9999, 10000, 9990, 500]
        good = aq.inspect_history(bars, now=NOW)
        dirty = aq.inspect_history(contaminated, now=NOW)
        self.assertEqual(good, dirty)


class ShortlistTests(unittest.TestCase):
    def test_bearish_cannot_be_recommended_in_either_profile(self):
        frame = make_bars(up=False)
        self.assertIsNone(inv.evaluate_candidate("HDFCBANK", frame, "moderate", NOW))
        self.assertIsNone(inv.evaluate_candidate("TMPV", frame, "aggressive", NOW))

    def test_bullish_complete_history_produces_trade_plan(self):
        frame = make_bars()
        for profile in ("moderate", "aggressive"):
            result = inv.evaluate_candidate("TCS", frame, profile, NOW)
            self.assertIsNotNone(result, f"{profile} synthetic healthy trend should pass")
            self.assertLess(result["sl"], result["entry"])
            self.assertGreater(result["t1"], result["entry"])
            self.assertGreater(result["t2"], result["t1"])
            self.assertEqual(result["asof"], "2026-10-28")

    def test_no_stale_low_turnover_invalid_or_incomplete_picks(self):
        for profile in ("moderate", "aggressive"):
            self.assertIsNone(inv.evaluate_candidate("TCS", make_bars(last="2026-10-01"), profile, NOW))
            self.assertIsNone(inv.evaluate_candidate("TCS", make_bars(count=45), profile, NOW))
            self.assertIsNone(inv.evaluate_candidate("TCS", make_bars(volume=1000), profile, NOW))
            bad = make_bars()
            bad.iloc[-1, bad.columns.get_loc("Close")] = float("nan")
            self.assertIsNone(inv.evaluate_candidate("TCS", bad, profile, NOW))

    def test_only_qualified_picks_and_cash_preserved(self):
        fake = [{"sym": "TCS", "score": 75, "volx": 1.4, "ltp": 185.5,
                 "chg": 1.1, "rsi": 62, "adx": 24, "entry": 185.5,
                 "sl": 177, "t1": 200, "t2": 214, "rr1": 1.6,
                 "asof": "2026-10-28"}]
        for profile in ("moderate", "aggressive"):
            text = inv.format_shortlist(profile, fake, 5, 20, unfinished=15)
            self.assertIn("TCS", text)
            self.assertIn("Partial coverage", text)
            self.assertIn("Unallocated cash", text)
            self.assertIn("SL ₹177", text)
            self.assertIn("No forced recommendations", text)
        empty = inv.format_shortlist("moderate", [], 20, 20)
        self.assertIn("NO BUY SIGNAL", empty)


class BulkInvestmentScanTests(unittest.TestCase):
    def test_one_bulk_call_replaces_twenty_throttled_stock_calls(self):
        symbols = tuple(f"STOCK{i}" for i in range(20))
        history = {"STOCK0": make_bars()}
        class FakeClock:
            @staticmethod
            def now(tz):
                return NOW
        with patch("market_data.bulk_history", return_value=history) as download:
            with patch.object(runner, "datetime", FakeClock):
                output = runner._bulk_evaluate("aggressive", symbols)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[:2], (symbols, "6mo"))
        self.assertEqual(download.call_args.kwargs["retries"], 1)
        self.assertIn("data returned: 1/20", output)
        self.assertIn("Partial coverage:", output)
        self.assertIn("NOT a full-market zero", output)

    def test_full_coverage_can_report_no_qualified_stocks(self):
        bearish = make_bars(up=False)
        symbols = tuple(f"STOCK{i}" for i in range(20))
        histories = {sym: bearish for sym in symbols}
        class FakeClock:
            @staticmethod
            def now(tz):
                return NOW
        with patch("market_data.bulk_history", return_value=histories):
            with patch.object(runner, "datetime", FakeClock):
                output = runner._bulk_evaluate("moderate", symbols)
        self.assertIn("data returned: 20/20", output)
        self.assertIn("NO BUY SIGNAL", output)
        self.assertNotIn("Partial coverage:", output)

    def test_slow_download_reports_pending_not_zero_bullish(self):
        gate = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            def slow_scan(profile, symbols):
                gate.wait(2)
                return "synthetic result"
            with patch.object(runner, "_POOL", pool), patch.object(
                    runner, "_ACTIVE", None), patch.object(
                    runner, "_ACTIVE_PROFILE", None), patch.object(
                    runner, "_bulk_evaluate", slow_scan):
                output = runner.run_investment_scan("aggressive", ["TCS"], deadline=0.01)
                self.assertIn("still downloading", output)
                self.assertNotIn("0/10", output)
                self.assertNotIn("NO BUY SIGNAL", output)
                gate.set()
                result = runner.run_investment_scan("aggressive", ["TCS"], deadline=2)
                self.assertEqual(result, "synthetic result")

    def test_unsupported_profile_rejected_without_download(self):
        with patch.object(runner, "_POOL") as worker:
            self.assertIn("Unsupported", runner.run_investment_scan("bogus", ["TCS"]))
            worker.submit.assert_not_called()


class HandlerIntegrationTests(unittest.TestCase):
    def test_analysis_requires_verified_history_before_creating_actions(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fun = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "send_symbol_menu")
        sent = []
        ns = {
            "resolve_symbol": lambda q: ("FAKE.NS", "Fake Co"),
            "get_hist": lambda s, p: pd.DataFrame(),
            "inspect_history": aq.inspect_history,
            "format_snapshot": aq.format_snapshot,
            "main_keyboard": lambda: "MENU",
            "analysis_choice_keyboard": lambda s: "CALLBACKS",
            "safe_send": lambda *args, **kwargs: sent.append((args, kwargs)),
            "html": __import__("html"),
            "logger": logging.getLogger(__name__),
        }
        exec(compile(ast.Module(body=[fun], type_ignores=[]), "<handler>", "exec"), ns)
        ns["send_symbol_menu"](1, "FAKE")
        self.assertEqual(sent[-1][1]["reply_markup"], "MENU")
        self.assertNotIn("CALLBACKS", str(sent[-1]))
        self.assertNotIn("✅ Found", str(sent[-1]))
        ns["get_hist"] = lambda s, p: make_bars()
        ns["send_symbol_menu"](1, "FAKE")
        self.assertEqual(sent[-1][1]["reply_markup"], "CALLBACKS")
        self.assertIn("completed session", sent[-1][0][1])


if __name__ == "__main__":
    unittest.main()
