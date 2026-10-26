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

    def test_concentration_alert_is_cost_basis_based(self):
        body = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("concentration >= 40", body)
        self.assertIn("Concentration risk:", body)
        self.assertIn("cost basis", body)

    def test_invalid_prices_are_excluded(self):
        body = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("math.isfinite", body)
        self.assertIn("ltp <= 0", body)


    def test_rendered_portfolio_with_missing_quote(self):
        from datetime import date
        import math
        namespace = {
            "portfolio": type("Portfolio", (), {"get": lambda self, uid: {
                "RELIANCE": {"qty": 10, "avg": 100.0},
                "TCS": {"qty": 5, "avg": 200.0},
            }})(),
            "get_live_price": lambda sym: 110.0 if sym == "RELIANCE" else None,
            "date": date,
            "math": math,
        }
        exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), "<portfolio>", "exec"), namespace)
        result = namespace["build_portfolio_card"](123)
        self.assertIn("Partial P&L: ₹+100.00", result)
        self.assertIn("TCS", result)
        self.assertIn("LTP:Unavailable", result)
        self.assertIn("priced capital: ₹1,000.00", result)
        self.assertNotIn("Total P&L:", result)

    def test_all_missing_quotes_are_not_flat_returns(self):
        from datetime import date
        import math
        namespace = {
            "portfolio": type("Portfolio", (), {"get": lambda self, uid: {
                "TCS": {"qty": 5, "avg": 200.0},
            }})(),
            "get_live_price": lambda sym: None,
            "date": date,
            "math": math,
        }
        exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), "<portfolio>", "exec"), namespace)
        result = namespace["build_portfolio_card"](123)
        self.assertIn("Prices unavailable:", result)
        self.assertIn("LTP:Unavailable", result)
        self.assertNotIn("Total P&L:", result)

if __name__ == "__main__":
    unittest.main()
