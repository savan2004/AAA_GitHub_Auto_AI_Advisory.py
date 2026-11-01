import unittest
from stock_analysis_v2 import assess_stock

class StockAnalysisTests(unittest.TestCase):
    def test_no_data_no_verdict(self):
        r = assess_stock({})
        self.assertIsNone(r["score"])
        self.assertEqual(r["coverage"], 0)
    def test_partial_data_not_scored(self):
        self.assertIsNone(assess_stock({"roe": 20, "debt_equity": 0.2})["score"])
    def test_good_data(self):
        r = assess_stock(dict(roe=20, debt_equity=.2, revenue_growth=12, earnings_growth=15, operating_margin=22, free_cash_flow=100))
        self.assertEqual(r["score"], 10)
        self.assertEqual(r["coverage"], 100)
    def test_bad_data(self):
        r = assess_stock(dict(roe=-1, debt_equity=3, revenue_growth=-2, earnings_growth=-5))
        self.assertEqual(r["score"], 0)
    def test_nan_excluded(self):
        self.assertEqual(assess_stock(dict(roe=float("nan"), debt_equity=.2))["coverage"], 17)

if __name__ == "__main__":
    unittest.main()
