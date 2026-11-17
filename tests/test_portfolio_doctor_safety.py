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

if __name__ == "__main__":
    unittest.main()
