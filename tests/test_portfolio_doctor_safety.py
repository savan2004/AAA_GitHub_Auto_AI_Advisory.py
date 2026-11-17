"""Offline Portfolio Doctor hard-deadline and workspace protection tests."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import portfolio_doctor_runner as runner


class WorkerTests(unittest.TestCase):
    def test_reject_bad_input_before_process_start(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runner.subprocess, "Popen") as popen:
                for filename, data in (("foo.xls", b"x"), ("foo.csv", b""),
                                       ("foo.xlsx", b"x" * (runner.MAX_BYTES + 1))):
                    with self.assertRaises(runner.PortfolioDoctorError):
                        runner.run_isolated(filename, data, folder)
                popen.assert_not_called()

    def test_reject_workspace_escape_and_fake_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            internal = root / "internal.pdf"
            internal.write_bytes(b"not a real pdf" * 200)
            with self.assertRaises(runner.PortfolioDoctorError):
                runner._pdf_in_workspace(internal, root)
            with tempfile.TemporaryDirectory() as other:
                outside = Path(other) / "other.pdf"
                outside.write_bytes(b"%PDF-" + b"x" * 1100)
                with self.assertRaises(runner.PortfolioDoctorError):
                    runner._pdf_in_workspace(outside, root)
            internal.write_bytes(b"%PDF-" + b"x" * 1100)
            self.assertEqual(runner._pdf_in_workspace(internal, root), internal)

    def test_hard_timeout_terminates_child_before_return(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = unittest.mock.Mock()
            fake.pid = 10293
            fake.wait.side_effect = [subprocess.TimeoutExpired("worker", 0.01), 0]
            with patch.object(runner.subprocess, "Popen", return_value=fake), \
                 patch.object(runner.os, "killpg") as killpg:
                with self.assertRaises(runner.PortfolioDoctorTimeout):
                    runner.run_isolated("positions.csv", b"SBIN,1,1", folder, timeout=0.01)
                if os.name == "posix":
                    killpg.assert_called_once_with(10293, runner.signal.SIGKILL)
                else:
                    fake.kill.assert_called_once()
                self.assertEqual(fake.wait.call_count, 2)

    def test_child_failure_not_accepted_as_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = unittest.mock.Mock()
            fake.wait.return_value = 1
            with patch.object(runner.subprocess, "Popen", return_value=fake):
                with self.assertRaises(runner.PortfolioDoctorError):
                    runner.run_isolated("positions.csv", b"SBIN,1,1", folder, timeout=1)

    def test_no_saved_holdings_or_telegram_references(self):
        src = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("portfolio_data.json", src)
        self.assertNotIn("send_document", src)



class LedgerTests(unittest.TestCase):
    def _ledger(self):
        from portfolio_doctor_ledger import PortfolioDoctorLedger
        return PortfolioDoctorLedger("postgresql://example.invalid/db", "long-independent-secret-for-unit-tests")

    def test_ledger_refuses_missing_configuration(self):
        from portfolio_doctor_ledger import PortfolioDoctorLedger, LedgerUnavailable
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LedgerUnavailable):
                PortfolioDoctorLedger.from_env()
        with self.assertRaises(LedgerUnavailable):
            PortfolioDoctorLedger("postgresql://example.invalid/db", "too-short")

    def test_user_and_file_receipts_are_secret_hashed(self):
        ledger = self._ledger()
        u1, u2 = ledger._digest("user", 123), ledger._digest("user", 124)
        self.assertNotEqual(u1, u2)
        self.assertNotEqual(u1, ledger._digest("upload", b"123"))
        self.assertEqual(len(u1), 64)
        self.assertNotIn("123", u1)

    def test_claim_is_database_backed_and_duplicate_resistant(self):
        from unittest.mock import MagicMock
        ledger = self._ledger()
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = ("r1",)
        with patch.object(ledger, "_connection", return_value=ctx):
            self.assertTrue(ledger.reserve("r1", 10, b"private"))
            sql = " ".join(str(c.args[0]) for c in conn.execute.call_args_list)
            self.assertIn("ON CONFLICT (user_hash, upload_hash, request_day) DO NOTHING", sql)
            self.assertIn("UNIQUE (user_hash, upload_hash, request_day)", sql)
            self.assertNotIn("private", str(conn.execute.call_args_list))
            conn.execute.return_value.fetchone.return_value = None
            self.assertFalse(ledger.reserve("r2", 10, b"private"))

    def test_receipt_must_contain_telegram_message_and_file(self):
        from portfolio_doctor_ledger import LedgerUnavailable
        ledger = self._ledger()
        with patch.object(ledger, "_transition") as transition:
            with self.assertRaises(LedgerUnavailable):
                ledger.mark_sent("r", 0, "abc")
            with self.assertRaises(LedgerUnavailable):
                ledger.mark_sent("r", 12, None)
            transition.assert_not_called()
            ledger.mark_sent("r", 12, "tgfile")
            transition.assert_called_once_with("r", "SENDING", "SENT", 12, "tgfile")

    def test_ambiguous_telegram_send_cannot_be_retried(self):
        from unittest.mock import MagicMock
        ledger = self._ledger()
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = conn
        with patch.object(ledger, "_connection", return_value=ctx):
            ledger.mark_failed("r")
        sql = " ".join(str(c.args[0]) for c in conn.execute.call_args_list)
        self.assertIn("SENDING' THEN 'UNCERTAIN'", sql)
        self.assertNotIn("DELETE FROM", sql)

    def test_customer_buttons_stay_disabled_by_default(self):
        main = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertIn('MSK_PORTFOLIO_DOCTOR_TEST_ENABLED", "") == "1"', main)
        self.assertIn('getattr(getattr(m, "chat", None), "type", None) == "private"', main)
        self.assertIn('safe_send(m.chat.id, "⚠️ Portfolio is temporarily hidden', main)
        self.assertIn('run_isolated(name, payload, folder, timeout=150)', main)
        self.assertIn('ledger.mark_sending(report_id)', main)
        self.assertIn('ledger.mark_sent(report_id, message_id, file_id)', main)


class TypedInputTests(unittest.TestCase):
    """Run actual handler code without importing the live Telegram bot."""

    def _extract(self, name, namespace):
        import ast
        import copy
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        node = copy.deepcopy(node)
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "<isolated-main>", "exec"), namespace)
        return namespace[name]

    def test_three_lines_become_private_csv_without_stock_search(self):
        import math
        import sys
        import types
        sent = []
        delivered = []
        fake_report = types.ModuleType("portfolio_report")
        fake_report.parse_text = lambda _text: ([
            {"symbol": "DLF", "qty": 50.0, "buy": 480.0},
            {"symbol": "SBIN", "qty": 100.0, "buy": 890.0},
            {"symbol": "BPCL", "qty": 204.0, "buy": 280.0},
        ], [])
        ns = {"math": math, "safe_send": lambda uid, body: sent.append(body),
              "portfolio_doctor_document": lambda msg, _typed_payload=None: delivered.append(_typed_payload)}
        handler = self._extract("portfolio_doctor_text", ns)
        msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=1001),
                                    text="DLF 50 480\nSBIN 100 890\nBPCL 204 280")
        with patch.dict(sys.modules, {"portfolio_report": fake_report}):
            handler(msg)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].decode("utf-8"),
                         "Symbol,Qty,Buy Price\nDLF,50.0,480.0\nSBIN,100.0,890.0\nBPCL,204.0,280.0\n")
        self.assertEqual(sent, [])

    def test_incorrect_holdings_cannot_trigger_report(self):
        import math
        import sys
        import types
        sent, delivered = [], []
        fake_report = types.ModuleType("portfolio_report")
        fake_report.parse_text = lambda _text: ([], ["invalid"])
        ns = {"math": math, "safe_send": lambda uid, body: sent.append(body),
              "portfolio_doctor_document": lambda *_a, **_k: delivered.append(True)}
        handler = self._extract("portfolio_doctor_text", ns)
        msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=1001), text="DLF")
        with patch.dict(sys.modules, {"portfolio_report": fake_report}):
            handler(msg)
        self.assertEqual(delivered, [])
        self.assertIn("Invalid holdings", sent[0])

    def test_pending_doctor_intercepts_text_before_symbol_lookup(self):
        import threading
        from types import SimpleNamespace
        routed, sent = [], []
        ns = {
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_waiting": {1001},
            "_doctor_allowed": lambda m: True,
            "portfolio_doctor_text": lambda m: routed.append(m.text),
            "API_RATE_LIMITER": SimpleNamespace(is_allowed=lambda _uid: True),
            "safe_send": lambda uid, body: sent.append(body),
        }
        handler = self._extract("handle_text", ns)
        handler(SimpleNamespace(chat=SimpleNamespace(id=1001, type="private"),
                                text="DLF 50 480\nSBIN 100 890"))
        self.assertEqual(routed, ["DLF 50 480\nSBIN 100 890"])
        self.assertEqual(sent, [])


class PartialResearchSummaryTests(unittest.TestCase):
    def _run_worker(self, freshness):
        import json
        import sys
        import types
        fake = types.ModuleType("portfolio_report")
        holdings = [{"symbol": "EXAMPLE", "qty": 2, "buy": 30}]
        fake.parse_file = lambda filename, payload: (holdings, [])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tmp").mkdir()
            upload = root / "upload.bin"
            upload.write_bytes(b"Symbol,Qty,Buy Price\nEXAMPLE,2,30")
            pdf = root / "summary.pdf"
            pdf.write_bytes(b"%PDF-" + b"safe" * 400)
            fake.run_job = lambda *_a, **_k: {
                "summary": str(pdf),
                "stocks": [],  # Optional 8-page research PDF failed.
                "recs": [{"priced": True, "freshness": freshness}],
            }
            with patch.dict(sys.modules, {"portfolio_report": fake}):
                if freshness != "Fresh":
                    with self.assertRaises(runner.PortfolioDoctorError) as ctx:
                        runner._worker(upload, root / "result.json", root, "holdings.csv")
                    self.assertEqual(ctx.exception.code, "STALE_OR_MISSING_PRICES")
                    return
                runner._worker(upload, root / "result.json", root, "holdings.csv")
            self.assertEqual(json.loads((root / "result.json").read_text())["status"], "READY")

    def test_fresh_summary_survives_missing_optional_research_pdf(self):
        self._run_worker("Fresh")

    def test_stale_prices_still_block_pdf(self):
        self._run_worker("Stale")



class TextParserTickerTests(unittest.TestCase):
    def test_x_inside_real_ticker_is_not_treated_as_quantity_separator(self):
        import ast
        import copy
        import re
        from typing import Tuple, List
        main = Path(__file__).resolve().parents[1]
        source = (main / "portfolio_report.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "parse_text")
        node = copy.deepcopy(node)
        ns = {"re": re, "Tuple": Tuple, "List": List,
              "parse_rows": lambda rows, resolver=None: (rows, [])}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "<text-parser>", "exec"), ns)
        rows, issues = ns["parse_text"]("AXISBANK 10 1000\nEXIDEIND 20 480\nSBIN 50x890")
        self.assertEqual(issues, [])
        self.assertEqual(rows, [["AXISBANK", "10", "1000"],
                                ["EXIDEIND", "20", "480"], ["SBIN", "50", "890"]])


if __name__ == "__main__":
    unittest.main()
