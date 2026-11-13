"""One-time, verified-owner SBIN PDF publication acceptance on the LIVE bot.

Never runs unless explicitly enabled. Does not change holdings, stocks or user
subscriptions. Uses a persistent run claim to prevent duplicate Telegram sends.
"""
import logging
import os
from pathlib import Path
import re
import time

LOG = logging.getLogger("msk_sbin_pdf_probe")


def run_once(ctx):
    run_id = os.getenv("MSK_SBIN_PDF_LIVE_RUN_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{8,100}", run_id):
        LOG.error("MSK_SBIN_PDF_PROBE_NOT_STARTED reason=invalid_run_id")
        return
    time.sleep(7)
    owner_id = ctx["usage_analytics"].get_owner_id()
    if type(owner_id) is not int or owner_id <= 0:
        LOG.error("MSK_SBIN_PDF_PROBE_NOT_STARTED reason=no_verified_owner")
        return
    from msk_live_acceptance import _claim_once
    try:
        claimed = _claim_once("sbinpdf-" + run_id)
    except Exception as exc:
        LOG.error("MSK_SBIN_PDF_PROBE_NOT_STARTED reason=durable_claim_error type=%s",
                  type(exc).__name__)
        return
    if not claimed:
        LOG.info("MSK_SBIN_PDF_PROBE_NOT_STARTED reason=already_executed")
        return

    LOG.info("MSK_SBIN_PDF_PROBE_BEGIN symbol=SBIN run_id=%s", run_id)
    path = None
    try:
        ok, result, name = ctx["generate_stock_report_pdf"]("SBIN")
        if not ok:
            LOG.error("MSK_SBIN_PDF_PROBE_RESULT state=withheld reason=%s", result)
            return
        path = Path(result)
        from pdf_release_gate import validate_research_pdf
        verified = validate_research_pdf(path, expected_symbol="SBIN")
        size = verified["bytes"]
        pages = verified["pages"]
        if pages < 8 or size < 25_000:
            raise ValueError("Real SBIN PDF failed release size or page guard")
        with path.open("rb") as pdf:
            receipt = ctx["bot"].send_document(
                owner_id, pdf,
                visible_file_name="SBIN_Research_Report.pdf",
                caption="📄 SBIN research PDF — live owner acceptance (real sources; educational only)",
                parse_mode="HTML",
            )
        msg_id = getattr(receipt, "message_id", None)
        document = getattr(receipt, "document", None)
        file_id = getattr(document, "file_id", None)
        if type(msg_id) is not int or msg_id <= 0 or not file_id:
            raise RuntimeError("Telegram did not return a verifiable document receipt")
        ctx["_record_telegram_api_ack"](owner_id, receipt, "document")
        LOG.info("MSK_SBIN_PDF_PROBE_RESULT state=delivered message_id=%d "
                 "document_file_id=%s bytes=%d pages=%d", msg_id, file_id, size, pages)
    except Exception as exc:
        LOG.exception("MSK_SBIN_PDF_PROBE_RESULT state=failed type=%s",
                      type(exc).__name__)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                LOG.warning("MSK_SBIN_PDF_PROBE_CLEANUP_FAILED")
