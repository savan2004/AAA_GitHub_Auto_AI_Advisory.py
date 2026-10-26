import unittest
from group_stock_requests import extract_stock_request


class GroupStockRequestTests(unittest.TestCase):
    def test_normal_language(self):
        self.assertEqual(extract_stock_request("MSK share review on Reliance"), "RELIANCE")
        self.assertEqual(extract_stock_request("MSK what about Tata Motors?"), "TATAMOTORS")
        self.assertEqual(extract_stock_request("MSK technical analysis of HDFC Bank"), "HDFCBANK")
        self.assertEqual(extract_stock_request("MSK chart NSE:SBIN"), "SBIN")

    def test_does_not_reply_to_chatter(self):
        for message in ("Reliance is rising", "MSK hello", "Review Reliance",
                        "MSK review the market", "MSK review Reliance and TCS",
                        "MSK review ABC unknown company"):
            with self.subTest(message=message):
                self.assertIsNone(extract_stock_request(message))

    def test_bounds(self):
        self.assertIsNone(extract_stock_request("MSK review Reliance " + "x" * 300))
        self.assertIsNone(extract_stock_request(None))


if __name__ == "__main__":
    unittest.main()
