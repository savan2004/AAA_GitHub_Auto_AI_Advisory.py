"""Full authored editorial issue: draft-only end-to-end regression."""
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfReader

from scripts.msk_magazine_editorial import EditorialPackError, load_editorial_pack
from scripts.msk_magazine_monthly import generate
from scripts.msk_magazine_charts import load_metrics, ResearchMetricError

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "docs/msk_magazine/ISSUE_2026-11_SOURCE_CITED_WORKING_DRAFT.json"
METRICS = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


class CompleteEditorialDraftTests(unittest.TestCase):
    def test_all_fourteen_sections_are_distinct_and_cited(self):
        pages = load_editorial_pack(PACK, "2026-11", as_of=date(2026, 11, 23))
        self.assertEqual(sorted(pages), list(range(3, 17)))
        self.assertEqual(len({p["headline"] for p in pages.values()}), 14)
        self.assertTrue(all(p["sources"] for p in pages.values()))

    def test_full_sixteen_page_pdf_contains_authored_analyses(self):
        with TemporaryDirectory() as temp:
            result = generate("2026-11", Path(temp),
                              editorial_pack=PACK, market_pack=METRICS, now=NOW)
            self.assertEqual(result["page_count"], 16)
            self.assertEqual(result["editorial_pages_authored"], 14)
            self.assertEqual(result["chart_pages_with_dated_evidence"], 14)
            self.assertTrue(result["mechanical_pdf_checks"]["passed"])
            self.assertFalse(result["publication_allowed"])
            self.assertFalse(result["editorial_approved"])
            reader = PdfReader(str(Path(temp) / "2026-11" / result["draft_pdf"]))
            self.assertEqual(len(reader.pages), 16)
            self.assertIn("Higher inflation raises", reader.pages[2].extract_text())
            self.assertIn("Research method, omissions", reader.pages[15].extract_text())
            self.assertIn("Nifty 50", reader.pages[4].extract_text())
            self.assertIn("TCS", reader.pages[11].extract_text())
            self.assertIn("+18.06%", reader.pages[7].extract_text())
            self.assertIn("UNAPPROVED EDITORIAL DRAFT", reader.pages[15].extract_text())

    def test_research_metrics_are_dated_and_numbered(self):
        metrics = load_metrics(METRICS, "2026-11")
        idx = {x["name"]: x for x in metrics["market_indices"]}
        self.assertEqual(idx["Nifty 50"]["m1"], 1.95)
        self.assertEqual(idx["Nifty Smallcap 250"]["m1"], -5.78)
        self.assertEqual(idx["Nifty 500 Equal Weight"]["m1"], -5.12)
        self.assertEqual(
            metrics["market_source"]["report_date"], "2026-10-31"
        )

    def test_future_evidence_date_blocks_artifact_creation(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                generate("2026-11", Path(temp),
                         market_pack=METRICS,
                         now=datetime(2026, 11, 20, tzinfo=timezone.utc))
            self.assertFalse((Path(temp) / "2026-11").exists())

    def test_missing_page_rejected_before_output_creation(self):
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        pack["pages"].pop()
        with TemporaryDirectory() as temp:
            bad = Path(temp) / "invalid.json"
            bad.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaises(EditorialPackError):
                generate("2026-11", Path(temp) / "out",
                         editorial_pack=bad, now=NOW)
            self.assertFalse((Path(temp) / "out" / "2026-11").exists())

    def test_unapproved_source_host_rejected(self):
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        pack["pages"][0]["sources"][0]["url"] = "https://example.com/market.pdf"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaises(EditorialPackError):
                load_editorial_pack(path, "2026-11", as_of=date(2026, 11, 23))

    def test_future_dated_source_rejected(self):
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        pack["pages"][0]["sources"][0]["published_on"] = "2026-12-01"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaises(EditorialPackError):
                load_editorial_pack(path, "2026-11", as_of=date(2026, 11, 23))

    def test_repeated_research_is_rejected(self):
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        pack["pages"][1]["lead"] = pack["pages"][0]["lead"]
        pack["pages"][1]["analysis"] = copy.deepcopy(pack["pages"][0]["analysis"])
        with TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaises(EditorialPackError):
                load_editorial_pack(path, "2026-11", as_of=date(2026, 11, 23))


if __name__ == "__main__":
    unittest.main()
