from dataclasses import replace
from datetime import timezone
from unittest import TestCase
from unittest.mock import Mock
from msk_ai_builder_evidence import normalize_secondary, EvidenceError
from msk_ai_builder_filings import DownloadedFiling
from msk_ai_builder_quarter_check import check_latest_quarter
from test_msk_ai_builder_filings import fixture as filing, MEMBER, URL, NOW
from test_msk_ai_builder_evidence import fixture as secondary


class QuarterCheckTests(TestCase):
    def setup_inputs(self):
        data = secondary()
        data["_statement_scope"] = "standalone"
        data["_source_url"] = "https://www.screener.in/company/FIXTURE/"
        data["quarters"][-1].update(revenue=1235, net_profit=-23)
        snap = normalize_secondary("FIXTURE", data, now=NOW)
        payload = {"totalCount": 1, "data": [
            {"symbol": "FIXTURE", "qe_Date": "30-SEP-2026",
             "consolidated": "Standalone", "broadcast_Date": "12-Nov-2026 17:22:00",
             "revised_Date": None, "ixbrl": URL}]}
        return snap, Mock(return_value=payload), Mock(return_value=DownloadedFiling(URL, filing(), NOW))

    def test_match_only_and_publication_withheld(self):
        snap, listing, download = self.setup_inputs()
        result = check_latest_quarter(MEMBER, snap, listing=listing, download=download, clock=lambda: NOW)
        self.assertEqual(result["status"], "LATEST_QUARTER_MATCHED_ONLY")
        self.assertEqual(result["publication_status"], "WITHHELD")
        self.assertEqual(result["issuer_revenue_cr"], "1234.5678")
        listing.assert_called_once_with("FIXTURE", snap.quarterly[-1].end)
        download.assert_called_once_with(URL)

    def test_conflict_and_scope_do_not_get_promoted(self):
        snap, listing, download = self.setup_inputs()
        with self.assertRaisesRegex(EvidenceError, "QUARTER_CHECK_VALUE_MISMATCH"):
            check_latest_quarter(MEMBER, replace(snap, quarterly=snap.quarterly[:-1] + (
                replace(snap.quarterly[-1], profit_cr=99),)),
                listing=listing, download=download, clock=lambda: NOW)
        listing.reset_mock()
        with self.assertRaisesRegex(EvidenceError, "QUARTER_CHECK_SCOPE_UNSUPPORTED"):
            check_latest_quarter(MEMBER, replace(snap, scope="consolidated"),
                listing=listing, download=download, clock=lambda: NOW)
        listing.assert_not_called()
