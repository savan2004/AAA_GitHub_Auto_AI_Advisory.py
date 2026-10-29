"""Conservative list: evidence gates, bearish exclusion, diversity and deadlines.

All bars are synthetic; this suite does not prove current NSE prices or a
historical success rate and never sends advice to a real Telegram user.
"""
import ast
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime
import html
import math
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import conservative_screener as model


def mock_bars(*, direction="up", volume=1_000_000, high_low_spread=2.0,
              end="2026-10-28", extra_today=False):
    i = np.arange(145, dtype=float)
    slope = 0.75 if direction == "up" else -0.75
    price = 530 + slope * i + 2.7 * np.sin(i * 1.3)
    if direction == "overbought":
        price = 530 + 3 * i
    indices = pd.bdate_range(end=end, periods=len(i))
    frame = pd.DataFrame({
        "Close": price,
        "High": price + high_low_spread,
        "Low": price - high_low_spread,
        "Volume": np.repeat(volume, len(price)),
    }, index=indices)
    if extra_today:
        frame.loc[pd.Timestamp("2026-10-29")] = [1_000_000, 1_000_001, 999_999, volume]
    return frame


class ConservativeGradeTests(unittest.TestCase):
    ASOF = date(2026, 10, 29)

    def test_bullish_late_day_completed_candle_qualifies(self):
        result = model.evaluate_conservative("TCS", mock_bars(), self.ASOF)
        self.assertIsNotNone(result)
        self.assertEqual(result["asof"], "2026-10-28")
        self.assertGreater(result["move20_pct"], 0)
        self.assertLessEqual(result["atr_pct"], 3.5)
        self.assertEqual(result["sector"], "IT")

    def test_bearish_is_never_recommended(self):
        self.assertIsNone(model.evaluate_conservative(
            "HDFCBANK", mock_bars(direction="down"), self.ASOF))

    def test_overbought_is_not_conservative(self):
        self.assertIsNone(model.evaluate_conservative(
            "TCS", mock_bars(direction="overbought"), self.ASOF))

    def test_partial_same_day_candle_is_discarded(self):
        normal = model.evaluate_conservative("INFY", mock_bars(), self.ASOF)
        polluted = model.evaluate_conservative(
            "INFY", mock_bars(extra_today=True), self.ASOF)
        self.assertIsNotNone(normal)
        self.assertEqual(normal, polluted)

    def test_insufficient_stale_missing_or_low_liquidity_rejected(self):
        self.assertIsNone(model.evaluate_conservative(
            "TCS", mock_bars().tail(30), self.ASOF))
        self.assertIsNone(model.evaluate_conservative(
            "TCS", mock_bars(end="2026-10-06"), self.ASOF))
        self.assertIsNone(model.evaluate_conservative(
            "TCS", mock_bars(volume=1_000), self.ASOF))
        missing = mock_bars()
        missing.loc[missing.index[-1], "Close"] = math.nan
        self.assertIsNone(model.evaluate_conservative("TCS", missing, self.ASOF))
        self.assertIsNone(model.evaluate_conservative("TCS", None, self.ASOF))

    def test_high_intraday_range_is_rejected(self):
        self.assertIsNone(model.evaluate_conservative(
            "TCS", mock_bars(high_low_spread=55.0), self.ASOF))

    def test_diversified_up_to_ten_no_duplicates_or_fabricated_picks(self):
        sectors = list(model.CONSERVATIVE_SECTORS.values())
        sample_symbols = [symbol for bucket in sectors for symbol in bucket[:2]]
        rows = [model.evaluate_conservative(sym, mock_bars(), self.ASOF)
                for sym in sample_symbols]
        self.assertTrue(all(rows))
        selected = model.pick_diversified(rows, limit=10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(len({r["sym"] for r in selected}), 10)
        counts = {}
        for row in selected:
            counts[row["sector"]] = counts.get(row["sector"], 0) + 1
        self.assertTrue(all(n <= 2 for n in counts.values()))
        self.assertEqual(len(model.pick_diversified(rows[:3], limit=10)), 3)


class TelegramIntegrationTests(unittest.TestCase):
    def test_conservative_button_never_displays_bearish_seed_company(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        ast_tree = ast.parse(source)
        fn = next(n for n in ast_tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "build_scan")
        gate = threading.BoundedSemaphore(1)
        pool = ThreadPoolExecutor(max_workers=3)
        old_tickers = ("HDFCBANK", "TCS", "INFY")
        profiles = {"conservative": old_tickers}
        class FrozenDatetime:
            @staticmethod
            def now(tz):
                return datetime(2026, 10, 29, 9, 32, tzinfo=tz)
        ns = {
            "SCREENER_STOCKS": profiles,
            "_SCAN_GATE": gate, "_SCAN_POOL": pool,
            "_SCAN_TIMEOUT_SECONDS": 0.2,
            "threading": threading, "wait": wait,
            "datetime": FrozenDatetime,
            "timezone": __import__("datetime").timezone,
            "timedelta": __import__("datetime").timedelta,
            "html": html, "date": date,
            "get_hist": lambda sym, period: mock_bars(
                direction="down" if sym == "HDFCBANK" else "up"),
            "evaluate_conservative": model.evaluate_conservative,
            "pick_diversified": model.pick_diversified,
            "logger": Mock(),
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<scan>", "exec"), ns)
        try:
            result = ns["build_scan"]("conservative")
            self.assertIn("BULLISH WATCHLIST", result)
            self.assertIn("TCS", result)
            self.assertIn("INFY", result)
            self.assertNotIn("HDFCBANK", result)
            self.assertIn("2/10 qualified", result)
            self.assertIn("Only 2 passed", result)
            self.assertIn("NOT 10 verified safe investments", result)
            self.assertIn("Session: 2026-10-28", result)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
