"""Page 05 secondary-source reconciliation must remain fail-closed."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "docs/msk_magazine/research/2026-11"
MASTER = ROOT / "docs/msk_magazine/LOCKED_DESIGN3_INDEX_MASTER.json"


class Page05SourceReconciliationTests(unittest.TestCase):
    def test_close_conflict_stays_withheld_from_magazine(self):
        pack = json.loads((RESEARCH / "page05_source_reconciliation.json").read_text(encoding="utf-8"))
        master = json.loads(MASTER.read_text(encoding="utf-8"))
        page = master["locked_index"][4]
        self.assertEqual(pack["page"], 5)
        self.assertEqual(pack["locked_title"], page["title"])
        self.assertEqual(pack["layout"], page["layout"])
        self.assertEqual(pack["status"], "PROVISIONAL_SECONDARY_DATA_CONFLICT_WITHHELD")
        self.assertIs(pack["official_source_bytes_downloaded"], False)
        self.assertIsNone(pack["official_source_sha256"])
        self.assertIs(pack["independent_review"], False)
        self.assertIs(pack["magazine_assembly_allowed"], False)
        attempt = pack["primary_source_fetch_attempt"]
        self.assertTrue(attempt["attempted"])
        self.assertEqual(attempt["http_status"], 0)
        self.assertEqual(attempt["bytes_received"], 0)
        self.assertTrue(attempt["network_blocked"])
        self.assertIn("curl (6) Could not resolve host", attempt["error"])
        values = [row["value"] for row in pack["close_reports"]]
        self.assertEqual(values.count(25349.65), 3)
        self.assertEqual(values.count(25366.4), 1)
        self.assertAlmostEqual(max(values) - min(values), 16.75)
        self.assertTrue(pack["no_forecast_or_recommendation"])
        self.assertRegex(pack["draft_pdf_sha256"], r"^[a-f0-9]{64}$")
        research = (RESEARCH / "page05_nifty_technical_outlook.md").read_text(encoding="utf-8")
        self.assertIn("withheld from assembly", research.lower())
        self.assertIn("16.75-point discrepancy", research.lower())
        self.assertIn("official source; retrieval blocked", research.lower())


if __name__ == "__main__":
    unittest.main()
