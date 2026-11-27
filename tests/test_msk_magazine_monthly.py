"""Real tests for MSK monthly finance evidence, scheduling and PDF safety."""
import copy
from datetime import datetime, timezone, date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.msk_magazine_monthly import (
    DEFAULT_MASTER, MagazineError, audit_pdf, first_monday,
    generate, is_monthly_due, issue_for, scheduled_issue_mode, load_master, validate_evidence,
)


class MagazineMonthlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.raw = b"Nifty 50 index closed at 25,144.30 points on 13 November 2026."
        (self.source_root / "market.txt").write_bytes(self.raw)
        self.row = {
            "page": 3, "label": "Nifty 50 close", "value": "25144.30",
            "unit": "index points", "observed_on": "2026-11-13",
            "source_url": "https://www.nseindia.com/market-data/live-equity-market",
            "source_file": "market.txt",
            "source_sha256": hashlib.sha256(self.raw).hexdigest(),
            "source_excerpt": self.raw.decode(),
        }

    def tearDown(self):
        self.temp.cleanup()

    def verify(self, row=None):
        return validate_evidence(
            {"issue": "2026-11", "observations": [row or self.row]},
            "2026-11", self.source_root, date(2026, 11, 16)
        )

    def test_github_archives_monthly_pdf_as_unpublished_draft_only(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/msk-magazine-monthly.yml").read_text()
        self.assertIn('cron: "30 2 * * *"', workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("GITHUB_RUN_ID", workflow)
        self.assertIn("first_monday", workflow)
        self.assertIn("continuation", workflow)
        self.assertIn('TAG="msk-magazine-${ISSUE}-draft"', workflow)
        self.assertNotIn("gh release publish", workflow)
        self.assertNotIn("sendDocument", workflow)
        self.assertNotIn('draft-$GITHUB_RUN_ID', workflow)
        self.assertNotIn("\\\\n", workflow)

    def test_daily_schedule_starts_on_first_monday_and_continues(self):
        first = datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc)
        next_day = datetime(2026, 12, 8, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(scheduled_issue_mode(first, "schedule"),
                         ("2026-12", "first_monday"))
        self.assertEqual(scheduled_issue_mode(next_day, "schedule"),
                         ("2026-12", "continuation"))

    def test_manual_run_allows_current_or_past_issue_but_not_future(self):
        now = datetime(2026, 11, 25, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(scheduled_issue_mode(now, "workflow_dispatch", "2026-10"),
                         ("2026-10", "manual"))
        self.assertEqual(scheduled_issue_mode(now, "workflow_dispatch"),
                         ("2026-11", "manual"))
        with self.assertRaises(MagazineError):
            scheduled_issue_mode(now, "workflow_dispatch", "2026-12")

    def test_manual_schedule_requires_timezone_and_supported_event(self):
        with self.assertRaises(MagazineError):
            scheduled_issue_mode(datetime(2026, 12, 7, 8), "schedule")
        with self.assertRaises(MagazineError):
            scheduled_issue_mode(datetime(2026, 12, 7, 8, tzinfo=timezone.utc),
                                "pull_request")

    def test_december_preview_is_manual_only_and_monthly_waits_for_kickoff(self):
        preview = (Path(__file__).resolve().parents[1] /
                   ".github/workflows/msk-magazine-dec1-private-preview.yml").read_text()
        self.assertIn("MSK Magazine - Dec 1 Private Live Review", preview)
        self.assertIn("workflow_dispatch", preview)
        self.assertNotRegex(preview, r"(?m)^\\s*schedule:")
        preview_day = datetime(2026, 12, 1, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(scheduled_issue_mode(preview_day, "schedule"),
                         ("2026-12", "not_due"))
    def test_daily_schedule_continues_current_month_after_kickoff(self):
        issue, mode = scheduled_issue_mode(
            datetime(2026, 11, 26, 2, 30, tzinfo=timezone.utc), "schedule")
        self.assertEqual(issue, "2026-11")
        self.assertEqual(mode, "continuation")

    def test_first_mondays(self):
        self.assertEqual(first_monday(2026, 11), date(2026, 11, 2))
        self.assertEqual(first_monday(2026, 12), date(2026, 12, 7))

    def test_first_monday_utc_to_ist(self):
        self.assertTrue(is_monthly_due(datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc)))
        self.assertEqual(issue_for(datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc)), "2026-12")

    def test_monday_outside_first_week_rejected(self):
        self.assertFalse(is_monthly_due(datetime(2026, 12, 14, 2, 30, tzinfo=timezone.utc)))

    def test_naive_schedule_rejected(self):
        with self.assertRaises(MagazineError):
            is_monthly_due(datetime(2026, 12, 7, 8))

    def test_master_locked_exactly(self):
        master = load_master()
        self.assertEqual(master["page_count"], 16)
        self.assertEqual([row["page"] for row in master["locked_index"]], list(range(1, 17)))

    def test_index_mutation_rejected(self):
        master = load_master()
        master["locked_index"][5]["page"] = 1
        file = self.root / "bad.json"
        file.write_text(json.dumps(master))
        with self.assertRaises(MagazineError):
            load_master(file)

    def test_financial_source_consistency(self):
        rows, errors = self.verify()
        self.assertFalse(errors)
        self.assertEqual(rows[3][0]["value"], "25144.30")
        self.assertEqual(rows[3][0]["source_file"], "market.txt")

    def test_same_date_conflicting_official_figures_are_withheld(self):
        raw = b"Table Nifty 50: 25,144.30 points. Commentary Nifty 50: 25,144.45 points."
        (self.source_root / "market.txt").write_bytes(raw)
        original = copy.deepcopy(self.row)
        original["source_sha256"] = hashlib.sha256(raw).hexdigest()
        original["source_excerpt"] = "Table Nifty 50: 25,144.30 points."
        second = copy.deepcopy(original)
        second["value"] = "25144.45"
        second["source_excerpt"] = "Commentary Nifty 50: 25,144.45 points."
        by_page, errors = validate_evidence(
            {"issue": "2026-11", "observations": [original, second]},
            "2026-11", self.source_root, date(2026, 11, 16)
        )
        self.assertFalse(by_page[3])
        self.assertTrue(any("conflicting published numbers" in e for e in errors))

    def test_wrong_source_hash_fails(self):
        row = copy.deepcopy(self.row)
        row["source_sha256"] = "a" * 64
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("checksum", errors[0])

    def test_value_not_in_original_excerpt_fails(self):
        row = copy.deepcopy(self.row)
        row["value"] = "25144.45"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("not in", errors[0])

    def test_fake_official_website_fails(self):
        row = copy.deepcopy(self.row)
        row["source_url"] = "https://nseindia.com.evil.example.com/markets"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("official domain", errors[0])

    def test_source_traversal_fails(self):
        row = copy.deepcopy(self.row)
        row["source_file"] = "../outside.txt"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("Observation 1", errors[0])

    def test_excerpt_not_in_document_fails(self):
        row = copy.deepcopy(self.row)
        row["source_excerpt"] = "The index closed at 25144.30"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("excerpt", errors[0])

    def test_future_figure_rejected(self):
        row = copy.deepcopy(self.row)
        row["observed_on"] = "2026-12-01"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("outside accepted window", errors[0])

    def test_missing_source_pack_only_creates_unapproved_draft(self):
        output = generate(
            "2026-12", self.root / "out",
            now=datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc),
            scheduled=True,
        )
        self.assertFalse(output["publication_allowed"])
        self.assertFalse(output["editorial_approved"])
        self.assertFalse(output["logo_asset_provided"])
        self.assertTrue(output["source_failures"])
        self.assertTrue(output["mechanical_pdf_checks"]["passed"])
        pdf = self.root / "out" / "2026-12" / output["draft_pdf"]
        self.assertTrue(pdf.is_file())
        self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(), output["pdf_sha256"])
        self.assertEqual(len(list(pdf.parent.glob("page_*.json"))), 16)

    def test_schedule_not_first_monday_blocks_generation(self):
        with self.assertRaises(MagazineError):
            generate("2026-12", self.root / "out",
                     now=datetime(2026, 12, 14, 2, 30, tzinfo=timezone.utc),
                     scheduled=True)
        self.assertFalse((self.root / "out" / "2026-12").exists())

    def test_schedule_wrong_issue_blocks(self):
        with self.assertRaises(MagazineError):
            generate("2026-11", self.root / "out",
                     now=datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc),
                     scheduled=True)

    def test_real_input_keeps_claim_unapproved(self):
        pack = self.root / "pack.json"
        pack.write_text(json.dumps({"issue":"2026-11","observations":[self.row]}))
        output = generate("2026-11", self.root / "out",
                          source_pack=pack, source_root=self.source_root,
                          now=datetime(2026, 11, 16, 2, 30, tzinfo=timezone.utc))
        self.assertEqual(output["source_consistency_rows"], 1)
        self.assertFalse(output["publication_allowed"])
        self.assertFalse(output["pages"][2]["financial_reviewed"])

    def test_issue_mismatch_never_enters_pdf(self):
        pack = self.root / "pack.json"
        pack.write_text(json.dumps({"issue":"2026-10","observations":[self.row]}))
        with self.assertRaises(MagazineError):
            generate("2026-11", self.root / "out", source_pack=pack,
                     source_root=self.source_root,
                     now=datetime(2026, 11, 16, tzinfo=timezone.utc))

    def test_pdf_tamper_detected(self):
        output = generate("2026-11", self.root / "out",
                          now=datetime(2026, 11, 16, tzinfo=timezone.utc))
        pdf = self.root / "out" / "2026-11" / output["draft_pdf"]
        pdf.write_bytes(b"%PDF-1.0\nfake")
        with self.assertRaises(Exception):
            audit_pdf(pdf, issue="2026-11")

    def test_no_market_data_implies_no_fake_stock_recommendations(self):
        result = generate("2026-12", self.root / "out",
                          now=datetime(2026, 12, 7, tzinfo=timezone.utc))
        self.assertEqual(result["source_consistency_rows"], 0)
        self.assertEqual(result["pages"][8]["observations"], [])
        self.assertFalse(result["publication_allowed"])


if __name__ == "__main__":
    unittest.main()
