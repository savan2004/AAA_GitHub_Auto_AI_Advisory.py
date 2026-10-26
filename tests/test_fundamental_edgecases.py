"""Additional offline regression cases for financial numeric boundaries.

No network, credentials, market-data calls, or production changes.
"""
import unittest
from test_fundamental_numeric_validation import load_safe_val


class FinancialNumericEdgeCases(unittest.TestCase):
    def setUp(self):
        self.safe_val = load_safe_val()

    def test_negative_zero_is_preserved(self):
        self.assertEqual(self.safe_val({"debtToEquity": -0.0}, "debtToEquity"), 0.0)

    def test_overflow_after_multiplier_falls_back(self):
        self.assertEqual(self.safe_val({"first": 1e308, "second": 1.25},
                                       "first", "second", mul=100), 125.0)

    def test_invalid_primary_zero_fallback(self):
        self.assertEqual(self.safe_val({"first": "NaN", "second": 0},
                                       "first", "second"), 0.0)

    def test_numeric_string_indian_comma_is_not_silently_misread(self):
        self.assertIsNone(self.safe_val({"value": "1,23,456"}, "value"))

    def test_non_numeric_text_never_becomes_zero(self):
        self.assertIsNone(self.safe_val({"value": "N/A"}, "value"))


if __name__ == "__main__":
    unittest.main()
