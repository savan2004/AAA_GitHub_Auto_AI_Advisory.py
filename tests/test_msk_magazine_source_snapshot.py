"""Negative-path checks for public evidence; does not certify source accuracy."""
import copy
import json
import unittest
from pathlib import Path
from msk_magazine_source_snapshot import EvidenceError, validate_snapshot, publication_observations

PACK = Path(__file__).resolve().parents[1] / "data/msk_magazines/public_snapshot_2026-11.partial.json"

class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.pack = json.loads(PACK.read_text(encoding="utf-8"))

    def test_partial_official_snapshot_is_structurally_valid(self):
        self.assertTrue(validate_snapshot(self.pack))
        self.assertFalse(self.pack["release_approved"])
        self.assertFalse(self.pack["coverage"]["universe_verified"])

    def test_partial_snapshot_cannot_feed_publication(self):
        self.assertTrue(validate_snapshot(self.pack))
        with self.assertRaisesRegex(EvidenceError, "No independently verified"):
            publication_observations(self.pack)

    def test_changing_verified_flag_cannot_bypass_publication_gate(self):
        data = copy.deepcopy(self.pack)
        data["observations"][0]["source_verified"] = True
        with self.assertRaises(EvidenceError):
            publication_observations(data)

    def test_missing_and_fabricated_coverage_rejected(self):
        for coverage in ({}, {"universe_verified": True, "constituents_checked": 400}):
            with self.subTest(coverage=coverage):
                data = copy.deepcopy(self.pack)
                data["coverage"] = coverage
                with self.assertRaises(EvidenceError):
                    validate_snapshot(data)

    def test_future_dates_and_untrusted_hosts_rejected(self):
        for field, value in (("observed_on", "2099-01-01"),
                             ("checked_at", "2099-01-01T10:00:00+05:30"),
                             ("checked_at", "2026-11-09T10:00:00"),
                             ("source_url", "https://example.com/market")):
            with self.subTest(field=field):
                data = copy.deepcopy(self.pack)
                data["observations"][0][field] = value
                with self.assertRaises(EvidenceError):
                    validate_snapshot(data)

    def test_source_claim_and_template_urls_fail_closed(self):
        for field, value in (("source_verified", True),
                             ("source_verified", None),
                             ("source_url", "https://www.nseindia.com/?indexType=%7B0%7D"),
                             ("source_note", "")):
            with self.subTest(field=field):
                data = copy.deepcopy(self.pack)
                data["observations"][0][field] = value
                with self.assertRaises(EvidenceError):
                    validate_snapshot(data)

    def test_official_host_with_unsafe_url_components_rejected(self):
        for url in (
            "https://user:password@www.nseindia.com/report",
            "https://www.nseindia.com:8443/report",
            "https://www.nseindia.com:invalid/report",
            "https://www.nseindia.com/report#unreviewed",
            "https://attacker.example@rbi.org.in/Home.aspx",
        ):
            with self.subTest(url=url):
                data = copy.deepcopy(self.pack)
                data["observations"][0]["source_url"] = url
                with self.assertRaises(EvidenceError):
                    validate_snapshot(data)

    def test_future_same_day_retrieval_rejected(self):
        from datetime import datetime, timedelta, timezone
        data = copy.deepcopy(self.pack)
        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        data["observations"][0]["checked_at"] = future.isoformat()
        with self.assertRaises(EvidenceError):
            validate_snapshot(data)

    def test_invalid_numeric_values_and_release_approval_rejected(self):
        for value in ("NaN", "Infinity", None, True):
            with self.subTest(value=value):
                data = copy.deepcopy(self.pack)
                data["observations"][0]["value"] = value
                with self.assertRaises(EvidenceError):
                    validate_snapshot(data)
        data = copy.deepcopy(self.pack)
        data["release_approved"] = True
        with self.assertRaises(EvidenceError):
            validate_snapshot(data)

if __name__ == "__main__":
    unittest.main()
