"""Portfolio quote failure must not be silently counted as flat P&L."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTION = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "build_portfolio_card")

class PortfolioQuoteTests(unittest.TestCase):
    def test_no_cost_basis_as_quote_fallback(self):
        body = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertNotIn("else avg", body)
        self.assertIn("ltp = None", body)

    def test_partial_pnl_is_labeled(self):
        body = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("priced_inv", body)
        self.assertIn("Partial", body)
        self.assertIn("Prices unavailable", body)

    def test_invalid_prices_are_excluded(self):
        body = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("math.isfinite", body)
        self.assertIn("ltp <= 0", body)

if __name__ == "__main__":
    unittest.main()
