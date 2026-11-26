"""Check owner-only Stocks Buzz receipt classification without live Telegram."""
import ast
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
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
        buzz_code = main.split("def run_buzz(chat_id, *, force_refresh=False):", 1)[1].split("@bot.message_handler(commands=", 1)[0]
        self.assertNotIn("stocks_buzz.run_scan(", buzz_code)
        self.assertIn("MSK_BUZZ_SNAPSHOT_MISSING", buzz_code)
        self.assertIn("MSK_ACCEPTANCE_FORCE_BUZZ_SCAN", acceptance)


    def _render_buzz_handler(self, *, cached=None, lookup_error=None):
        """Compile the production function alone; no Telegram or stock API."""
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "run_buzz")
        get_cached = Mock(return_value=cached, side_effect=lookup_error)
        scan = Mock(side_effect=AssertionError("Render must never run a live scan"))
        module = SimpleNamespace(get_cached=get_cached, run_scan=scan)
        sent, delivered, logger = Mock(), Mock(), Mock()
        ns = {"stocks_buzz": module, "safe_send": sent,
              "_deliver_buzz": delivered, "logger": logger}
        exec(compile(ast.fix_missing_locations(ast.Module(
             body=[handler], type_ignores=[])), "<msk-real-run-buzz>", "exec"), ns)
        return ns["run_buzz"], get_cached, scan, sent, delivered

    def test_render_missing_research_never_starts_scan_or_sends_picks(self):
        handler, lookup, scan, sent, delivered = self._render_buzz_handler()
        handler(42)
        handler(43, force_refresh=True)
        self.assertEqual(lookup.call_count, 2)
        scan.assert_not_called()
        delivered.assert_not_called()
        self.assertEqual(sent.call_count, 2)
        self.assertIn("not ready", sent.call_args_list[0].args[1])
        self.assertIn("GitHub Actions", sent.call_args_list[0].args[1])

    def test_render_cached_research_delivered_without_downloading_stocks(self):
        result = {"data_session": "synthetic-fixture", "downloaded": 1000}
        handler, lookup, scan, sent, delivered = self._render_buzz_handler(cached=result)
        handler(42)
        lookup.assert_called_once_with()
        scan.assert_not_called()
        sent.assert_not_called()
        delivered.assert_called_once_with(42, result)

    def test_render_research_database_error_fails_closed(self):
        handler, lookup, scan, sent, delivered = self._render_buzz_handler(
            lookup_error=RuntimeError("private-connection-string"))
        handler(42)
        lookup.assert_called_once_with()
        scan.assert_not_called()
        delivered.assert_not_called()
        self.assertIn("not ready", sent.call_args.args[1])
        self.assertNotIn("private-connection-string", sent.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
