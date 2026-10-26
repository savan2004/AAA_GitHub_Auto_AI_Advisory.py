"""Static regression checks for the Fundamental Analysis button output contract."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTION = next(n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "build_fundamental_text")

class FundamentalQualityTests(unittest.TestCase):
    def test_completeness_and_missing_metrics_visible(self):
        text = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("Data completeness:", text)
        self.assertIn("Missing:", text)
        self.assertIn("Verify against filings", text)

    def test_zero_values_are_not_discarded_by_truthiness(self):
        text = ast.get_source_segment(SOURCE, FUNCTION)
        for key in ("pe", "pb", "eps", "mcap", "rev", "de", "beta"):
            self.assertIn('fund.get("'+key+'") is not None', text)

    def test_untrusted_company_name_is_escaped(self):
        text = ast.get_source_segment(SOURCE, FUNCTION)
        self.assertIn("html.escape(str(name))", text)

if __name__ == "__main__":
    unittest.main()
