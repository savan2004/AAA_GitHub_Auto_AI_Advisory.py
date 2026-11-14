"""Run real Stocks Buzz session/coverage functions without external providers."""
import ast
import os
import sys
import types
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Callable
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

SOURCE = Path(__file__).resolve().parents[1].joinpath("stocks_buzz.py").read_text(encoding="utf-8")
IST = timezone(timedelta(hours=5, minutes=30))


def load_functions(*names, globals_=None):
    tree = ast.parse(SOURCE)
    found = {n.name: n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    if set(names) != set(found):
        raise AssertionError("Stocks Buzz source function missing")
    ns = {"pd": pd, "np": np, "datetime": datetime, "time": time,
          "IST": IST, "MIN_SCAN_COVERAGE": .8, "MIN_BARS": 130,
          "MIN_UNIVERSE_FOR_TOP1000": 5, "os": os,
          "Optional": Optional, "Callable": Callable}
    ns.update(globals_ or {})
    exec(compile(ast.Module(body=[found[name] for name in names],
                            type_ignores=[]), "stocks_buzz.py", "exec"), ns)
    return ns


def series(day="2026-11-13"):
    periods = pd.bdate_range(end=day, periods=160)
    close = np.linspace(100., 160., len(periods))
    return pd.DataFrame({"Close": close, "Open": close - .5,
                         "High": close + 1, "Low": close - 1,
                         "Volume": np.full(len(periods), 1000000)}, index=periods)


class SessionSafety(unittest.TestCase):
    def test_weekend_and_market_open_do_not_use_unfinished_candle(self):
        ns = load_functions("_last_completed_session")
        self.assertEqual(ns["_last_completed_session"](datetime(2026, 11, 14, 11, tzinfo=IST)),
                         date(2026, 11, 13))
        self.assertEqual(ns["_last_completed_session"](datetime(2026, 11, 16, 11, tzinfo=IST)),
                         date(2026, 11, 13))
        self.assertEqual(ns["_last_completed_session"](datetime(2026, 11, 16, 16, tzinfo=IST)),
                         date(2026, 11, 16))

    def test_last_completed_frame_drops_partial_and_rejects_stale(self):
        ns = load_functions("_completed_session_frame")
        frame = series()
        intraday = pd.DataFrame({"Close": [200.], "High": [201.]},
                                index=pd.to_datetime(["2026-11-16"]))
        mixed = pd.concat([frame, intraday])
        self.assertEqual(len(ns["_completed_session_frame"](mixed, date(2026, 11, 13))),
                         len(frame))
        self.assertIsNone(ns["_completed_session_frame"](frame, date(2026, 11, 16)))

    def test_cache_remains_fast_same_completed_session_only(self):
        lock = threading.Lock()
        state = {"result": {"data_session": date(2026, 11, 13), "coverage": 1.0},
                 "ts": 1.0}
        ns = load_functions("get_cached", globals_={
            "_state_lock": lock, "_state": state,
            "_last_completed_session": lambda: date(2026, 11, 13)})
        self.assertIs(ns["get_cached"](), state["result"])
        ns["_last_completed_session"] = lambda: date(2026, 11, 16)
        self.assertIsNone(ns["get_cached"]())

    def test_ranking_refuses_partial_sample_before_analyzing(self):
        universe = {"symbols": ["A", "B", "C", "D", "E"], "source": "Official"}
        good = series()
        hist = {"A": good, "B": good, "C": good}
        analyze = Mock(side_effect=AssertionError("no ranking before coverage check"))
        ns = load_functions("_completed_session_frame", "run_scan", globals_={
            "get_universe": lambda: universe,
            "bulk_history": lambda *_a, **_k: hist,
            "index_history": lambda *_a: good,
            "_last_completed_session": lambda: date(2026, 11, 13),
            "analyze_stock": analyze, "logger": Mock(), "market_regime": Mock(),
            "_state": {"result": None, "ts": 0.}, "_state_lock": threading.Lock(),
        })
        with self.assertRaisesRegex(ValueError, "ranking withheld"):
            ns["run_scan"]()
        analyze.assert_not_called()

    def test_after_close_secondary_recovers_only_verified_symbol_history(self):
        universe = {"symbols": ["A", "B", "C", "D", "E"], "source": "Official"}
        good = series()
        primary = {"A": good, "B": good, "C": good}
        secondary = types.SimpleNamespace(
            fetch_tej_history=lambda sym, session: good if sym == "D" else None)
        ns = load_functions("_completed_session_frame", "run_scan", globals_={
            "get_universe": lambda: universe,
            "bulk_history": lambda *_a, **_k: primary,
            "index_history": lambda *_a: good,
            "_last_completed_session": lambda: date(2026, 11, 13),
            "analyze_stock": lambda *_a, **_kw: None,
            "market_regime": lambda *_a: {"text": "Nifty sample"},
            "logger": Mock(), "_state": {"result": None, "ts": 0.},
            "_state_lock": threading.Lock(),
        })
        with patch.dict(os.environ, {
            "MSK_BUZZ_ALLOW_SECONDARY_EOD": "true",
            "MSK_INDEX_DATABASE_URL": ""}), \
             patch.dict(sys.modules, {"overnight_index_sources": secondary}):
            result = ns["run_scan"]()
        self.assertEqual(result["downloaded"], 4)
        self.assertEqual(result["secondary_count"], 1)
        self.assertEqual(result["coverage"], .8)
        self.assertIn("verified raw NSE EOD", result["price_sources"])



if __name__ == "__main__":
    unittest.main()
