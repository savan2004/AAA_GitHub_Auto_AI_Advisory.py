"""Release-fence regression: fictional self-approved JSON cannot reach customers."""
import json
import tempfile
import unittest
from datetime import date
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import msk_ai_builder_research_release as release
from msk_ai_builder_automatic import ResearchUnavailable, approved_plan, automatic_available
from msk_ai_builder_profile import ResearchProfile
from test_msk_ai_builder_automatic import fixture, flags, SESSION


class AuditedReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps(fixture())
        self.profile = ResearchProfile(1_000_000, "medium", 36)

    def test_customer_forged_approved_labels_are_denied_without_release(self):
        with self.assertRaisesRegex(ResearchUnavailable, "NO_VERIFIED_PORTFOLIO_AVAILABLE"):
            approved_plan(self.profile, self.raw, latest_session=SESSION)
        self.assertFalse(automatic_available(environ=flags()))

    def test_registry_not_enabled_even_when_sha_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": 1, "publication_status": "WITHHELD",
                "approved": []}), encoding="utf-8")
            with patch.object(release, "_REGISTRY", registry):
                with self.assertRaises(release.UnreviewedResearch):
                    release.require_reviewed_research(
                        self.raw, latest_session=SESSION, companies=fixture()["companies"])

    def test_audited_artifact_requires_exact_bytes_and_issuer_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audits = root / "audits"
            audits.mkdir()
            rows = fixture()["companies"]
            audit = {
                "schema_version": 1, "close_date": SESSION.isoformat(),
                "review_status": "PRIMARY_DOCUMENTS_RECONCILED",
                "companies": [
                    {"symbol": r["symbol"], "isin": r["isin"],
                     "filing_sha256": r["filing_sha256"],
                     "close_rupees": r["close_rupees"]} for r in rows],
            }
            raw_audit = json.dumps(audit, sort_keys=True).encode("utf-8")
            audit_digest = sha256(raw_audit).hexdigest()
            (audits / (audit_digest + ".json")).write_bytes(raw_audit)
            registry = root / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": 1, "publication_status": "APPROVED",
                "approved": [{
                    "artifact_sha256": sha256(self.raw.encode("utf-8")).hexdigest(),
                    "close_date": SESSION.isoformat(),
                    "audit_sha256": audit_digest,
                    "regulatory_review_id": "OFFLINE_TEST_ONLY_0001"}]}), encoding="utf-8")
            with patch.object(release, "_REGISTRY", registry), patch.object(release, "_AUDITS", audits):
                # This verifies ONLY that a committed audit has matching bytes,
                # not that these mock issuer values came from the real NSE.
                self.assertTrue(release.require_reviewed_research(
                    self.raw, latest_session=SESSION, companies=rows))
                with self.assertRaises(release.UnreviewedResearch):
                    release.require_reviewed_research(
                        self.raw + " ", latest_session=SESSION, companies=rows)
                bad_rows = [dict(row) for row in rows]
                bad_rows[0]["isin"] = "INE123456789"
                with self.assertRaises(release.UnreviewedResearch):
                    release.require_reviewed_research(
                        self.raw, latest_session=SESSION, companies=bad_rows)
                (audits / (audit_digest + ".json")).write_bytes(b"{}")
                with self.assertRaises(release.UnreviewedResearch):
                    release.require_reviewed_research(
                        self.raw, latest_session=SESSION, companies=rows)


if __name__ == "__main__":
    unittest.main()
