"""Pure, offline acceptance for BOTH Swing Telegram investor reports.

No bot token, NSE/Yahoo calls, account data or sample output is published.
Fixtures are synthetic and used only for code regression.
"""
import ast
import html
import logging
import math
import time
import unittest
from collections import Counter
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1].joinpath("swing_trades.py").read_text(encoding="utf-8")
NODES = ast.parse(SOURCE)


def scope_for(*names, data=None):
    scope = {
        "Counter": Counter, "math": math,
        "esc": lambda value: html.escape(str(value), quote=False),
        "MIN_RR1": 1.2, "MAX_PER_SECTOR": 2, "MAX_LONG": 4, "MAX_SHORT": 2,
        "THRESHOLDS": {
            "conservative": {"LONG": 7, "SHORT": 8},
            "aggressive": {"LONG": 6, "SHORT": 7},
        },
        "logger": logging.getLogger("swing_investor_test"),
        "_load": lambda: data or {},
    }
    # Keep original source functions, not a reimplemented copy.
    for name in ("_pick", "_verified_swing_row",
                 "_swing_watch_candidates", "_swing_reason",
                 "_format_swing_watchlist", "get_swing_trades"):
        fn = next(n for n in NODES.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     "swing_trades.py", "exec"), scope)
    return scope


def candidate(sym, *, side="LONG", score=8, price=1000, sector="Banking",
              risk=3, atr=20, volume=1.5):
    sgn = 1 if side == "LONG" else -1
    stop = price - sgn * (price * risk / 100)
    # Real swing engine defines ATR-based targets; risk and R:R recomputed.
    return {
        "sym": sym, "side": side, "score": score,
        "setup": "Breakout" if side == "LONG" else "Breakdown",
        "ltp": price, "sl": stop,
        "t1": price + sgn * (2 * atr),
        "t2": price + sgn * (3.5 * atr),
        "atr": atr, "rr1": 2 * atr / abs(price - stop),
        "rr2": 3.5 * atr / abs(price - stop),
        "volx": volume, "sector": sector,
        "target_model": "structure-v2",
        "stop_basis": "support" if side == "LONG" else "resistance",
        "stop_anchor": stop + sgn*.1*atr,
        "t1_basis": "resistance" if side == "LONG" else "support",
        "t2_basis": "resistance" if side == "LONG" else "support",
        "t1_anchor": price + sgn*2.1*atr,
        "t2_anchor": price + sgn*3.6*atr,
        "parts": {"trend": 2, "weekly": 1, "mom": 2, "adx": 1,
                  "vol": 1, "rs": 1, "setup": 1},
    }


class SwingInvestorTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            candidate("BANK1", score=9, sector="Banking"),
            candidate("TECH1", score=9, sector="Technology"),
            candidate("PHARMA", score=8, sector="Pharma"),
            candidate("BANK2", score=8, sector="Banking"),
            candidate("CEMENT", score=8, sector="Materials"),
            candidate("METAL", side="SHORT", score=9, price=400,
                      risk=5, atr=14, sector="Metals"),
        ]
        self.data = {
            "quality": "READY",
            "scanned": 84, "total": 100, "asof": "13-Nov-2026",
            "regime": "neutral", "rows": self.rows, "trades": [],
        }
        self.scope = scope_for(data=self.data)

    def test_safe_has_real_dynamic_levels_four_eligible_cards_and_wait(self):
        report = self.scope["get_swing_trades"]("conservative")
        self.assertIn("MSK SWING TRADE | SAFE", report)
        self.assertIn("TOP 5 SWING WATCHLIST | 4/5 qualified", report)
        self.assertEqual(report.count(" | Score "), 4)
        self.assertEqual(report.count("Status: WAIT"), 4)
        self.assertNotIn("METAL", report)  # SHORT risk 5% exceeds SAFE 4% cap
        self.assertIn("Entry watch zone: ₹", report)
        self.assertIn("SL: ₹", report)
        self.assertIn("T1: ₹", report)
        self.assertIn("T2: ₹", report)
        self.assertIn("R:R = 1:", report)
        self.assertIn("NO ACTIVE TRADE", report)
        self.assertIn("13-Nov-2026 close", report)
        self.assertIn("Stop risk:", report)
        self.assertIn("Risk per trade: planned loss ≤1%", report)
        self.assertNotIn("HDFCBANK", report)  # Old illustrative example
        self.assertNotIn("Score 8.6/10", report)
        self.assertLess(len(report), 3800)

    def test_aggressive_uses_same_fields_and_half_percent_position_risk(self):
        report = self.scope["get_swing_trades"]("aggressive")
        self.assertIn("MSK SWING TRADE | AGGRESSIVE", report)
        self.assertIn("TOP 5 MOMENTUM WATCHLIST", report)
        self.assertEqual(report.count(" | Score "), 5)
        self.assertEqual(report.count("Status: WAIT"), 5)
        self.assertIn("≤0.5%", report)
        self.assertIn("Short requires eligible F&O instrument", report)
        self.assertIn("NO ACTIVE TRADE", report)
        self.assertLess(len(report), 3800)

    def test_ranking_restricts_sector_and_symbol_duplicates(self):
        data = dict(self.data, rows=self.rows + [
            candidate("BANK3", score=10, sector="Banking"),
            candidate("BANK1", side="SHORT", score=10, sector="Banking"),
        ])
        picks = self.scope["_swing_watch_candidates"](
            data, {"LONG": 7, "SHORT": 8})
        self.assertLessEqual(len(picks), 5)
        self.assertEqual(len({p["sym"] for p in picks}), len(picks))
        self.assertLessEqual(sum(p["sector"] == "Banking" for p in picks), 2)

    def test_invalid_risk_direction_or_infinite_rejected(self):
        check = self.scope["_verified_swing_row"]
        x = candidate("BAD", side="LONG")
        x["sl"] = x["ltp"] + 50
        self.assertIsNone(check(x))
        y = candidate("BAD2", side="SHORT", price=100, risk=5, atr=10)
        y["t1"] = y["ltp"] + 5
        self.assertIsNone(check(y))
        z = candidate("BAD3")
        z["ltp"] = math.inf
        self.assertIsNone(check(z))
        m = candidate("BAD4")
        del m["parts"]
        self.assertIsNone(check(m))

    def test_short_reverses_target_and_stop_direction(self):
        x = candidate("SHORT", side="SHORT", price=400, risk=5, atr=14)
        verified = self.scope["_verified_swing_row"](x)
        self.assertIsNotNone(verified)
        self.assertGreater(x["sl"], x["ltp"])
        self.assertLess(x["t1"], x["ltp"])
        self.assertLess(x["t2"], x["t1"])
        self.assertAlmostEqual(verified["risk_pct"], 5.0)

    def test_rejects_partial_market_scan_without_price_ideas(self):
        bad = dict(self.data, quality="INSUFFICIENT_COVERAGE",
                   scanned=40, total=100)
        func = scope_for(data=bad)["get_swing_trades"]
        report = func("conservative")
        self.assertIn("ranking withheld", report)
        self.assertNotIn("BANK1", report)
        self.assertNotIn("Entry watch zone", report)

    def test_no_candidates_does_not_fill_with_pretend_prices(self):
        data = dict(self.data, rows=[])
        func = scope_for(data=data)["get_swing_trades"]
        for mode in ("conservative", "aggressive"):
            report = func(mode)
            self.assertIn("NO QUALIFYING SETUP TODAY", report)
            self.assertIn("0/5 qualified", report)
            self.assertNotIn("Entry watch zone", report)
            self.assertNotIn("Status: BUY", report)

    def test_high_stop_risk_is_excluded_not_counted_qualified(self):
        data = dict(self.data, rows=[
            candidate("HIGHSTOP", score=10, price=1000, risk=8, atr=80)])
        report = scope_for(data=data)["get_swing_trades"]("conservative")
        self.assertIn("0/5 qualified", report)
        self.assertIn("NO QUALIFYING SETUP TODAY", report)
        self.assertNotIn("HIGHSTOP", report)
        self.assertNotIn("Entry watch zone", report)

    def test_profile_caps_apply_at_boundary(self):
        data = dict(self.data, rows=[
            candidate("SAFEAT", score=9, risk=4, atr=60, sector="Banking"),
            candidate("SAFEOVER", score=8, risk=4.01, atr=60, sector="Technology"),
            candidate("AGGRAT", score=8, risk=7, atr=60, sector="Materials"),
            candidate("AGGROVER", score=9, risk=7.01, atr=60, sector="Pharma"),
        ])
        safe = scope_for(data=data)["get_swing_trades"]("conservative")
        aggressive = scope_for(data=data)["get_swing_trades"]("aggressive")
        self.assertIn("1/5 qualified", safe)
        self.assertIn("SAFEAT", safe)
        for excluded in ("SAFEOVER", "AGGRAT", "AGGROVER"):
            self.assertNotIn(excluded, safe)
        self.assertIn("3/5 qualified", aggressive)
        for included in ("SAFEAT", "SAFEOVER", "AGGRAT"):
            self.assertIn(included, aggressive)
        self.assertNotIn("AGGROVER", aggressive)

    def test_lower_ranked_stock_backfills_over_limit_top_pick(self):
        data = dict(self.data, rows=[
            candidate("HIGH9", score=10, risk=8, sector="Banking", atr=80),
            candidate("LOW8", score=8, risk=3, sector="Banking"),
        ])
        safe = scope_for(data=data)["get_swing_trades"]("conservative")
        self.assertIn("1/5 qualified", safe)
        self.assertNotIn("HIGH9", safe)
        self.assertIn("LOW8", safe)

    def test_actual_reward_risk_not_forced_to_simulated_two_to_one(self):
        data = dict(self.data, rows=[candidate(
            "REALRR", price=1000, risk=3, atr=22, score=9)])
        text = scope_for(data=data)["get_swing_trades"]("conservative")
        self.assertIn("R:R = 1:1.5 / 1:2.6", text)
        self.assertNotIn("R:R = 1:2.0 / 1:3.0", text)


if __name__ == "__main__":
    unittest.main()
