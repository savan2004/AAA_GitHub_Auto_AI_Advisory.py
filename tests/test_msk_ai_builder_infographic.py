"""Offline infographic report and Telegram transport regression tests."""
import io
import json
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfReader
from msk_ai_builder_automatic import approved_plan, ResearchUnavailable
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_infographic import allocation_pdf, _indian
from msk_ai_builder_telegram import deliver_allocation
from test_msk_ai_builder_automatic import sample

SESSION=date(2026,11,20)


def fixture(cap=15000000,risk="medium",months=36,n=20):
    payload=sample()
    template=payload["companies"][0]
    payload["companies"]=[dict(template,symbol=f"TEST{i:02d}",isin=f"IN{i:010d}",
                                sector=f"Sector {i}",score=95-i,close_rupees="100")
                          for i in range(n)]
    plan=approved_plan(ResearchProfile(cap,risk,months),json.dumps(payload),latest_session=SESSION)
    return plan,payload


class InfographicTests(unittest.TestCase):
    def test_twenty_stock_report_all_pages_and_exact_amounts(self):
        plan,payload=fixture()
        raw=allocation_pdf(plan,payload,fictional=True)
        pages=PdfReader(io.BytesIO(raw),strict=True).pages
        self.assertEqual(len(pages),3)
        text=" ".join(p.extract_text() for p in pages)
        self.assertIn("FICTIONAL QA DEMO",text)
        self.assertIn("Rs 1,50,00,000.00",text)
        self.assertIn("Rs "+_indian(plan.cash),text)
        for p in plan.positions:
            self.assertIn(p.candidate.symbol,text)
            self.assertIn(_indian(p.cost),text)
        self.assertEqual(len(plan.positions),20)
        self.assertEqual(plan.cash+sum(p.cost for p in plan.positions),Decimal(15000000))

    def test_zero_equity_has_explicit_cash_and_no_blank_pages(self):
        plan,artifact=fixture(risk="low",months=6)
        pages=PdfReader(io.BytesIO(allocation_pdf(plan,artifact,fictional=True))).pages
        self.assertEqual(len(pages),3)
        self.assertIn("No stocks fit",pages[0].extract_text())
        self.assertTrue(all(p.extract_text().strip() for p in pages))

    def test_source_links_and_verified_single_document_delivery(self):
        plan,artifact=fixture(n=5)
        bot=Mock()
        files=[]
        def send(uid, document, **opts):
            self.assertEqual(uid,123)
            self.assertTrue(opts["protect_content"])
            files.append(document.read())
            return SimpleNamespace(message_id=5,document=SimpleNamespace(file_id="TEST"))
        bot.send_document.side_effect=send
        with patch.dict("os.environ",{"MSK_BUILDER_V2_INFOGRAPHIC_ENABLED":"1"}), patch(
                "msk_ai_builder_automatic.load_approved_model",return_value=(plan,artifact,False)):
            deliver_allocation(bot,123,15000000,"medium",36)
        self.assertEqual(bot.send_document.call_count,1)
        pages=PdfReader(io.BytesIO(files[0])).pages
        self.assertEqual(len(pages[2]["/Annots"]),2*len(plan.positions))

    def test_optimized_report_has_history_not_forecast(self):
        from test_msk_ai_builder_optimizer import fixture as optimizer_fixture
        from msk_ai_builder_allocation import allocate_portfolio
        candidates,history=optimizer_fixture()
        profile=ResearchProfile(100000,"medium",18)
        plan=allocate_portfolio(profile,candidates,expected_session=SESSION,risk_history=history)
        payload=sample()
        template=payload["companies"][0]
        payload["companies"]=[dict(template,symbol=c.symbol) for c in candidates]
        payload["risk_history"]=history
        pages=PdfReader(io.BytesIO(allocation_pdf(plan,payload,optimized=True,fictional=True))).pages
        self.assertEqual(len(pages),4)
        self.assertIn("Annualized volatility",pages[3].extract_text())
        self.assertIn("Not a realized track record",pages[3].extract_text())
        with self.assertRaises((KeyError,ValueError)):
            allocation_pdf(plan,dict(payload,risk_history={}),optimized=True)

    def test_withheld_or_unconfirmed_never_retries(self):
        bot=Mock()
        with patch.dict("os.environ",{"MSK_BUILDER_V2_INFOGRAPHIC_ENABLED":"1"}):
            with patch("msk_ai_builder_automatic.load_approved_model",side_effect=ResearchUnavailable):
                with self.assertRaises(ResearchUnavailable):
                    deliver_allocation(bot,123,15000000,"medium",36)
            bot.send_document.assert_not_called()
            plan,artifact=fixture(n=5)
            bot.send_document.return_value=SimpleNamespace(message_id=None)
            with patch("msk_ai_builder_automatic.load_approved_model",return_value=(plan,artifact,False)):
                with self.assertRaises(RuntimeError):
                    deliver_allocation(bot,123,15000000,"medium",36)
            self.assertEqual(bot.send_document.call_count,1)


if __name__ == "__main__":
    from pathlib import Path
    plan,payload=fixture()
    Path("msk-allocation-infographic-fictional.pdf").write_bytes(allocation_pdf(plan,payload,fictional=True))
    from test_msk_ai_builder_optimizer import fixture as optimizer_fixture
    from msk_ai_builder_allocation import allocate_portfolio
    candidates,history=optimizer_fixture()
    plan=allocate_portfolio(ResearchProfile(100000,"medium",18),candidates,expected_session=SESSION,risk_history=history)
    payload=sample()
    template=payload["companies"][0]
    payload["companies"]=[dict(template,symbol=c.symbol) for c in candidates]
    payload["risk_history"]=history
    Path("msk-optimized-infographic-fictional.pdf").write_bytes(allocation_pdf(plan,payload,optimized=True,fictional=True))
