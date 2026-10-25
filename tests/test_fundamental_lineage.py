"""Offline regression suite: financial ratios and field-level provider lineage.

No HTTP requests, Telegram access, environment keys, or paid providers.
AST extraction avoids importing the deployed bot's heavy dependencies.
"""
import ast
import logging
import math
import sys
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def extract_functions(path, names, globals_dict=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(selected) != len(names):
        raise AssertionError("missing target function")
    ns = {"math": math, "Optional": Optional}
    if globals_dict:
        ns.update(globals_dict)
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, "exec"), ns)
    return ns


class SourceAccuracyTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.calls = []
        self.yahoo = {}
        self.screener = {}
        self.finnhub = {}
        self.yfinance = {}
        env = {
            "logger": logging.getLogger("financial-audit-test"),
            "_get_cached": lambda key, ttl: self.cache.get(key),
            "_set_cached": lambda key, val, ttl=None: self.cache.__setitem__(key, val),
            "_fetch_screener": lambda sym: self.screener.copy(),
            "_fetch_finnhub": lambda sym: self.finnhub.copy(),
            "_fetch_yfinance": lambda sym: self.yfinance.copy(),
            "CACHE_TTL_FUND": 3600,
        }
        self.funcs = extract_functions(
            "fundamentals.py", {"safe_val", "yahoo_debt_equity", "get_fundamentals"}, env)

    def query(self):
        module = types.ModuleType("data_engine")
        def get_info(sym):
            self.calls.append(sym)
            return self.yahoo.copy()
        module.get_info = get_info
        with patch.dict(sys.modules, {"data_engine": module}):
            return self.funcs["get_fundamentals"]("INFY")

    def test_yahoo_raw_percent_normalized(self):
        self.yahoo = {"pe": 22, "debtToEquity": 75, "eps": -3}
        result = self.query()
        self.assertEqual(result["de"], 0.75)
        self.assertEqual(result["eps"], -3)
        self.assertEqual(result["field_sources"]["de"],
                         "data_engine (mixed third-party providers)")

    def test_pre_normalized_ratio_preferred(self):
        self.yahoo = {"debt_equity_ratio": 0.83, "debtToEquity": 83}
        self.assertEqual(self.query()["de"], 0.83)

    def test_rounded_zero_debt_is_kept(self):
        self.yahoo = {"debtToEquity": 0, "eps": 0, "roe": 0}
        result = self.query()
        self.assertEqual(result["de"], 0.0)
        self.assertEqual(result["eps"], 0.0)
        self.assertEqual(result["roe"], 0.0)
        self.assertEqual(result["field_sources"]["de"],
                         "data_engine (mixed third-party providers)")

    def test_screener_debt_already_ratio_not_redivided(self):
        self.screener = {"de": 0.52, "pe": 17}
        result = self.query()
        self.assertEqual(result["de"], 0.52)
        self.assertEqual(result["field_sources"]["de"], "Screener.in")
        self.assertEqual(result["field_sources"]["pe"], "Screener.in")

    def test_finnhub_debt_already_ratio_not_redivided(self):
        self.finnhub = {"de": 1.25, "eps": -2}
        result = self.query()
        self.assertEqual(result["de"], 1.25)
        self.assertEqual(result["field_sources"]["de"], "Finnhub")

    def test_yfinance_debt_percent_normalized(self):
        self.yfinance = {"debtToEquity": 140, "trailingPE": 20}
        result = self.query()
        self.assertEqual(result["de"], 1.4)
        self.assertEqual(result["field_sources"]["de"], "yfinance (Yahoo)")

    def test_fallback_only_fills_missing_preserving_first_source(self):
        self.yahoo = {"pe": 25}
        self.screener = {"pe": 10, "roe": 18}
        self.finnhub = {"roe": 5, "eps": 22}
        result = self.query()
        self.assertEqual(result["pe"], 25)
        self.assertEqual(result["roe"], 18)
        self.assertEqual(result["eps"], 22)
        self.assertIn("data_engine", result["field_sources"]["pe"])
        self.assertEqual(result["field_sources"]["roe"], "Screener.in")
        self.assertEqual(result["field_sources"]["eps"], "Finnhub")

    def test_cached_result_does_not_refetch(self):
        self.yahoo = {"debtToEquity": 80}
        first = self.query()
        self.yahoo = {"debtToEquity": 999}
        second = self.query()
        self.assertEqual(first, second)
        self.assertEqual(self.calls, ["INFY"])

    def test_reject_nonfinite_nan_inf_bool(self):
        safe = self.funcs["safe_val"]
        self.assertIsNone(safe({"x": float("nan")}, "x"))
        self.assertIsNone(safe({"x": float("inf")}, "x"))
        self.assertIsNone(safe({"x": True}, "x"))
        self.assertEqual(safe({"x": 0}, "x"), 0)

    def test_main_safe_val_nonfinite_and_zero(self):
        main = extract_functions("main.py", {"safe_val"})["safe_val"]
        self.assertEqual(main({"de": 0}, "de"), 0)
        self.assertIsNone(main({"de": math.nan}, "de"))
        self.assertIsNone(main({"de": True}, "de"))

    def test_main_uses_normalized_debt_ratio(self):
        content = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertEqual(content.count('de = safe_val(info, "debt_equity_ratio")'), 2)
        self.assertEqual(content.count('de = safe_val(info, "debtToEquity", mul=0.01)'), 2)
        self.assertNotIn('de = fund.get("de") or safe_val(info, "debtToEquity")', content)

    def test_research_report_show_source_not_exchange_verified(self):
        sys.path.insert(0, str(ROOT))
        from fundamental_audit import format_audit
        audit = format_audit("INFY", {
            "pe": 22, "de": 0.75,
            "field_sources": {
                "pe": "Screener.in",
                "de": "data_engine (mixed third-party providers)",
            }})
        self.assertIn("P/E: 22.00 [provider: Screener.in]", audit)
        self.assertIn("Debt/Equity: 0.75 [provider: Mixed free providers]", audit)
        self.assertIn("UNVERIFIED", audit)
        self.assertIn("Audit generated", audit)
        self.assertNotIn("Retrieved:", audit)

    def test_untrusted_source_label_cannot_spoof_verified(self):
        sys.path.insert(0, str(ROOT))
        from fundamental_audit import format_audit
        audit = format_audit("INFY", {"pe": 10,
            "field_sources": {"pe": "NSE VERIFIED \nBUY!"}})
        self.assertIn("provider: source unknown", audit)
        self.assertNotIn("BUY!", audit)


if __name__ == "__main__":
    unittest.main()
