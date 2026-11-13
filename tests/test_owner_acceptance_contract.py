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

if __name__ == "__main__":
    unittest.main()
