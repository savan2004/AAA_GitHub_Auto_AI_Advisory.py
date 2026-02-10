import unittest
from bot_main import get_signal  # Import from bot_main.py

class TestSignals(unittest.TestCase):
    def test_get_signal_valid(self):
        # Test signal generation for a valid symbol and price
        signal = get_signal("RELIANCE", 2500.0)
        self.assertIsInstance(signal, str)  # Should return a string
        self.assertGreater(len(signal), 0)  # Should not be empty
    
    def test_get_signal_fallback(self):
        # Test fallback to OpenAI if Gemini fails (simulate by mocking, but here we test basic call)
        # Note: In real test, you might mock API failures; here we assume APIs work
        signal = get_signal("NIFTY", 18000.0)
        self.assertIsInstance(signal, str)
    
    def test_get_signal_invalid_price(self):
        # Test with invalid price (should still generate signal)
        signal = get_signal("BANKNIFTY", -100.0)
        self.assertIsInstance(signal, str)  # Should handle gracefully

if __name__ == '__main__':
    unittest.main()
