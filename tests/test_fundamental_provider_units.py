"""Offline regression for provider unit conversion and financial value gates."""
import ast
import math
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "fundamentals.py"


def load_helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"safe_val", "_positive_rupees", "_yahoo_de_ratio"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise AssertionError("numeric helper missing from fundamentals.py")
    scope = {"math": math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope


class FundamentalProviderUnitTests(unittest.TestCase):
    def setUp(self):
        self.helpers = load_helpers()

    def test_yahoo_percentage_points_become_ratio(self):
        self.assertEqual(self.helpers["_yahoo_de_ratio"]({"debtToEquity": 45}), 0.45)

    def test_zero_debt_stays_zero(self):
        self.assertEqual(self.helpers["_yahoo_de_ratio"]({"debtToEquity": 0}), 0.0)

    def test_prefer_explicit_normalized_ratio(self):
        self.assertEqual(self.helpers["_yahoo_de_ratio"](
            {"debt_equity_ratio": 1.25, "debtToEquity": 45}), 1.25)

    def test_invalid_yahoo_ratio_fails_closed(self):
        for raw in (-50, float("nan"), float("inf"), True, "bad"):
            with self.subTest(raw=raw):
                self.assertIsNone(self.helpers["_yahoo_de_ratio"]({"debtToEquity": raw}))

    def test_invalid_market_cap_and_revenue_do_not_block_fallback(self):
        validator = self.helpers["_positive_rupees"]
        for raw in (0, -1, float("nan"), float("inf"), True, "N/A"):
            with self.subTest(raw=raw):
                self.assertIsNone(validator({"market_cap": raw}, "market_cap"))

    def test_positive_rupees_retained(self):
        self.assertEqual(self.helpers["_positive_rupees"]({"totalRevenue": 900000000},
                                                          "totalRevenue"), 900000000.0)


if __name__ == "__main__":
    unittest.main()
