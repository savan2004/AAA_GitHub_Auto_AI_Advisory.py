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
        values = [row["value"] for row in pack["close_reports"]]
        self.assertEqual(values.count(25349.65), 3)
        self.assertEqual(values.count(25366.4), 1)
        self.assertAlmostEqual(max(values) - min(values), 16.75)
        self.assertTrue(pack["no_forecast_or_recommendation"])
        research = (RESEARCH / "page05_nifty_technical_outlook.md").read_text(encoding="utf-8")
        self.assertIn("withheld from assembly", research.lower())
        self.assertIn("conflicting close", research.lower())
        self.assertIn("official primary source", research.lower())


if __name__ == "__main__":
    unittest.main()
