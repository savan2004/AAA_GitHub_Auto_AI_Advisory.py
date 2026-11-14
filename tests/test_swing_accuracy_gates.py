"""Swing accuracy gates against mixed-session and partial-data rankings."""
import ast
import logging
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

SOURCE = Path(__file__).resolve().parents[1].joinpath("swing_trades.py").read_text(encoding="utf-8")


def extracted(name, overrides=None):
    node = next(n for n in ast.parse(SOURCE).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = {"pd": pd, "time": time, "logger": logging.getLogger("swing_test"),
             "CACHE_TTL": 1800, "HOLD_BARS": 10}
    scope.update(overrides or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]),
                 "swing_trades.py", "exec"), scope)
    return scope[name]


class SwingAccuracy(unittest.TestCase):
    def _scan(self, matching=4, benchmark=True):
        session = pd.Timestamp("2026-11-13")
        idx = pd.bdate_range(end=session, periods=140)
        nifty = pd.DataFrame({"Close": [100 + i for i in range(140)]}, index=idx)
        symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        histories = {sym: object() for sym in symbols}
        used = iter([session] * matching + [pd.Timestamp("2026-11-12")] * (5 - matching))
        cached = Mock()
        scan = extracted("_scan", {
            "_scan_universe": lambda: symbols,
            "bulk_history": lambda *_a, **_k: histories,
            "index_history": lambda *_a: nifty if benchmark else None,
            "_drop_forming": lambda df: df,
            "_naive": lambda x: x,
            "_now_ist": lambda: datetime(2026, 11, 14, 12),
            "_prep": lambda *_a: ({"idx": [next(used)], "c": [1, 2]}, None),
            "_signal": lambda *_a: (None, "no confirmed setup"),
            "_backtest": lambda *_a: [],
            "_sector_of": lambda s: "Sample",
            "_regime": lambda *_a: "neutral",
            "cached_set": cached,
            "_ckey": lambda: "sample",
        })
        return scan(), cached

    def test_four_of_five_same_session_is_accepted(self):
        result, saved = self._scan(matching=4)
        self.assertEqual(result["quality"], "READY")
        self.assertEqual(result["scanned"], 4)
        saved.assert_called_once()

    def test_three_of_five_refuses_all_rankings(self):
        result, saved = self._scan(matching=3)
        self.assertEqual(result["quality"], "INSUFFICIENT_COVERAGE")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["trades"], [])
        saved.assert_not_called()

    def test_no_nifty_benchmark_refuses_rankings(self):
        result, saved = self._scan(benchmark=False)
        self.assertEqual(result["quality"], "NO_BENCHMARK")
        self.assertEqual(result["scanned"], 0)
        saved.assert_not_called()

    def test_no_candidate_can_be_sent_from_unverified_cache(self):
        func = extracted("get_swing_trades", {"_load": lambda: {
            "quality": "INSUFFICIENT_COVERAGE", "rows": [{"sym": "DUMMY"}],
            "scanned": 3, "total": 5,
        }})
        value = func("conservative")
        self.assertIn("ranking withheld", value)
        self.assertNotIn("DUMMY", value)
        self.assertIn("No trade has been recommended", value)


if __name__ == "__main__":
    unittest.main()
