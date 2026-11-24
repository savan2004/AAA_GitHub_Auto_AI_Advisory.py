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

    def test_final_close_and_intraday_are_distinguished(self):
        page = json.loads(MASTER.read_text(encoding="utf-8"))["locked_index"][4]
        self.assertEqual(self.pack["page"], 5)
        self.assertEqual(self.pack["locked_title"], page["title"])
        self.assertEqual(self.pack["layout"], page["layout"])
        self.assertEqual(self.pack["status"], "PROVISIONAL_SECONDARY_ONLY_OFFICIAL_SOURCE_WITHHELD")
        self.assertIs(self.pack["official_source_bytes_downloaded"], False)
        self.assertIsNone(self.pack["official_source_sha256"])
        self.assertIs(self.pack["independent_review"], False)
        self.assertIs(self.pack["magazine_assembly_allowed"], False)
        self.assertNotIn("close_disputed", self.pack["reported_ohlc_secondary"])
        values = [row["value"] for row in self.pack["close_reports"]]
        self.assertEqual(len(values), 6)
        self.assertEqual(set(values), {25349.65})
        intraday = self.pack["intraday_observation"]
        self.assertEqual(intraday["classification"], "INTRADAY_NOT_CLOSE")
        self.assertEqual(intraday["time"], "15:15 IST")
        self.assertEqual(intraday["value"], 25366.4)
        self.assertAlmostEqual(intraday["value"] - values[0], 16.75)
        self.assertNotIn(intraday["value"], values)
        self.assertEqual(self.pack["close_history_secondary"][-1]["close"], 25349.65)
        self.assertTrue(self.pack["no_forecast_or_recommendation"])
        self.assertRegex(self.pack["draft_pdf_sha256"], r"^[a-f0-9]{64}$")
        attempt = self.pack["primary_source_fetch_attempt"]
        self.assertTrue(attempt["network_blocked"])
        self.assertEqual(attempt["http_status"], 0)
        self.assertEqual(attempt["bytes_received"], 0)
        self.assertIn("curl (6) Could not resolve host", attempt["error"])
        research = (RESEARCH / "page05_nifty_technical_outlook.md").read_text(encoding="utf-8")
        self.assertIn("six secondary reports agree", research.lower())
        self.assertIn("15:15 ist intraday update", research.lower())
        self.assertIn("not the final close", research.lower())
        self.assertIn("curl error 6; http 000; 0 bytes", research.lower())
        self.assertIn("official source; retrieval blocked", research.lower())
        pdf_sha = re.search(r"PDF SHA-256: ([a-f0-9]{64})", research)
        self.assertIsNotNone(pdf_sha)
        self.assertEqual(pdf_sha.group(1), self.pack["draft_pdf_sha256"])

    def test_pdf_reproducible_one_page_and_cited(self):
        with tempfile.TemporaryDirectory() as tmp:
            out1 = Path(tmp) / "page05-a.pdf"
            out2 = Path(tmp) / "page05-b.pdf"
            sha1 = generate_page05(RESEARCH / "page05_source_reconciliation.json", out1)
            sha2 = generate_page05(RESEARCH / "page05_source_reconciliation.json", out2)
            self.assertRegex(sha1, r"^[a-f0-9]{64}$")
            self.assertEqual(sha1, sha2)
            reader = PdfReader(str(out1))
            self.assertEqual(len(reader.pages), 1)
            self.assertGreaterEqual(len(reader.pages[0].get("/Annots") or []), 9)
            text = reader.pages[0].extract_text() or ""
            for phrase in ("NIFTY 50 TECHNICAL OUTLOOK", "SECONDARY CLOSE REPORTS AGREE", "15:15 IST INTRADAY", "NOT CLOSE", "UNAPPROVED"):
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
