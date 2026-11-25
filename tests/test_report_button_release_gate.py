"""Run the real Research PDF button worker with isolated delivery mocks.

Never contacts Telegram or Neon; tests release gating, ACK and cleanup only.
"""
import ast
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import logging
import os
import threading
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent


def _worker(globals_dict):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "run_report")
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(ROOT / "main.py"), "exec"), globals_dict)
    return globals_dict["run_report"]


def _world(result_path):
    send = Mock()
    bot = Mock()
    bot.send_document.return_value = SimpleNamespace(
        message_id=77123,
        document=SimpleNamespace(file_id="document-receipt-test"))
    ack = Mock()
    thread = SimpleNamespace(
        Event=threading.Event,
        Thread=lambda **kw: SimpleNamespace(start=lambda: None))
    ns = {
        "resolve_symbol": lambda value: ("SBIN.NS", "State Bank of India"),
        "safe_send": send, "threading": thread,
        "generate_stock_report_pdf": lambda sym: (True, str(result_path), "State Bank of India"),
        "logger": logging.getLogger("test_pdf_button"),
        "get_verified_quote": lambda sym: None,
        "bot": bot, "_record_telegram_api_ack": ack,
        "datetime": datetime, "os": os,
    }
    return _worker(ns), send, bot, ack


class ResearchPdfButtonGateTests(unittest.TestCase):
    def test_rejects_short_report_and_does_not_send(self):
        from pdf_release_gate import ResearchPDFRefused
        with TemporaryDirectory() as folder:
            file = Path(folder) / "SBIN_report.pdf"
            file.write_bytes(b"%PDF-1.4\nshort")
            run, send, bot, ack = _world(file)
            with patch("pdf_release_gate.validate_research_pdf",
                       side_effect=ResearchPDFRefused("Missing 8 pages")) as gate:
                run(12, "SBIN")
            gate.assert_called_once_with(str(file), expected_symbol="SBIN",
                                         min_pages=8)
            bot.send_document.assert_not_called()
            ack.assert_not_called()
            self.assertFalse(file.exists())
            self.assertTrue(any("withheld" in str(x.args[1]).lower()
                                for x in send.call_args_list))

    def test_generation_error_does_not_expose_provider_message(self):
        with TemporaryDirectory() as folder:
            run, send, bot, ack = _world(Path(folder) / "unused.pdf")
            secret = "postgresql://private-user:private-password@hidden-host/db"
            run.__globals__["generate_stock_report_pdf"] = lambda sym: (
                False, "HTTP failure: " + secret, "Issuer")
            run(12, "SBIN")
            customer_text = " ".join(str(call.args[1])
                                     for call in send.call_args_list)
            self.assertIn("Research PDF unavailable", customer_text)
            self.assertNotIn(secret, customer_text)
            bot.send_document.assert_not_called()
            ack.assert_not_called()

    def test_unhandled_provider_exception_does_not_expose_secret(self):
        with TemporaryDirectory() as folder:
            run, send, bot, ack = _world(Path(folder) / "unused.pdf")
            secret = "PRIVATE_PROVIDER_TOKEN_NOT_FOR_TELEGRAM"
            def fail(_sym):
                raise RuntimeError(secret)
            run.__globals__["generate_stock_report_pdf"] = fail
            run(12, "SBIN")
            customer_text = " ".join(str(call.args[1])
                                     for call in send.call_args_list)
            self.assertIn("Research PDF unavailable", customer_text)
            self.assertNotIn(secret, customer_text)
            bot.send_document.assert_not_called()
            ack.assert_not_called()

    def test_validated_pdf_sent_once_and_ack_recorded(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "SBIN_report.pdf"
            file.write_bytes(b"%PDF-1.4\ngenuine test fixture only")
            run, send, bot, ack = _world(file)
            with patch("pdf_release_gate.validate_research_pdf",
                       return_value={"pages":8, "bytes":685897,
                                     "status":"structure_and_disclosure_only"}) as gate:
                run(12, "SBIN")
            gate.assert_called_once_with(str(file), expected_symbol="SBIN",
                                         min_pages=8)
            bot.send_document.assert_called_once()
            ack.assert_called_once()
            self.assertEqual(ack.call_args.args[2], "document")
            self.assertFalse(file.exists())

    def test_verified_daily_cache_skips_all_slow_downloads(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "SBIN_20261113_verified.pdf"
            file.write_bytes(b"%PDF-1.4\\nverified internal fixture")
            run, send, bot, ack = _world(file)
            run.__globals__["generate_stock_report_pdf"] = Mock(
                side_effect=AssertionError("No new provider calls for certified cache"))
            with (
                patch("research_pdf_snapshot.load_verified_report",
                      return_value=(str(file), "2026-11-13")) as read,
                patch("pdf_release_gate.validate_research_pdf",
                      return_value={"pages":8,"bytes":685897}),
            ):
                run(12, "SBIN")
            read.assert_called_once_with("SBIN")
            bot.send_document.assert_called_once()
            ack.assert_called_once()
            self.assertFalse(file.exists())

    def test_invalid_symbol_disclosure_never_bypasses_gate(self):
        from pdf_release_gate import ResearchPDFRefused
        with TemporaryDirectory() as folder:
            file = Path(folder) / "SBIN_report.pdf"
            file.write_bytes(b"%PDF-1.4\nnot for sale")
            run, send, bot, ack = _world(file)
            with patch("pdf_release_gate.validate_research_pdf",
                       side_effect=ResearchPDFRefused("Wrong symbol")):
                run(12, "SBIN")
            bot.send_document.assert_not_called()
            self.assertFalse(file.exists())

    def test_local_report_reuses_file_and_updates_only_caption(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "SBIN_cached.pdf"
            raw = b"%PDF-1.4\nlocal fixture"
            file.write_bytes(raw)
            run, send, bot, ack = _world(file)
            run.__globals__["generate_stock_report_pdf"] = Mock(
                side_effect=AssertionError("No full regeneration"))
            run.__globals__["get_verified_quote"] = lambda sym: {
                "price": 123.45, "quote_source": "NSE India",
                "quote_asof_ist": "2026-11-13T14:10:00+05:30",
                "quote_status": "recent_observation"}
            captured = []
            def capture(chat, stream, **kwargs):
                captured.append((stream.read(), kwargs["caption"]))
                return SimpleNamespace(message_id=123)
            bot.send_document.side_effect = capture
            with (
                patch("research_pdf_snapshot.load_recent_local_report",
                      return_value=(str(file), "2026-11-13")),
                patch("research_pdf_snapshot.load_verified_report",
                      side_effect=AssertionError("No DB read on local hit")),
                patch("pdf_release_gate.validate_research_pdf",
                      return_value={"pages": 8, "bytes": 685897}),
            ):
                run(12, "SBIN")
            self.assertEqual(captured[0][0], raw)
            self.assertIn("123.45", captured[0][1])
            self.assertIn("14:10 IST", captured[0][1])
            bot.send_document.assert_called_once()
            ack.assert_called_once()


if __name__ == "__main__":
    unittest.main()
