import io
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pypdf import PdfReader
from msk_ai_builder_beta import beta_model, beta_pdf
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_allocation import EQUITY_CAP
from msk_ai_builder_telegram import deliver_preview

ENV={"MSK_BUILDER_V2_PREVIEW_ENABLED":"1","MSK_BUILDER_V2_PREVIEW_USER_ID":"123"}


class PrivateBetaTests(unittest.TestCase):
    def test_all_36_profiles(self):
        for cap in (1000000,5000000,10000000):
            for risk in ("low","medium","high"):
                for months in (6,18,36,60):
                    profile=ResearchProfile(cap,risk,months)
                    plan,artifact=beta_model(profile)
                    self.assertEqual(artifact["publication_status"],"FICTIONAL_ONLY")
                    self.assertLessEqual(len(plan.positions),profile.max_stocks)
                    self.assertEqual(plan.cash+sum(p.cost for p in plan.positions),cap)
                    self.assertGreaterEqual(plan.cash,Decimal(cap)*(100-EQUITY_CAP[risk][months])/100)
                    self.assertTrue(all(p.candidate.symbol.startswith("DEMO") for p in plan.positions))

    def test_pdf_is_fictional_and_has_no_source_links(self):
        pages=PdfReader(io.BytesIO(beta_pdf(ResearchProfile(1000000,"medium",36)))).pages
        self.assertEqual(len(pages),3)
        for page in pages:
            self.assertIn("TEST MODE",page.extract_text())
            self.assertIn("FICTIONAL QA DEMO",page.extract_text())
            self.assertNotIn("/Annots",page)
        self.assertIn("DEMO01",pages[1].extract_text())

    def test_owner_only_and_single_protected_document(self):
        bot=Mock()
        with patch.dict("os.environ",ENV,clear=True):
            with self.assertRaises(PermissionError):
                deliver_preview(bot,124,1000000,"medium",36)
            bot.send_document.assert_not_called()
            def send(uid,document,**opts):
                self.assertEqual(uid,123)
                self.assertTrue(opts["protect_content"])
                self.assertIn("TEST MODE",opts["caption"])
                self.assertEqual(len(PdfReader(io.BytesIO(document.read())).pages),3)
                return SimpleNamespace(message_id=7,document=SimpleNamespace(file_id="MOCK"))
            bot.send_document.side_effect=send
            deliver_preview(bot,123,1000000,"medium",36)
            self.assertEqual(bot.send_document.call_count,1)

    def test_disabled_even_for_owner(self):
        bot=Mock()
        with patch.dict("os.environ",{"MSK_BUILDER_V2_PREVIEW_USER_ID":"123"},clear=True):
            with self.assertRaises(PermissionError):
                deliver_preview(bot,123,1000000,"medium",36)
        bot.send_document.assert_not_called()
