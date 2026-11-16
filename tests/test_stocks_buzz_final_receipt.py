"""Check owner-only Stocks Buzz receipt classification without live Telegram."""
import unittest
from unittest.mock import patch
from pathlib import Path
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


    def test_owner_only_header_log_and_force_refresh_contract(self):
        probe = {"action": "StocksBuzz", "receipts": [], "files": {}, "closed": False}
        token = _PROBE.set(probe)
        try:
            with patch("msk_live_acceptance.LOG.info") as log:
                capture_telegram_ack("text", 205, message_text=(
                    "🔥 STOCKS BUZZ — Breakout Radar\\n"
                    "📅 Scan date: 2026-11-16\\n"
                    "📈 Candle session: 2026-11-13\\n"
                    "📊 Coverage: 91.8%\\n"
                    "🕒 DATA STATUS: FRESH\\n"
                    "🔎 Scanned 918 of 1000 stocks\\n"
                    "🚀 35 confirmed breakouts"))
            entries = [str(call) for call in log.call_args_list]
            self.assertTrue(any("MSK_ACCEPTANCE_BUZZ_RETURNED_HEADER" in entry
                                and "FRESH" in entry and "91.8%" in entry
                                for entry in entries))
        finally:
            _PROBE.reset(token)
        repo = Path(__file__).resolve().parents[1]
        main = (repo / "main.py").read_text(encoding="utf-8")
        acceptance = (repo / "msk_live_acceptance.py").read_text(encoding="utf-8")
        self.assertIn("def run_buzz(chat_id, *, force_refresh=False):", main)
        self.assertIn("cached = None if force_refresh else stocks_buzz.get_cached()", main)
        self.assertIn("MSK_ACCEPTANCE_FORCE_BUZZ_SCAN", acceptance)


if __name__ == "__main__":
    unittest.main()
