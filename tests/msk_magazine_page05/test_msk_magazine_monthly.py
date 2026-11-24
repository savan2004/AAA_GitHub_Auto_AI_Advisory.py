"""Page 05 secondary-source reconciliation and PDF renderer must remain fail-closed."""
import json
import re
from pathlib import Path
import tempfile
import unittest
from pypdf import PdfReader
from scripts.msk_magazine_page05_draft import generate_page05

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "docs/msk_magazine/research/2026-11"
MASTER = ROOT / "docs/msk_magazine/LOCKED_DESIGN3_INDEX_MASTER.json"


class Page05SourceReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.pack = json.loads((RESEARCH / "page05_source_reconciliation.json").read_text(encoding="utf-8"))

    def test_close_conflict_stays_withheld_from_magazine(self):
        page = json.loads(MASTER.read_text(encoding="utf-8"))["locked_index"][4]
        self.assertEqual(self.pack["page"], 5)
        self.assertEqual(self.pack["locked_title"], page["title"])
        self.assertEqual(self.pack["layout"], page["layout"])
        self.assertEqual(self.pack["status"], "PROVISIONAL_SECONDARY_DATA_CONFLICT_WITHHELD")
        self.assertIs(self.pack["official_source_bytes_downloaded"], False)
        self.assertIsNone(self.pack["official_source_sha256"])
        self.assertIs(self.pack["independent_review"], False)
        self.assertIs(self.pack["magazine_assembly_allowed"], False)
        attempt = self.pack["primary_source_fetch_attempt"]
        self.assertTrue(attempt["attempted"])
        self.assertEqual(attempt["http_status"], 0)
        self.assertEqual(attempt["bytes_received"], 0)
        self.assertTrue(attempt["network_blocked"])
        self.assertIn("curl (6) Could not resolve host", attempt["error"])
        values = [row["value"] for row in self.pack["close_reports"]]
        self.assertEqual(values.count(25349.65), 3)
        self.assertEqual(values.count(25366.4), 1)
        self.assertAlmostEqual(max(values) - min(values), 16.75)
        self.assertTrue(self.pack["no_forecast_or_recommendation"])
        self.assertRegex(self.pack["draft_pdf_sha256"], r"^[a-f0-9]{64}$")
        research = (RESEARCH / "page05_nifty_technical_outlook.md").read_text(encoding="utf-8")
        self.assertIn("withheld from assembly", research.lower())
        self.assertIn("16.75-point discrepancy", research.lower())
        self.assertIn("curl error 6; http 000; 0 bytes", research.lower())
        self.assertIn("official source; retrieval blocked", research.lower())
        pdf_sha = re.search(r"PDF SHA-256: ([a-f0-9]{64})", research)
        self.assertIsNotNone(pdf_sha)
        self.assertEqual(pdf_sha.group(1), self.pack["draft_pdf_sha256"])

    def test_pdf_is_reproducible_one_page_and_cited(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "page05.pdf"
            sha = generate_page05(RESEARCH / "page05_source_reconciliation.json", out)
            self.assertEqual(sha, self.pack["draft_pdf_sha256"])
            reader = PdfReader(str(out))
            self.assertEqual(len(reader.pages), 1)
            self.assertGreaterEqual(len(reader.pages[0].get("/Annots") or []), 6)
            text = reader.pages[0].extract_text() or ""
            for phrase in ("NIFTY 50 TECHNICAL OUTLOOK", "DATA STATUS", "CLOSE DISPUTED", "UNAPPROVED"):
                self.assertIn(phrase, text)

    def test_renderer_rejects_assembly_allowed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "manifest.json"
            modified = dict(self.pack)
            modified["magazine_assembly_allowed"] = True
            invalid.write_text(json.dumps(modified), encoding="utf-8")
            with self.assertRaises(ValueError):
                generate_page05(invalid, Path(tmp) / "blocked.pdf")


if __name__ == "__main__":
    unittest.main()
