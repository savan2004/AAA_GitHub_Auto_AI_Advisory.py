import unittest
from data_manager import DataManager

class TestLTP(unittest.TestCase):
    def setUp(self):
        self.data_manager = DataManager()
    
    def test_get_ltp_valid_symbol(self):
        # Test LTP retrieval for a valid symbol (e.g., RELIANCE)
        price = self.data_manager.get_ltp("RELIANCE")
        self.assertIsInstance(price, (float, type(None)))  # Should return float or None
        if price:
            self.assertGreater(price, 0)  # Price should be positive if available
    
    def test_get_ltp_invalid_symbol(self):
        # Test LTP for invalid symbol
        price = self.data_manager.get_ltp("INVALID")
        self.assertIsNone(price)  # Should return None for invalid symbol
    
    def test_throttling(self):
        # Test that requests are throttled (no rapid calls)
        import time
        start = time.time()
        self.data_manager.get_ltp("NIFTY")
        self.data_manager.get_ltp("BANKNIFTY")
        end = time.time()
        self.assertGreaterEqual(end - start, 1)  # At least 1 second delay

if __name__ == '__main__':
    unittest.main()
