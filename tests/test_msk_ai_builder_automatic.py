"""Offline contract: synthetic fixtures prove mechanics, NOT verified NSE equities."""
import copy
import io
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfReader
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_automatic import (
    ResearchUnavailable, approved_plan, automatic_available, build_automatic_pdf,
)
from msk_ai_builder_telegram import deliver_automatic_pdf

SESSION = date(2026, 11, 20)


def fixture(n=20):
    """Explicitly fictitious private test rows; never a production data feed."""
    rows = []
    for i in range(n):
        rows.append({
            "symbol": f"QA{i:02d}",
            "isin": f"INE{i:09d}",
            "sector": f"Fictional Industry {i}",
            "close_date": SESSION.isoformat(),
            "close_rupees": "100.00",
            "score": str(99 - i),
            "evidence_status": "ISSUER_AUTHENTICATED",
            "price_status": "EXCHANGE_VERIFIED",
            "valuation_status": "VERIFIED",
            "risk_status": "VERIFIED",
            "corporate_actions_status": "VERIFIED",
            "issuer_identity_verified": True,
            "verified_annual_periods": 5,
            "verified_quarter_periods": 8,
            "filing_sha256": "a" * 64,
            "filing_url": "https://nsearchives.nseindia.in/fictional-test-fixture.html",
            "price_source_url": "https://archives.nseindia.com/fictional-test-prices.csv",
        })
    return {
        "schema_version": 1,
        "publication_status": "APPROVED",
        "source_class": "ISSUER_PRIMARY_VERIFIED",
        "regulatory_review_status": "APPROVED",
        "close_date": SESSION.isoformat(),
        "companies": rows,
    }


def flags(payload=None):
    return {"MSK_BUILDER_V2_ENABLED": "1",
            "MSK_BUILDER_V2_APPROVED_RESEARCH": json.dumps(payload or fixture())}


class ApprovedBuilderTests(unittest.TestCase):
    def test_profile_tiers_automatic_selection_and_cash_reconcile(self):
        for capital, maximum in ((1_000_000, 5), (5_000_000, 10), (15_000_000, 20)):
            profile = ResearchProfile(capital, "high", 60)
            plan, data = approved_plan(profile, json.dumps(fixture()), latest_session=SESSION)
            with self.subTest(capital=capital):
                self.assertEqual(len(plan.positions), maximum)
                self.assertEqual(plan.close_date, SESSION)
                self.assertTrue(all(row.shares > 0 for row in plan.positions))
                self.assertEqual(
                    plan.cash + sum((row.cost for row in plan.positions), Decimal(0)),
                    Decimal(capital),
                )
                self.assertEqual(len({row.candidate.symbol for row in plan.positions}), maximum)
                self.assertEqual(data["source_class"], "ISSUER_PRIMARY_VERIFIED")

    def test_missing_stale_and_unverified_research_never_allocates(self):
        bad = []
        for key, value in (
            ("publication_status", "WITHHELD"),
            ("source_class", "SECONDARY_ONLY"),
            ("regulatory_review_status", "PENDING"),
        ):
            data = fixture()
            data[key] = value
            bad.append(data)
        for key, value in (
            ("evidence_status", "SECONDARY_ONLY"),
            ("price_status", "SOURCE_REFERENCE_NOT_RECONCILED"),
            ("valuation_status", "PENDING"),
            ("risk_status", "WITHHELD"),
            ("corporate_actions_status", "UNKNOWN"),
            ("issuer_identity_verified", False),
            ("verified_annual_periods", 4),
            ("verified_quarter_periods", 1),
            ("filing_sha256", "missing"),
            ("filing_url", "http://nseindia.com/not-https"),
            ("price_source_url", "https://attacker.example/prices"),
            ("close_date", (SESSION-timedelta(days=1)).isoformat()),
            ("score", "0"),
            ("close_rupees", "NaN"),
        ):
            data = fixture()
            data["companies"][0][key] = value
            bad.append(data)
        mixed = fixture()
        mixed["companies"][1]["isin"] = mixed["companies"][0]["isin"]
        bad.append(mixed)
        fake = fixture()
        fake["companies"][0]["symbol"] = "DEMO01"
        bad.append(fake)
        empty = fixture()
        empty["companies"] = []
        bad.append(empty)
        for i, data in enumerate(bad):
            with self.subTest(case=i), self.assertRaises(ResearchUnavailable):
                approved_plan(ResearchProfile(1_000_000, "medium", 36),
                              json.dumps(data), latest_session=SESSION)
        with self.assertRaises(ResearchUnavailable):
            approved_plan(ResearchProfile(1_000_000, "medium", 36),
                          json.dumps(fixture()), latest_session=SESSION+timedelta(days=1))

    def test_low_risk_short_horizon_never_invents_positions(self):
        with self.assertRaises(ResearchUnavailable):
            approved_plan(ResearchProfile(1_000_000, "low", 6),
                          json.dumps(fixture()), latest_session=SESSION)

    def test_exact_source_whitelist_blocks_lookalikes(self):
        for url in (
            "https://nsearchives.nseindia.in.evil.net/file",
            "https://nsearchives.nseindia.in@evil.net/file",
            "https://nsearchives.nseindia.in:8443/file",
            "https://nsearchives.nseindia.in/",
            "https://nsearchives.nseindia.in/file#fragment",
        ):
            data = fixture()
            data["companies"][0]["filing_url"] = url
            with self.subTest(url=url), self.assertRaises(ResearchUnavailable):
                approved_plan(ResearchProfile(1_000_000, "high", 60),
                              json.dumps(data), latest_session=SESSION)

    def test_real_pdf_renderer_works_for_five_ten_twenty_mocked_companies(self):
        # All source links and names are fictional offline fixtures. Real customers
        # cannot get these without intentionally forged production approval flags.
        for capital, count in ((1_000_000, 5), (5_000_000, 10), (15_000_000, 20)):
            with self.subTest(capital=capital):
                pdf = build_automatic_pdf(capital, "high", 60,
                                           environ=flags(), latest_session=SESSION)
                pages = PdfReader(io.BytesIO(pdf), strict=True).pages
                self.assertEqual(len(pages), 3)
                text = "\n".join(p.extract_text() or "" for p in pages)
                self.assertIn(f"{count} stocks", text)
                self.assertIn("QA00", text)
                self.assertNotIn("TEST MODE", text)
                self.assertNotIn("DEMO01", text)

    def test_telegram_delivery_once_only_when_receipt_is_verified(self):
        bot = Mock()
        bot.send_document.return_value = SimpleNamespace(
            message_id=101, document=SimpleNamespace(file_id="fake-platform-file"))
        with patch.dict("os.environ", flags(), clear=True), \
             patch("msk_ai_builder_automatic._completed_session", return_value=SESSION):
            receipt = deliver_automatic_pdf(bot, 101, 1_000_000, "medium", 36)
            self.assertEqual(receipt.message_id, 101)
            self.assertEqual(bot.send_document.call_count, 1)
            kwargs = bot.send_document.call_args.kwargs
            self.assertTrue(kwargs["protect_content"])
            self.assertEqual(kwargs["timeout"], 30)
            self.assertNotIn("fictional", kwargs["caption"].lower())
            bot.send_document.return_value = SimpleNamespace(
                message_id=None, document=SimpleNamespace(file_id=None))
            with self.assertRaisesRegex(RuntimeError, "UNCONFIRMED"):
                deliver_automatic_pdf(bot, 101, 1_000_000, "medium", 36)
        bot.send_document.reset_mock()
        with patch.dict("os.environ", {"MSK_BUILDER_V2_ENABLED": "1"}, clear=True):
            with self.assertRaises(ResearchUnavailable):
                deliver_automatic_pdf(bot, 101, 1_000_000, "medium", 36)
        bot.send_document.assert_not_called()

    def test_flag_off_even_if_artifact_is_provided(self):
        env = flags()
        env["MSK_BUILDER_V2_ENABLED"] = "0"
        self.assertFalse(automatic_available(environ=env))
        with self.assertRaises(ResearchUnavailable):
            build_automatic_pdf(1_000_000, "high", 60,
                                environ=env, latest_session=SESSION)


if __name__ == "__main__":
    unittest.main()
