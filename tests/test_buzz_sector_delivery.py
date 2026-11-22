"""Exercise the actual button workers with deterministic Telegram and report doubles."""
import ast
import logging
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, call


def worker(name, **dependencies):
    source = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {"logger": logging.getLogger(__name__), **dependencies}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
    return env[name]


class DeliveryTests(unittest.TestCase):
    def test_buzz_failed_chunk_stops_before_keyboard_hint(self):
        send = Mock(side_effect=[object(), None, object()])
        buzz = Mock()
        buzz.format_buzz.return_value = ["first", "second", "last"]
        deliver = worker("_deliver_buzz", safe_send=send, stocks_buzz=buzz,
                         _buzz_keyboard=lambda _: "keyboard")
        deliver(1, {})
        self.assertEqual(send.call_args_list[:2], [
            call(1, "first", reply_markup=None), call(1, "second", reply_markup=None)])
        self.assertEqual(send.call_count, 3)
        self.assertIn("incomplete", send.call_args.args[1])

    def test_sector_request_during_delivery_gets_same_result(self):
        report = Mock()
        report.generate_sector_report_pdf.return_value = (True, "report.pdf", [])
        delivered = []

        def deliver(chat_id, path, summary):
            delivered.append((chat_id, path))
            if chat_id == 1:
                run(2)

        run = worker("run_sector_report", safe_send=Mock(), sector_report=report,
                     _sector_lock=threading.Lock(), _sector_running=False,
                     _sector_waiters=set(), _deliver_sector=deliver, time=time)
        run(1)
        self.assertEqual(delivered, [(1, "report.pdf"), (2, "report.pdf")])
        report.generate_sector_report_pdf.assert_called_once()
