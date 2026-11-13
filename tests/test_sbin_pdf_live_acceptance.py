"""No Telegram network calls: proof that the live SBIN test is owner-only."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import msk_sbin_pdf_probe as probe


class SBINLiveProbeTests(unittest.TestCase):
    def test_without_verified_owner_sends_nothing(self):
        bot=SimpleNamespace(send_document=Mock())
        ctx={"usage_analytics":SimpleNamespace(get_owner_id=lambda:None),"bot":bot}
        with patch.dict(os.environ, {"MSK_SBIN_PDF_LIVE_RUN_ID":"test-sbin-run-1"}), \
             patch.object(probe.time,"sleep",return_value=None):
            probe.run_once(ctx)
        bot.send_document.assert_not_called()

    def test_verified_pdf_generates_a_real_document_receipt_and_cleans_file(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/"SBIN_report.pdf"
            path.write_bytes(b"%PDF-" + b"x"*29000)
            document=SimpleNamespace(message_id=12345,
                       document=SimpleNamespace(file_id="real-document-receipt-abc"))
            bot=SimpleNamespace(send_document=Mock(return_value=document))
            ack=Mock()
            ctx={
                "usage_analytics":SimpleNamespace(get_owner_id=lambda:112233),
                "generate_stock_report_pdf":Mock(return_value=(True,str(path),"State Bank of India")),
                "bot":bot,
                "_record_telegram_api_ack":ack,
            }
            with patch.dict(os.environ, {"MSK_SBIN_PDF_LIVE_RUN_ID":"test-sbin-run-2"}), \
                 patch.object(probe.time,"sleep",return_value=None), \
                 patch("msk_live_acceptance._claim_once",return_value=True), \
                 patch("pdf_release_gate.validate_research_pdf",
                       return_value={"pages":8,"bytes":29005}):
                probe.run_once(ctx)
            bot.send_document.assert_called_once()
            ack.assert_called_once_with(112233,document,"document")
            self.assertFalse(path.exists())

    def test_if_report_withheld_no_telegram_pdf_send(self):
        bot=SimpleNamespace(send_document=Mock())
        ctx={
            "usage_analytics":SimpleNamespace(get_owner_id=lambda:112233),
            "generate_stock_report_pdf":Mock(return_value=(False,"no verified financial data",None)),
            "bot":bot,
        }
        with patch.dict(os.environ, {"MSK_SBIN_PDF_LIVE_RUN_ID":"test-sbin-run-3"}), \
             patch.object(probe.time,"sleep",return_value=None), \
             patch("msk_live_acceptance._claim_once",return_value=True):
            probe.run_once(ctx)
        bot.send_document.assert_not_called()


if __name__=="__main__":
    unittest.main()
