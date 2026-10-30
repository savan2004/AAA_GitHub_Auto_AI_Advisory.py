import unittest
from stock_analysis_report_v2 import technical_snapshot, format_report

class IntegratedReportTests(unittest.TestCase):
    def test_insufficient_history(self):
        self.assertNotEqual(technical_snapshot(None)["status"], "OK")
    def test_missing_fundamentals_withholds_score(self):
        text = format_report("TEST", {}, {"status":"Insufficient history"}, "2026-10-26 UTC")
        self.assertIn("Withheld", text)
        self.assertIn("insufficient data", text.lower())
    def test_escapes_symbol(self):
        text = format_report("<BAD>", {}, {"status":"Invalid"}, "now")
        self.assertIn("&lt;BAD&gt;", text)
    def test_invalid_price(self):
        class FakeFrame:
            def __len__(self): return 55
            def __contains__(self, k): return k == "Close"
            def __getitem__(self, k): return self
            def tail(self, n): return [1]*54 + [float("nan")]
        self.assertEqual(technical_snapshot(FakeFrame())["status"], "Invalid price history")

if __name__ == "__main__":
    unittest.main()
