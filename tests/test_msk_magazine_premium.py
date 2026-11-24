"""Premium visual renderer regression; no real Telegram delivery."""
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfReader
from scripts.msk_magazine_monthly import generate
from scripts.msk_magazine_premium import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
EDITORIAL = ROOT / "docs/msk_magazine/ISSUE_2026-11_SOURCE_CITED_WORKING_DRAFT.json"
METRICS = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"
NOW = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)


class PremiumMagazineTests(unittest.TestCase):
    def test_all_article_pages_have_defined_layout_family(self):
        self.assertEqual(set(FAMILIES), set(range(3, 17)))
        self.assertGreaterEqual(len(set(FAMILIES.values())), 4)

    def test_real_source_backed_pdf_has_professional_pages_and_no_publish_gate(self):
        with TemporaryDirectory() as temp:
            result = generate("2026-11", Path(temp), editorial_pack=EDITORIAL,
                              market_pack=METRICS, now=NOW)
            self.assertEqual(result["visual_engine"], "premium_a4_editorial_v1")
            self.assertEqual(result["page_count"], 16)
            self.assertTrue(result["mechanical_pdf_checks"]["passed"],
                            result["mechanical_pdf_checks"]["errors"])
            self.assertFalse(result["publication_allowed"])
            self.assertFalse(result["editorial_approved"])
            reader = PdfReader(str(Path(temp) / "2026-11" / result["draft_pdf"]))
            self.assertEqual(len(reader.pages), 16)
            cover = " ".join((reader.pages[0].extract_text() or "").split())
            self.assertIn("THE MARKET REVIEW", cover)
            self.assertIn("NIFTY RISES. SMALL CAPS FALL.", cover)
            self.assertNotIn("DIWALI SPECIAL", cover)
            self.assertIn("PRIVATE REVIEW COPY", cover)
            contents = " ".join((reader.pages[1].extract_text() or "").split())
            self.assertIn("THE CONTENTS", contents)
            self.assertIn("Editor's Note & Index", contents)
            page_three = " ".join((reader.pages[2].extract_text() or "").split())
            self.assertIn("THE BIG PICTURE", page_three)
            page_four = " ".join((reader.pages[3].extract_text() or "").split())
            self.assertIn("01 / INSIGHT", page_four)
            for i in range(2, 16):
                text = reader.pages[i].extract_text() or ""
                self.assertIn("PRIMARY SOURCES", text, i+1)
                self.assertIn("RISK", text, i+1)
                self.assertIn("UNAPPROVED EDITORIAL DRAFT", text, i+1)
                self.assertNotIn("Design 3 :", text, i+1)
                self.assertGreaterEqual(len(reader.pages[i].get("/Annots", [])),
                                        len(result["pages"][i]["editorial_sources"]))


if __name__ == "__main__":
    unittest.main()
