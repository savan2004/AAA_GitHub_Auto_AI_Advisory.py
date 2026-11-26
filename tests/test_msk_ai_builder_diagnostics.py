"""Offline regression for fast per-company source diagnostics and safe artifacts."""
import contextlib
import io
import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock, patch

from msk_ai_builder_diagnostics import (
    classify_failure, diagnose_quarter, diagnostic_summary,
    diagnostic_markdown, main, ai_source_hints,
)
from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_filings import DownloadedFiling
from test_msk_ai_builder_quarter_check import QuarterCheckTests
from test_msk_ai_builder_filings import MEMBER, NOW, URL, fixture


class ResearchFailureDiagnostics(unittest.TestCase):
    def inputs(self):
        return QuarterCheckTests().setup_inputs()

    def test_match_is_only_quarter_and_never_publication(self):
        snap, listing, download = self.inputs()
        result = diagnose_quarter(
            MEMBER, snap, listing=listing, download=download, clock=lambda: NOW)
        self.assertEqual(result["status"], "LATEST_QUARTER_MATCHED_ONLY")
        self.assertEqual(result["stage"], "LATEST_QUARTER_ONLY")
        self.assertEqual(result["listing_total"], 1)
        self.assertEqual(result["publication_status"], "WITHHELD")
        self.assertFalse(diagnostic_summary([result], requested=20)["rehearsal_passed"])

    def test_zero_listing_captures_precise_company_stage_and_next_action(self):
        snap, listing, download = self.inputs()
        listing.return_value = {"totalCount": 0, "data": []}
        result = diagnose_quarter(
            MEMBER, snap, listing=listing, download=download, clock=lambda: NOW)
        self.assertEqual(result["status"], "FILING_METADATA_COVERAGE")
        self.assertEqual(result["stage"], "DISCOVERY")
        self.assertEqual(result["listing_total"], 0)
        self.assertEqual(result["listing_rows"], 0)
        self.assertIn("official", result["next_action"])
        download.assert_not_called()
        summary = diagnostic_summary([result], requested=20)
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(summary["failure_counts_by_stage"], {"DISCOVERY": 1})
        self.assertIn("FIXTURE", diagnostic_markdown(summary))
        self.assertIn("FILING_METADATA_COVERAGE", diagnostic_markdown(summary))
        self.assertEqual(summary["publication_status"], "WITHHELD")

    def test_ai_only_proposes_allowlisted_sources_without_paid_fallback(self):
        from types import SimpleNamespace
        from datetime import date
        url = "https://www.bseindia.com/XBRLFILES/filing.xml"
        request = Mock(return_value=SimpleNamespace(ok=True, text=json.dumps([
            url, "https://evil.example/filing.xml",
            "https://www.bseindia.com/XBRLFILES/../private.xml"])))
        self.assertEqual(ai_source_hints("MCX", date(2026,9,30), "consolidated",
                         environ={}, request=request)["status"], "DISABLED")
        request.assert_not_called()
        result = ai_source_hints("MCX", date(2026,9,30), "consolidated",
                         environ={"MSK_BUILDER_AI_SOURCE_HINTS":"1",
                                  "GROQ_API_KEY":"fixture", "MSK_AI_ALLOW_PAID":"1",
                                  "OPENAI_API_KEY":"not-forwarded"}, request=request)
        self.assertEqual(result, {"status":"UNVERIFIED_SOURCE_HINTS","urls":[url]})
        self.assertEqual(request.call_args.kwargs["env"], {"GROQ_API_KEY":"fixture"})

    def test_ai_recovery_never_turns_missing_listing_into_pass(self):
        snap, listing, download = self.inputs()
        listing.return_value = {"totalCount":0,"data":[]}
        hint = {"status":"UNVERIFIED_SOURCE_HINTS","urls":["https://www.bseindia.com/XBRLFILES/filing.xml"]}
        with patch.dict("os.environ", {"MSK_BUILDER_AI_SOURCE_HINTS":"1"}), \
             patch("msk_ai_builder_diagnostics.ai_source_hints", return_value=hint) as ai:
            result = diagnose_quarter(MEMBER, snap, listing=listing, download=download, clock=lambda: NOW)
            ai.assert_called_once()
        self.assertEqual(result["ai_source_hints"],hint)
        self.assertEqual(result["status"],"FILING_METADATA_COVERAGE")
        self.assertEqual(result["publication_status"],"WITHHELD")
        self.assertFalse(diagnostic_summary([result],requested=1)["rehearsal_passed"])
        download.assert_not_called()

    def test_numeric_revenue_disagreement_shows_comparison_stage(self):
        snap, listing, download = self.inputs()
        changed = replace(snap, quarterly=snap.quarterly[:-1] +
                          (replace(snap.quarterly[-1], revenue_cr=Decimal("999")),))
        result = diagnose_quarter(
            MEMBER, changed, listing=listing, download=download, clock=lambda: NOW)
        self.assertEqual(result["status"], "QUARTER_CHECK_VALUE_MISMATCH")
        self.assertEqual(result["stage"], "NUMERIC_COMPARISON")
        self.assertIn("do not alter", result["next_action"])
        self.assertEqual(result["publication_status"], "WITHHELD")

    def test_source_schema_missing_field_reports_parser_stage(self):
        snap, listing, download = self.inputs()
        download.return_value = DownloadedFiling(
            URL, fixture().replace(b"Revenue from operations", b"Unknown revenue"), NOW)
        result = diagnose_quarter(
            MEMBER, snap, listing=listing, download=download, clock=lambda: NOW)
        self.assertEqual(result["status"], "FILING_FINANCIAL_LAYOUT")
        self.assertEqual(result["stage"], "PARSER_LAYOUT")
        self.assertIn("income-statement", result["next_action"])

    def test_reason_codes_never_echo_secrets_or_raw_exceptions(self):
        suspect = EvidenceError("https://example.org/private?api_key=should-never-print")
        result = classify_failure(suspect)
        self.assertEqual(result["reason_code"], "UNRECOGNIZED_EVIDENCE_ERROR")
        self.assertNotIn("api_key", json.dumps(result))
        self.assertEqual(
            classify_failure(EvidenceError("FILING_MISSING_OR_AMBIGUOUS_FIELD_TOTAL_INCOME"))["stage"],
            "PARSER_FIELD")
        self.assertEqual(
            classify_failure(EvidenceError("BANK_INCOME_RECONCILIATION"))["stage"],
            "BANK_RECONCILIATION")

    def test_report_keeps_per_company_failures_and_no_approval(self):
        matched = {"symbol": "TESTPASS", "status": "LATEST_QUARTER_MATCHED_ONLY",
                   "publication_status": "WITHHELD"}
        mcx = {"symbol": "TESTMISS", "status": "FILING_METADATA_COVERAGE",
               "stage": "DISCOVERY", "next_action": "Inspect issuer filing",
               "listing_total": 0, "listing_rows": 0,
               "publication_status": "WITHHELD"}
        result = diagnostic_summary([matched, mcx], requested=2)
        self.assertEqual((result["matched"], result["checked"]), (1, 2))
        self.assertFalse(result["rehearsal_passed"])
        self.assertEqual(result["failures"][0]["symbol"], "TESTMISS")
        self.assertNotIn("approved", json.dumps(result).lower())
        self.assertIn("TESTMISS", diagnostic_markdown(result))
        self.assertEqual(
            diagnostic_summary([matched], requested=1)["rehearsal_passed"], True)
        self.assertEqual(
            diagnostic_summary([matched], requested=2)["rehearsal_passed"], False)

    def test_single_symbol_diagnostic_avoids_full_bank_fetch_and_preserves_gate(self):
        payload = {"totalCount": 0, "data": []}
        buff = io.StringIO()
        with patch("msk_ai_builder_diagnostics.fetch_listing", return_value=payload) as listing, \
             contextlib.redirect_stdout(buff):
            rc = main(["--symbol", "MCX", "--period-end", "2026-09-30",
                       "--scope", "consolidated"])
        self.assertEqual(rc, 1)
        listing.assert_called_once()
        parsed = json.loads(buff.getvalue())
        self.assertEqual(parsed["status"], "FILING_METADATA_COVERAGE")
        self.assertEqual(parsed["publication_status"], "WITHHELD")


if __name__ == "__main__":
    unittest.main()
