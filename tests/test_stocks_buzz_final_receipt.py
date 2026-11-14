"""Check owner-only Stocks Buzz receipt classification without live Telegram."""
import unittest
from msk_live_acceptance import _PROBE, capture_telegram_ack


class StocksBuzzFinalReceiptTests(unittest.TestCase):
    def test_only_final_scan_output_is_accepted(self):
        probe = {"action": "StocksBuzz", "receipts": [], "files": {}, "closed": False}
        token = _PROBE.set(probe)
        try:
            capture_telegram_ack("text", 201, message_text="🔥 Stocks Buzz — scanning the top 1000 NSE stocks")
            capture_telegram_ack("text", 202, message_text="⏳ Downloaded 450/1000 stocks…")
            capture_telegram_ack("text", 203, message_text="❌ Stocks Buzz failed: TimeoutError")
            self.assertEqual(probe["receipts"], [])
            capture_telegram_ack("text", 204, message_text=(
                "🔥 STOCKS BUZZ — Breakout Radar\n"
                "🔎 Scanned 980 of 1000 stocks → 600 passed liquidity filter\n"
                "🚀 2 confirmed breakouts"))
            self.assertEqual(probe["receipts"], [("text", 204)])
        finally:
            _PROBE.reset(token)


if __name__ == "__main__":
    unittest.main()
