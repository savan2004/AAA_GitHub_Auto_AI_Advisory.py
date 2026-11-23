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
    validate_source_receipts,
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

    def test_nse_download_failure_does_not_block_unapproved_draft(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/msk-magazine-monthly.yml").read_text()
        self.assertIn('mkdir -p "$SOURCE_DIR"', workflow)
        self.assertIn('if python scripts/msk_magazine_nse.py', workflow)
        self.assertIn('DOWNLOAD_FAILED.txt', workflow)
        self.assertIn('rm -f "$SOURCE_DIR/source_pack.json"', workflow)
        self.assertIn('Continue only with an UNAPPROVED, data-withheld draft', workflow)
        self.assertIn('Create a 16-page UNAPPROVED monthly draft only', workflow)
        self.assertNotIn('continue-on-error: true', workflow)

    def test_archive_requires_matching_pdf_checksum(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/msk-magazine-monthly.yml").read_text()
        archive = workflow.split("      - name: Archive each run in a durable PRIVATE GitHub draft release", 1)[1]
        self.assertIn('gate.get("pdf_sha256")', archive)
        self.assertIn('hashlib.sha256(pdf.read_bytes()).hexdigest()', archive)
        self.assertIn('Magazine PDF checksum mismatch or missing: archive blocked', archive)
        self.assertLess(archive.index('Magazine PDF checksum mismatch or missing: archive blocked'),
                        archive.index('gh release create'))

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

    def test_stale_prior_year_figure_rejected(self):
        row = copy.deepcopy(self.row)
        row["observed_on"] = "2025-11-13"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("freshness", errors[0])

    def test_stale_prior_month_figure_rejected(self):
        row = copy.deepcopy(self.row)
        row["observed_on"] = "2026-09-01"
        rows, errors = self.verify(row)
        self.assertFalse(rows)
        self.assertIn("freshness", errors[0])

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

    def test_missing_explicit_source_pack_keeps_draft_unapproved(self):
        missing = self.root / "not-downloaded.json"
        result = generate("2026-12", self.root / "out",
                          source_pack=missing, source_root=self.source_root,
                          now=datetime(2026, 12, 7, tzinfo=timezone.utc))
        self.assertEqual(result["source_consistency_rows"], 0)
        self.assertFalse(result["publication_allowed"])
        self.assertTrue(any("Source evidence pack unavailable" in failure
                            for failure in result["source_failures"]))
        self.assertTrue((self.root / "out" / "2026-12" /
                         result["draft_pdf"]).is_file())
        self.assertEqual(len(list((self.root / "out" / "2026-12").glob("page_*.json"))), 16)

    def test_corrupt_source_pack_produces_data_withheld_draft(self):
        pack = self.root / "corrupt.json"
        pack.write_text("{broken JSON", encoding="utf-8")
        result = generate("2026-11", self.root / "out",
                          source_pack=pack, source_root=self.source_root,
                          now=datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertEqual(result["source_consistency_rows"], 0)
        self.assertFalse(result["publication_allowed"])
        self.assertTrue(any("invalid source evidence pack" in item
                            for item in result["source_failures"]))
        self.assertTrue((self.root / "out" / "2026-11" /
                         result["draft_pdf"]).is_file())
        self.assertEqual(len(list((self.root / "out" / "2026-11").glob("page_*.json"))), 16)

    def test_nse_preflight_sources_directory_allows_draft(self):
        # The workflow downloader creates this before the PDF generator runs.
        sources = self.root / "out" / "2026-12" / "sources"
        sources.mkdir(parents=True)
        (sources / "download.txt").write_text("original evidence")
        result = generate("2026-12", self.root / "out",
                          now=datetime(2026, 12, 7, tzinfo=timezone.utc))
        self.assertFalse(result["publication_allowed"])
        self.assertEqual((sources / "download.txt").read_text(), "original evidence")
        self.assertTrue((sources.parent / result["draft_pdf"]).exists())

    def test_existing_issue_draft_is_never_overwritten(self):
        dest = self.root / "out" / "2026-12"
        (dest / "sources").mkdir(parents=True)
        (dest / "release_gate.json").write_text("preserve")
        with self.assertRaises(MagazineError):
            generate("2026-12", self.root / "out",
                     now=datetime(2026, 12, 7, tzinfo=timezone.utc))
        self.assertEqual((dest / "release_gate.json").read_text(), "preserve")

    def test_unrelated_preexisting_issue_directory_is_rejected(self):
        dest = self.root / "out" / "2026-12"
        dest.mkdir(parents=True)
        (dest / "other.txt").write_text("preserve")
        with self.assertRaises(MagazineError):
            generate("2026-12", self.root / "out",
                     now=datetime(2026, 12, 7, tzinfo=timezone.utc))
        self.assertEqual((dest / "other.txt").read_text(), "preserve")

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

    def test_consistent_source_does_not_claim_independent_authenticity(self):
        # Matching local bytes and an official-looking URL are not download proof.
        pack = self.root / "pack.json"
        pack.write_text(json.dumps({"issue": "2026-11", "observations": [self.row]}))
        result = generate("2026-11", self.root / "out",
                          source_pack=pack, source_root=self.source_root,
                          now=datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertEqual(result["source_consistency_rows"], 1)
        self.assertIs(result["source_authenticity_verified"], False)
        self.assertIn("independent", result["source_provenance_blocker"])
        self.assertFalse(result["source_receipt_structurally_valid"])
        self.assertIn("No source_receipts", " ".join(result["source_receipt_validation"]["failures"]))
        gate = json.loads((self.root / "out" / "2026-11" /
                           "release_gate.json").read_text())
        self.assertIs(gate["source_authenticity_verified"], False)
        self.assertIs(gate["publication_allowed"], False)

    def _receipt(self, row=None, *, reviewed=True, fetch_time="2026-11-15T10:00:00Z",
                  review_time="2026-11-15T10:15:00Z"):
        row = row or self.row
        return {
            "issue": "2026-11",
            "source_url": row["source_url"],
            "source_file": row["source_file"],
            "source_sha256": row["source_sha256"],
            "fetched_at_utc": fetch_time,
            "http_status": 200,
            "reviewer_verified": reviewed,
            "reviewer": "Independent reviewer",
            "reviewed_at_utc": review_time,
            "review_notes": "Cross-checked original source and figure against the official release.",
        }

    def test_source_receipt_structure_requires_match_and_review(self):
        accepted, errors = self.verify()
        self.assertFalse(errors)
        pack = {"issue": "2026-11", "source_receipts": [self._receipt()]}
        result = validate_source_receipts(
            pack, accepted, self.source_root, "2026-11", date(2026, 11, 16))
        self.assertTrue(result["passed"])
        self.assertTrue(result["schema_only"])
        self.assertTrue(result["human_independent_authentication_required"])

        unreviewed = self._receipt(reviewed=False)
        result = validate_source_receipts(
            {"issue": "2026-11", "source_receipts": [unreviewed]},
            accepted, self.source_root, "2026-11", date(2026, 11, 16))
        self.assertFalse(result["passed"])
        self.assertIn("reviewer confirmation", " ".join(result["failures"]))

        wrong_hash = self._receipt()
        wrong_hash["source_sha256"] = "0" * 64
        result = validate_source_receipts(
            {"issue": "2026-11", "source_receipts": [wrong_hash]},
            accepted, self.source_root, "2026-11", date(2026, 11, 16))
        self.assertFalse(result["passed"])
        self.assertIn("does not match", " ".join(result["failures"]))

        future = self._receipt(fetch_time="2026-11-17T10:00:00Z",
                               review_time="2026-11-17T10:15:00Z")
        result = validate_source_receipts(
            {"issue": "2026-11", "source_receipts": [future]},
            accepted, self.source_root, "2026-11", date(2026, 11, 16))
        self.assertFalse(result["passed"])
        self.assertIn("after issue evidence cutoff", " ".join(result["failures"]))

    def test_receipt_schema_pass_does_not_claim_source_authenticity(self):
        pack = {"issue": "2026-11", "observations": [self.row],
                "source_receipts": [self._receipt()]}
        pack_path = self.root / "pack_with_receipt.json"
        pack_path.write_text(json.dumps(pack))
        result = generate("2026-11", self.root / "out",
                         source_pack=pack_path, source_root=self.source_root,
                         now=datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertTrue(result["source_receipt_structurally_valid"])
        self.assertTrue(result["source_receipt_validation"]["schema_only"])
        self.assertIs(result["source_authenticity_verified"], False)
        self.assertIs(result["publication_allowed"], False)

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
