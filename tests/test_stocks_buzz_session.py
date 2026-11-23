"""Run real Stocks Buzz session/coverage functions without external providers."""
import ast
import os
import math
import html
import sys
import types
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Callable, List
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
          "Optional": Optional, "Callable": Callable, "List": List}
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

    def test_three_stock_trade_ready_gate_fixture(self):
        """Synthetic candidates: clean, 12% stop, RSI 85 — NEVER market calls."""
        ns = load_functions("_safe_float", "_trade_ready_block_reasons",
                            "_partition_breakouts", globals_={
            "math": math, "MAX_TRADE_RISK_PCT": 8.,
            "MAX_EXTENSION_PCT": 10., "MIN_TRADED_VALUE": 2e7,
        })
        clean = dict(sym="CLEAN", kind="BREAKOUT", risk_pct=6.,
                     rsi=63., ext_pct=5., vol_ratio=3., tv_cr=10.)
        high_risk = dict(clean, sym="HIGH_RISK", risk_pct=12.)
        hot_rsi = dict(clean, sym="HOT_RSI", rsi=85.)
        confirmed, ready, extended = ns["_partition_breakouts"]({
            "breakouts": [clean, high_risk, hot_rsi],
        })
        self.assertEqual([r["sym"] for r in confirmed],
                         ["CLEAN", "HIGH_RISK", "HOT_RSI"])
        self.assertEqual([r["sym"] for r in ready], ["CLEAN"])
        self.assertEqual(extended, [])
        self.assertIn("stop loss 12.0% > 8% limit",
                      ns["_trade_ready_block_reasons"](high_risk))
        self.assertIn("RSI 85 outside 50–75",
                      ns["_trade_ready_block_reasons"](hot_rsi))

    def test_old_snapshot_extension_cannot_appear_in_top_breakouts(self):
        ns = load_functions("_safe_float", "_trade_ready_block_reasons",
                            "_partition_breakouts", globals_={
            "math": math, "MAX_TRADE_RISK_PCT": 8.,
            "MAX_EXTENSION_PCT": 10., "MIN_TRADED_VALUE": 2e7,
        })
        clean = dict(sym="CLEAN", risk_pct=5., rsi=60.,
                     ext_pct=2., vol_ratio=3., tv_cr=10.)
        stretched = dict(clean, sym="EXTENDED", ext_pct=10.1)
        confirmed, ready, extended = ns["_partition_breakouts"]({
            "breakouts": [clean, stretched]})
        self.assertEqual([r["sym"] for r in confirmed], ["CLEAN"])
        self.assertEqual([r["sym"] for r in ready], ["CLEAN"])
        self.assertEqual([r["sym"] for r in extended], ["EXTENDED"])

    def test_telegram_header_and_risk_sections_are_rendered(self):
        """Real formatter using synthetic fixture; owner receipt recognizes final text."""
        names = ("_e", "_stars", "_safe_float", "_trade_ready_block_reasons",
                 "_partition_breakouts", "_buzz_data_status", "_breakout_card",
                 "_extended_card", "_watch_card", "format_buzz")
        ns = load_functions(*names, globals_={
            "math": math, "html": html, "MIN_TRADED_VALUE": 2e7,
            "MAX_TRADE_RISK_PCT": 8., "MAX_EXTENSION_PCT": 10.,
            "_last_completed_session": lambda now=None: date(2026, 11, 13),
        })
        row = dict(sym="CLEAN", kind="BREAKOUT", ltp=100., chg=2.,
                   score=84., rsi=63., ext_pct=5., risk_pct=6.,
                   vol_ratio=3., tv_cr=10., atr_pct=3., reasons=["breakout"],
                   flags=[], entry=100., sl=94., t1=109., t2=118.)
        scan = {
            "ts": datetime(2026, 11, 14, 12, tzinfo=IST),
            "data_session": date(2026, 11, 13), "coverage": .918,
            "downloaded": 918, "universe_size": 1000, "liquid": 882,
            "breakouts": [row], "watch": [], "extended": 0,
            "regime": {"text": "Synthetic market regime"},
            "source": "Synthetic test only",
        }
        fresh = ns["format_buzz"](scan)
        self.assertIn("STOCKS BUZZ — Breakout Radar", fresh[0])
        self.assertIn("Scanned", fresh[0])
        self.assertIn("confirmed breakouts", fresh[0])
        self.assertIn("DATA STATUS: <b>FRESH</b>", fresh[0])
        self.assertIn("CONFIRMED BREAKOUTS", "\\n".join(fresh))
        self.assertIn("TRADE-READY BREAKOUTS", "\\n".join(fresh))
        self.assertIn("Reference Entry", "\\n".join(fresh))
        # Legacy snapshot: 2 previously excluded plus 1 newly >10% extended.
        old_scan = dict(scan, breakouts=[dict(row, ext_pct=11., sym="OVER")],
                        extended=2)
        self.assertIn("3 extended", ns["format_buzz"](old_scan)[0])
        # One missed completed NSE session must suppress old trade-ready
        # prices and targets; informational research remains visible.
        ns["_last_completed_session"] = lambda now=None: date(2026, 11, 16)
        previous = ns["format_buzz"](scan)
        previous_text = "\\n".join(previous)
        self.assertIn("DATA STATUS: <b>STALE</b>", previous[0])
        self.assertIn("No trade-ready calls on stale data", previous_text)
        self.assertNotIn("Reference Entry", previous_text)
        self.assertNotIn("T1 ₹", previous_text)
        ns["_last_completed_session"] = lambda now=None: date(2026, 11, 18)
        stale = ns["format_buzz"](scan)
        self.assertIn("DATA STATUS: <b>STALE</b>", stale[0])
        self.assertIn("No trade-ready calls on stale data", "\\n".join(stale))

    def test_freshness_uses_completed_trading_days_not_weekend_days(self):
        ns = load_functions("_buzz_data_status", globals_={
            "_last_completed_session": lambda now=None: date(2026, 11, 16)})
        self.assertEqual(ns["_buzz_data_status"]({
            "data_session": date(2026, 11, 16)}), ("FRESH", 0))
        # Friday's report is stale after Monday's completed close.
        self.assertEqual(ns["_buzz_data_status"]({
            "data_session": date(2026, 11, 13)}), ("STALE", 1))
        self.assertEqual(ns["_buzz_data_status"]({
            "data_session": date(2026, 11, 12)}), ("STALE", 2))
        # Weekend before the Monday close still belongs to Friday's session.
        ns["_last_completed_session"] = lambda now=None: date(2026, 11, 13)
        self.assertEqual(ns["_buzz_data_status"]({
            "data_session": date(2026, 11, 13)}), ("FRESH", 0))

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



    def test_stock_buzz_streams_250_symbols_with_full_coverage_and_stable_ranking(self):
        """All 250 synthetic stocks count; no provider batch holds the full universe."""
        symbols = [f"TEST{i:03d}" for i in range(250)]
        frame = series()
        batches, progress, reviewed = [], [], []
        saved = Mock(return_value=False)

        def fake_bulk(part, period, *, chunk_size, progress_cb):
            self.assertEqual(period, "1y")
            self.assertEqual(chunk_size, 100)
            self.assertLessEqual(len(part), 100)
            batches.append(tuple(part))
            if progress_cb:
                progress_cb(len(part), len(part))
            return {sym: frame for sym in part}

        def fake_analysis(sym, _df, _nifty, _delivery):
            # Must not analyze until the entire universe passed the gate.
            self.assertEqual(len(batches), 3)
            reviewed.append(sym)
            i = int(sym[-3:])
            if i % 25 == 0:
                return {"kind": "BREAKOUT", "sym": sym,
                        "score": float(100 - i // 25),
                        "vol_ratio": 2.0}
            return {"kind": "NONE", "sym": sym}

        state = {"result": None, "ts": 0.0}
        ns = load_functions("_completed_session_frame", "run_scan", globals_={
            "get_universe": lambda: {"symbols": symbols, "source": "Synthetic"},
            "bulk_history": fake_bulk,
            "index_history": lambda *_args: frame,
            "_last_completed_session": lambda: date(2026, 11, 13),
            "analyze_stock": fake_analysis,
            "market_regime": lambda *_args: {"name": "Nifty"},
            "logger": Mock(), "_state": state,
            "_state_lock": threading.Lock(),
        })
        snapshot = types.SimpleNamespace(save_verified_scan=saved)
        with patch.dict(os.environ, {"MSK_BUZZ_ALLOW_SECONDARY_EOD": "false"}), \
             patch.dict(sys.modules, {"stocks_buzz_snapshot": snapshot}):
            result = ns["run_scan"](
                progress_cb=lambda done, total: progress.append((done, total)))

        self.assertEqual([len(b) for b in batches], [100, 100, 50])
        self.assertEqual(progress, [(100, 250), (200, 250), (250, 250)])
        self.assertEqual(reviewed, symbols)
        self.assertEqual(result["downloaded"], 250)
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["liquid"], 250)
        self.assertEqual(len(result["breakouts"]), 10)
        self.assertEqual(result["breakouts"][0]["sym"], "TEST000")
        self.assertIs(state["result"], result)
        saved.assert_called_once_with(result)

    def test_streaming_never_scores_or_publishes_if_full_coverage_fails(self):
        """Fail closed even when several 100-symbol provider batches returned data."""
        symbols = [f"TEST{i:03d}" for i in range(205)]
        frame = series()
        batches = []
        analyze = Mock(side_effect=AssertionError("partial universe must not rank"))
        save = Mock(side_effect=AssertionError("partial universe must not save"))

        def partial_bulk(part, _period, *, chunk_size, progress_cb):
            batches.append(len(part))
            returned = {sym: frame for sym in part[:int(len(part) * .6)]}
            if progress_cb:
                progress_cb(len(returned), len(part))
            return returned

        state = {"result": None, "ts": 0.0}
        ns = load_functions("_completed_session_frame", "run_scan", globals_={
            "get_universe": lambda: {"symbols": symbols, "source": "Synthetic"},
            "bulk_history": partial_bulk,
            "index_history": lambda *_args: frame,
            "_last_completed_session": lambda: date(2026, 11, 13),
            "analyze_stock": analyze, "market_regime": Mock(),
            "logger": Mock(), "_state": state,
            "_state_lock": threading.Lock(),
        })
        snapshot = types.SimpleNamespace(save_verified_scan=save)
        with patch.dict(os.environ, {"MSK_BUZZ_ALLOW_SECONDARY_EOD": "false"}), \
             patch.dict(sys.modules, {"stocks_buzz_snapshot": snapshot}):
            with self.assertRaisesRegex(ValueError, "ranking withheld"):
                ns["run_scan"]()
        self.assertEqual(batches, [100, 100, 5])
        self.assertIsNone(state["result"])
        analyze.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
