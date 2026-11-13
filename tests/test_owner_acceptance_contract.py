"""Offline contract checks for the opt-in owner acceptance harness."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class OwnerAcceptanceContract(unittest.TestCase):
    def test_owner_only_and_one_time(self):
        code = (ROOT / "msk_live_acceptance.py").read_text(encoding="utf-8")
        ast.parse(code)
        self.assertIn('get_owner_id()', code)
        self.assertIn('_claim_once(run_id)', code)
        self.assertIn('ON CONFLICT DO NOTHING RETURNING run_id', code)
        self.assertIn('PORTFOLIO_DATABASE_URL', code)

    def test_16_actions_and_pdf_checks(self):
        tree = ast.parse((ROOT / "msk_live_acceptance.py").read_text(encoding="utf-8"))
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_actions")
        actions = next(n.value for n in ast.walk(run) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "actions" for t in n.targets))
        self.assertEqual(len(actions.elts), 16)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('MSK_PDF_VALIDATED', main)
        self.assertIn('MSK_LIVE_ACCEPTANCE_RUN_ID', main)



class LiveEvidenceGate(unittest.TestCase):
    def test_telegram_api_receipt_does_not_prove_valid_content(self):
        from msk_live_acceptance import _save_action
        with self.assertRaisesRegex(ValueError, "full live proof"):
            _save_action("test-run", "Analysis", status="PASS",
                         message_id=142, validation_status="passed")

    def test_ratings_cannot_be_written_for_unverified_features(self):
        from msk_live_acceptance import _save_action
        with self.assertRaisesRegex(ValueError, "Scores require passed"):
            _save_action("test-run", "Portfolio", status="UNVERIFIED",
                         final_score=9.0)

    def test_file_receipts_require_a_real_file_id_and_ignore_late_messages(self):
        from msk_live_acceptance import _PROBE, capture_telegram_ack
        probe = {"action": "Report", "receipts": [], "files": {}, "closed": False}
        token = _PROBE.set(probe)
        try:
            capture_telegram_ack("document", 14159)
            self.assertEqual(probe["receipts"], [("document", 14159)])
            self.assertEqual(probe["files"], {})
            capture_telegram_ack("document", 14160, "telegram-file-id")
            self.assertEqual(probe["files"][14160], "telegram-file-id")
            probe["closed"] = True
            capture_telegram_ack("document", 14161, "late-file-id")
            self.assertNotIn(14161, probe["files"])
        finally:
            _PROBE.reset(token)

    def test_full_audit_does_not_label_an_api_ack_as_pass(self):
        code = (ROOT / "msk_live_acceptance.py").read_text(encoding="utf-8")
        self.assertIn('record_status = "FAIL" if fail else "UNVERIFIED"', code)
        self.assertIn('MSK_ACCEPTANCE_NEON_SAVED', code)
        self.assertIn('MSK_ACCEPTANCE_NEON_FAILED', code)

class ClaimOnceDatabaseRegression(unittest.TestCase):
    def test_inserted_and_existing_runs_return_correct_boolean(self):
        import os
        from unittest.mock import MagicMock, patch
        from msk_live_acceptance import _claim_once

        conn = MagicMock()
        conn.__enter__.return_value = conn
        cursor = MagicMock()
        conn.execute.return_value = cursor
        psycopg = MagicMock()
        psycopg.connect.return_value = conn
        with patch.dict(os.environ, {"PORTFOLIO_DATABASE_URL":
                                     "postgresql://sample.invalid/neondb?sslmode=require"}):
            with patch.dict("sys.modules", {"psycopg": psycopg}):
                cursor.fetchone.return_value = ("run-id",)
                self.assertTrue(_claim_once("run-id"))
                cursor.fetchone.return_value = None
                self.assertFalse(_claim_once("run-id"))
        self.assertGreaterEqual(conn.execute.call_count, 10)


class PdfLiveReceiptLedgerContract(unittest.TestCase):
    def test_real_api_fields_saved_not_generated_fake_ids(self):
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import msk_live_acceptance as acceptance
        import msk_sbin_pdf_probe as probe

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SBIN.pdf"
            path.write_bytes(b"%PDF-1.4\nunit-test-only\n")
            receipt = SimpleNamespace(
                message_id=14199,
                document=SimpleNamespace(file_id="telegram-test-file-id")
            )
            bot = SimpleNamespace(send_document=MagicMock(return_value=receipt))
            ctx = {
                "usage_analytics": SimpleNamespace(get_owner_id=lambda: 123456),
                "generate_stock_report_pdf": lambda symbol:
                    (True, str(path), "SBIN"),
                "bot": bot,
                "_record_telegram_api_ack": MagicMock(),
            }
            validator = SimpleNamespace(
                validate_research_pdf=lambda *args, **kwargs:
                    {"pages": 8, "bytes": 26000}
            )
            with patch.dict(os.environ, {"MSK_SBIN_PDF_LIVE_RUN_ID":"proof-run"}):
                with patch.dict("sys.modules", {"pdf_release_gate":validator}):
                    with patch.object(probe.time, "sleep"):
                        with patch.object(acceptance, "_claim_once",return_value=True):
                            with patch.object(acceptance, "_save_action") as save:
                                probe.run_once(ctx)
            self.assertEqual(save.call_args.args,
                             ("sbinpdf-proof-run", "Report"))
            self.assertEqual(save.call_args.kwargs["message_id"],14199)
            self.assertEqual(save.call_args.kwargs["file_id"],
                             "telegram-test-file-id")
            self.assertTrue(save.call_args.kwargs["pdf_delivered"])
            self.assertEqual(save.call_args.kwargs["status"],"UNVERIFIED")


class SelectedFeatureRerunContract(unittest.TestCase):
    def test_unknown_feature_filter_sends_nothing(self):
        import os
        from unittest.mock import patch
        import msk_live_acceptance as acceptance
        ctx = {"bot": object(), "safe_send": lambda uid, msg: None}
        with patch.dict(os.environ, {"MSK_LIVE_ACCEPTANCE_ONLY_FEATURES":
                                     "UnknownFeature"}):
            with patch.object(acceptance, "_claim_action") as claim:
                acceptance._run_actions(ctx, 1234, "test-run")
                claim.assert_not_called()

    def test_selected_feature_avoids_other_button_sends(self):
        import os
        from unittest.mock import patch
        import msk_live_acceptance as acceptance
        output = []
        ctx = {
            "bot": object(),
            "safe_send": lambda uid, msg: output.append(msg),
            "request_index_top5": lambda profile, uid, send:
                                   "🛑 Overnight report withheld",
        }
        with patch.dict(os.environ, {"MSK_LIVE_ACCEPTANCE_ONLY_FEATURES":
                                     "LargeCapTop5"}):
            with patch.object(acceptance,"_claim_action",return_value=True) as claim:
                with patch.object(acceptance,"_save_action") as save:
                    with patch.object(acceptance,"_finish_action",return_value=True):
                        acceptance._run_actions(ctx,1234,"test-run")
        claim.assert_called_once_with("test-run","LargeCapTop5")
        self.assertEqual(len(output),1)
        self.assertEqual(save.call_args.kwargs["status"],"FAIL")


class ResumeSafetyContract(unittest.TestCase):
    def test_restart_resume_uses_durable_claim_before_sending(self):
        import os
        from unittest.mock import MagicMock, patch
        import msk_live_acceptance as acceptance

        connection = MagicMock()
        connection.__enter__.return_value = connection
        # Newly claimed action; there is no old Neon acceptance result.
        claimed = MagicMock()
        claimed.fetchone.return_value = ("LargeCapTop5",)
        old = MagicMock()
        old.fetchone.return_value = None
        connection.execute.side_effect = [claimed, old, MagicMock()]
        psycopg = MagicMock()
        psycopg.connect.return_value = connection
        with patch.dict(os.environ, {"PORTFOLIO_DATABASE_URL":
                                     "postgresql://sample.invalid/neondb?sslmode=require"}):
            with patch.dict("sys.modules", {"psycopg": psycopg}):
                self.assertTrue(acceptance._claim_action("run-v2", "LargeCapTop5"))
        self.assertEqual(connection.execute.call_count, 3)

    def test_interrupted_action_never_replayed(self):
        import os
        from unittest.mock import MagicMock, patch
        import msk_live_acceptance as acceptance

        connection = MagicMock()
        connection.__enter__.return_value = connection
        no_claim = MagicMock()
        no_claim.fetchone.return_value = None
        started = MagicMock()
        started.fetchone.return_value = ("STARTED",)
        connection.execute.side_effect = [
            no_claim, started, MagicMock(), MagicMock(), MagicMock()
        ]
        psycopg = MagicMock()
        psycopg.connect.return_value = connection
        with patch.dict(os.environ, {"PORTFOLIO_DATABASE_URL":
                                     "postgresql://sample.invalid/neondb?sslmode=require"}):
            with patch.dict("sys.modules", {"psycopg": psycopg}):
                self.assertFalse(acceptance._claim_action("run-v2", "StocksBuzz"))
        self.assertEqual(connection.execute.call_count, 5)
        self.assertIn("INTERRUPTED", str(connection.execute.call_args_list))

    def test_successful_record_does_not_trigger_duplicate_send(self):
        import os
        from unittest.mock import MagicMock, patch
        import msk_live_acceptance as acceptance

        connection = MagicMock()
        connection.__enter__.return_value = connection
        claimed = MagicMock()
        claimed.fetchone.return_value = ("Analysis",)
        old = MagicMock()
        old.fetchone.return_value = ("UNVERIFIED", 14165)
        connection.execute.side_effect = [claimed, old, MagicMock(), MagicMock()]
        psycopg = MagicMock()
        psycopg.connect.return_value = connection
        with patch.dict(os.environ, {"PORTFOLIO_DATABASE_URL":
                                     "postgresql://sample.invalid/neondb?sslmode=require"}):
            with patch.dict("sys.modules", {"psycopg": psycopg}):
                self.assertFalse(acceptance._claim_action("run-v2", "Analysis"))
        self.assertIn("SKIP_EXISTING_RESULT", str(connection.execute.call_args_list))


    def test_pdf_probe_run_claim_returns_saved_database_result(self):
        """A recorded claim must return a Boolean, never NameError."""
        import os
        from unittest.mock import MagicMock, patch
        import msk_live_acceptance as acceptance

        def fake_db_result(inserted):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            statements = [MagicMock() for _ in range(5)]
            # Four DDL statements then the INSERT.
            statements[-1].fetchone.return_value = ("test-run",) if inserted else None
            connection.execute.side_effect = statements
            return connection

        for inserted in (True, False):
            with self.subTest(inserted=inserted):
                connection = fake_db_result(inserted)
                psycopg = MagicMock()
                psycopg.connect.return_value = connection
                with patch.dict(os.environ, {
                    "PORTFOLIO_DATABASE_URL": "postgresql://db.invalid/test?sslmode=require"
                }), patch.dict("sys.modules", {"psycopg": psycopg}):
                    self.assertIs(acceptance._claim_once("sbinpdf-test-run"), inserted)


if __name__ == "__main__":
    unittest.main()
