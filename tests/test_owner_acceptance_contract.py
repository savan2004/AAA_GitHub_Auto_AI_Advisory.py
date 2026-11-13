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
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_once")
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

if __name__ == "__main__":
    unittest.main()
