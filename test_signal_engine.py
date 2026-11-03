import unittest
from signal_engine import make_signal

class SignalTests(unittest.TestCase):
    def test_buy_with_two_to_one_reward_risk(self):
        result = make_signal(110, 105, 100, 2)
        self.assertEqual(result['signal'], 'BUY')
        self.assertEqual(result['stop'], 107)
        self.assertEqual(result['target'], 116)
        self.assertEqual(result['rr'], 2)

    def test_stale_prices_never_trade(self):
        self.assertEqual(make_signal(110, 105, 100, 2, age_days=6)['signal'], 'NO TRADE')

    def test_missing_or_invalid_prices_never_trade(self):
        for price in (0, -1, float('nan'), float('inf')):
            self.assertEqual(make_signal(price, 105, 100, 2)['signal'], 'NO TRADE')

    def test_volatile_stocks_never_trade(self):
        self.assertEqual(make_signal(110, 105, 100, 20)['signal'], 'NO TRADE')

    def test_bearish_trend_does_not_claim_short_sale(self):
        self.assertEqual(make_signal(90, 95, 100, 2)['signal'], 'SELL / EXIT WATCH')

    def test_uncertain_trend_waits(self):
        self.assertEqual(make_signal(100, 105, 95, 2)['signal'], 'HOLD / WAIT')

if __name__ == '__main__':
    unittest.main()
