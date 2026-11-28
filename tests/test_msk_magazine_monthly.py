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
    generate, is_monthly_due, issue_for, load_master, validate_evidence,
    validate_source_receipts, validate_handoff_manifest,
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
        self.assertIn('cron: "30 2 * * 1"', workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("GITHUB_RUN_ID", workflow)
        self.assertNotIn("gh release publish", workflow)
        self.assertNotIn("sendDocument", workflow)

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

    def test_candidate_only_dry_run_snapshot_is_not_financial_evidence(self):
        # Mirrors the metadata/candidate-only shape of an unapproved dry-run
        # snapshot; candidate rows are not dated, source-matched observations.
        pack = {
            "issue": "2026-11",
            "report": {"publishable": False, "dry_run": True},
            "candidates": [{"symbol": "EXAMPLE", "price": 123.45}],
        }
        rows, errors = validate_evidence(
            pack, "2026-11", self.source_root, date(2026, 11, 27)
        )
        self.assertEqual(rows, {})
        self.assertIn("No financial observations supplied", " ".join(errors))

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

    def _receipt(self, row=None, *, fetch_time="2026-11-16T10:00:00Z",
                  review_time="2026-11-16T10:15:00Z"):
        row = row or self.row
        return {
            "issue": "2026-11",
            "source_url": row["source_url"],
            "source_file": row["source_file"],
            "source_sha256": row["source_sha256"],
            "fetched_at_utc": fetch_time,
            "http_status": 200,
            "reviewer_verified": True,
            "reviewer": "Test-only reviewer",
            "reviewed_at_utc": review_time,
            "review_notes": "Test fixture cross-check; not real evidence.",
        }


    def _handoff(self):
        return {
            "issue": "2026-11",
            "source_project": "AiAutoUpdate",
            "source_chat": "AutoAiAdvisory (SK)",
            "source_message_id": 12345,
            "published_at": "2026-11-27T12:00:00Z",
            "as_of": "2026-11-27",
            "approved_by": "Test-only source owner",
            "approval_timestamp": "2026-11-27T12:15:00Z",
            "source_document_ids": ["test-document-001"],
            "source_document_urls": [],
            "coverage": {"universe_size": 10, "verified_count": 8, "missing_count": 2},
            "data_failures": ["Two rows intentionally missing in this test fixture"],
            "editorial_signoff": True,
        }

    def test_handoff_manifest_requires_complete_edition_receipt(self):
        valid = validate_handoff_manifest(
            {"handoff_receipt": self._handoff()}, "2026-11",
            validation_time=datetime(2026, 11, 28, 3, 30, tzinfo=timezone.utc))
        self.assertTrue(valid["passed"])
        self.assertTrue(valid["schema_only"])
        self.assertTrue(valid["human_independent_authentication_required"])
        self.assertTrue(valid["partial_coverage"])
        self.assertEqual(valid["coverage"]["missing_count"], 2)

        for field in ("source_message_id", "approved_by", "coverage", "editorial_signoff"):
            with self.subTest(missing=field):
                receipt = self._handoff()
                receipt.pop(field)
                rejected = validate_handoff_manifest(
                    {"handoff_receipt": receipt}, "2026-11",
                    validation_time=datetime(2026, 11, 28, 3, 30, tzinfo=timezone.utc))
                self.assertFalse(rejected["passed"])

    def test_handoff_manifest_rejects_future_dates_and_bad_coverage(self):
        receipt = self._handoff()
        receipt["approval_timestamp"] = "2026-11-29T00:00:00Z"
        future = validate_handoff_manifest(
            {"handoff_receipt": receipt}, "2026-11",
            validation_time=datetime(2026, 11, 28, 3, 30, tzinfo=timezone.utc))
        self.assertFalse(future["passed"])
        self.assertIn("future", " ".join(future["failures"]).casefold())

        receipt = self._handoff()
        receipt["coverage"]["verified_count"] = 9
        unbalanced = validate_handoff_manifest(
            {"handoff_receipt": receipt}, "2026-11",
            validation_time=datetime(2026, 11, 28, 3, 30, tzinfo=timezone.utc))
        self.assertFalse(unbalanced["passed"])
        self.assertIn("coverage", " ".join(unbalanced["failures"]).casefold())

    def test_handoff_structure_does_not_authenticate_financial_data(self):
        pack = {"issue": "2026-11", "observations": [],
                "handoff_receipt": self._handoff()}
        pack_path = self.root / "handoff_only_pack.json"
        pack_path.write_text(json.dumps(pack), encoding="utf-8")
        output = generate(
            "2026-11", self.root / "out", source_pack=pack_path,
            source_root=self.source_root,
            now=datetime(2026, 11, 28, 3, 30, tzinfo=timezone.utc))
        self.assertTrue(output["source_handoff_structurally_valid"])
        self.assertFalse(output["source_authenticity_verified"])
        self.assertFalse(output["editorial_approved"])
        self.assertFalse(output["publication_allowed"])
        self.assertEqual(output["source_handoff_validation"]["coverage"]["missing_count"], 2)


    def test_future_receipt_timestamp_is_rejected(self):
        accepted, errors = self.verify()
        self.assertFalse(errors)
        receipt = self._receipt(fetch_time="2026-11-16T18:00:00Z",
                                review_time="2026-11-16T18:15:00Z")
        gate = validate_source_receipts(
            {"source_receipts": [receipt]}, accepted, self.source_root,
            "2026-11", date(2026, 11, 16),
            validation_time=datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc))
        self.assertFalse(gate["passed"])
        self.assertIn("future relative to validation", " ".join(gate["failures"]))

    def test_receipt_schema_pass_still_withholds_authenticity(self):
        pack = {"issue": "2026-11", "observations": [self.row],
                "source_receipts": [self._receipt()]}
        pack_path = self.root / "pack_with_receipt.json"
        pack_path.write_text(json.dumps(pack), encoding="utf-8")
        output = generate("2026-11", self.root / "out",
                         source_pack=pack_path, source_root=self.source_root,
                         now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertTrue(output["source_receipt_structurally_valid"])
        self.assertIs(output["source_authenticity_verified"], False)
        self.assertIs(output["publication_allowed"], False)
        self.assertTrue(output["source_receipt_validation"]["schema_only"])

    def test_missing_receipt_fails_closed_for_authenticity(self):
        pack = {"issue": "2026-11", "observations": [self.row]}
        pack_path = self.root / "pack_without_receipt.json"
        pack_path.write_text(json.dumps(pack), encoding="utf-8")
        output = generate("2026-11", self.root / "out",
                         source_pack=pack_path, source_root=self.source_root,
                         now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertFalse(output["source_receipt_structurally_valid"])
        self.assertIs(output["source_authenticity_verified"], False)
        self.assertIs(output["publication_allowed"], False)
        self.assertIn("No source_receipts", " ".join(output["source_receipt_validation"]["failures"]))

    def test_corrupt_source_pack_generates_data_withheld_draft(self):
        pack_path = self.root / "corrupt_source_pack.json"
        pack_path.write_text("{bad JSON", encoding="utf-8")
        output = generate("2026-11", self.root / "out",
                         source_pack=pack_path, source_root=self.source_root,
                         now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(output["source_consistency_rows"], 0)
        self.assertFalse(output["publication_allowed"])
        self.assertTrue(any("invalid source evidence pack" in item
                           for item in output["source_failures"]))
        pdf = self.root / "out" / "2026-11" / output["draft_pdf"]
        self.assertTrue(pdf.is_file())

    def test_missing_source_directory_still_generates_withheld_draft(self):
        pack = self.root / "pack_with_missing_source_directory.json"
        pack.write_text(json.dumps({
            "issue": "2026-11", "observations": [self.row],
            "source_receipts": [],
        }), encoding="utf-8")
        missing_root = self.root / "missing-source-directory"
        self.assertFalse(missing_root.exists())

        output = generate(
            "2026-11", self.root / "out",
            source_pack=pack, source_root=missing_root,
            now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(output["source_consistency_rows"], 0)
        self.assertFalse(output["source_receipt_structurally_valid"])
        self.assertFalse(output["source_authenticity_verified"])
        self.assertFalse(output["editorial_approved"])
        self.assertFalse(output["publication_allowed"])
        self.assertIn("Source evidence directory unavailable",
                      " ".join(output["source_failures"]))
        self.assertFalse(output["source_receipt_validation"]["passed"])
        pdf = self.root / "out" / "2026-11" / output["draft_pdf"]
        self.assertTrue(pdf.is_file())
        self.assertTrue(output["mechanical_pdf_checks"]["passed"])

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
