"""Offline numeric validation tests for Fundamental Analysis (Issue #17).

Extract the pure helper from source to avoid importing live providers or
requiring Telegram credentials. No external requests or secrets are used.
"""
import ast
import math
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "fundamentals.py"


def load_safe_val():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "safe_val")
    ns = {"math": math}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns["safe_val"]


def load_yahoo_debt_equity():
    """Import only the pure converter; never import the live provider module."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "yahoo_debt_equity_to_ratio")
    ns = {"math": math}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns["yahoo_debt_equity_to_ratio"]


class FundamentalNumericValidationTests(unittest.TestCase):
    def setUp(self):
        self.safe_val = load_safe_val()
        self.yahoo_de = load_yahoo_debt_equity()

    def test_zero_debt_is_preserved(self):
        self.assertEqual(self.safe_val({"debtToEquity": 0}, "debtToEquity"), 0.0)

    def test_zero_dividend_yield_is_preserved(self):
        self.assertEqual(self.safe_val({"dividendYield": "0"}, "dividendYield"), 0.0)

    def test_rejects_non_finite_and_uses_fallback(self):
        for invalid in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.safe_val({"first": invalid, "second": 2.5},
                                               "first", "second"), 2.5)

    def test_rejects_boolean_and_non_numeric(self):
        self.assertIsNone(self.safe_val({"a": True, "b": "n/a"}, "a", "b"))

    def test_negative_earnings_remain_valid(self):
        self.assertEqual(self.safe_val({"eps": -12.345}, "eps"), -12.35)

    def test_yahoo_percent_point_debt_to_equity_is_actual_ratio(self):
        self.assertEqual(self.yahoo_de(25.0), 0.25)
        self.assertEqual(self.yahoo_de(175.0), 1.75)
        self.assertEqual(self.yahoo_de(0), 0.0)
        self.assertEqual(self.yahoo_de("0.8"), 0.008)

    def test_yahoo_debt_to_equity_invalid_data_is_withheld(self):
        for invalid in (None, True, -5, "bad", float("inf"), float("nan"), 100001):
            with self.subTest(value=invalid):
                self.assertIsNone(self.yahoo_de(invalid))

    def test_fundamentals_uses_normalized_yahoo_fields_but_not_screener_ratio(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('safe_val(info, "debt_equity_ratio")', source)
        self.assertIn('yahoo_debt_equity_to_ratio(info.get("debtToEquity"))', source)
        self.assertIn('yahoo_debt_equity_to_ratio(yf_info.get("debtToEquity"))', source)
        self.assertIn('result["de"]   = sc.get("de")', source)
        self.assertIn('result["de"]     = safe_val(fh, "de")', source)

    def test_multiplier_and_missing_value(self):
        self.assertEqual(self.safe_val({"roe": 0.125}, "roe", mul=100), 12.5)
        self.assertIsNone(self.safe_val({}, "roe"))


if __name__ == "__main__":
    unittest.main()
