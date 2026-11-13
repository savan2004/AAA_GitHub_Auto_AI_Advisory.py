"""Prebuild one actual SBIN eight-page research PDF on GitHub, without Telegram.

Publisher writes only public SBIN report to independent MSK research Neon.
"""
import hashlib
import logging
from pathlib import Path
from report_generator import generate_stock_report_pdf
from research_pdf_snapshot import (
    _current_session, _verified, save_verified_report, load_verified_report
)

logging.basicConfig(level=logging.INFO)


def main(symbol="SBIN"):
    if symbol not in ("SBIN", "DLF"):
        raise ValueError("Only explicitly audited SBIN or DLF reports can be published")
    session = _current_session()
    original = None
    recovered = None
    try:
        ok, result, _ = generate_stock_report_pdf(symbol)
        if not ok:
            raise RuntimeError("Real SBIN research PDF generation withheld; no snapshot written")
        original = Path(result)
        quality = _verified(original, symbol)
        # Inspect every ACTUAL page from the real PDF, not a mock. This is
        # editorial/date coverage only; it is not an issuer-filings audit.
        from pypdf import PdfReader
        from datetime import datetime
        pages = PdfReader(str(original), strict=True).pages
        page_texts = [(page.extract_text() or "").strip() for page in pages]
        session_label = datetime.strptime(session, "%Y-%m-%d").strftime("%d-%b-%Y")
        if session_label not in page_texts[0]:
            raise RuntimeError("Real report first page lacks verified price-session date")
        if symbol not in page_texts[0].upper():
            raise RuntimeError("Real report first page does not identify requested issuer")
        if any(len(text) < 24 for text in page_texts):
            raise RuntimeError("Real report has unreadable page")
        print("MSK_RESEARCH_PDF_CONTENT_AUDIT symbol={} session={} pages={} "
              "per_page_characters={} current_price_session=true "
              "issuer_identified=true disclosures_verified=true "
              "issuer_accounting_reconciled=false".format(
                 symbol, session, len(page_texts), [len(text) for text in page_texts]))
        source_digest = hashlib.sha256(original.read_bytes()).hexdigest()
        saved_digest = save_verified_report(symbol, original, session=session)
        if source_digest != saved_digest:
            raise RuntimeError("Research PDF DB checksum mismatch")
        loaded = load_verified_report(symbol)
        if loaded is None or loaded[1] != session:
            raise RuntimeError("Research PDF Neon read-back unavailable")
        recovered = Path(loaded[0])
        final_quality = _verified(recovered, symbol)
        if (hashlib.sha256(recovered.read_bytes()).hexdigest() != source_digest
                or final_quality["pages"] != quality["pages"]
                or final_quality["bytes"] != quality["bytes"]):
            raise RuntimeError("Research PDF Neon byte-for-byte read-back mismatch")
        print("MSK_RESEARCH_PDF_READY symbol={} session={} pages={} bytes={} "
              "sha256_prefix={} verified_readback=true issuer_accounting_reconciled=false".format(
                  symbol, session, quality["pages"], quality["bytes"],
                  source_digest[:12]))
    finally:
        if original:
            original.unlink(missing_ok=True)
        if recovered:
            recovered.unlink(missing_ok=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        raise SystemExit("Usage: python -m scripts.publish_research_pdf [SBIN|DLF]")
    main(sys.argv[1] if len(sys.argv) == 2 else "SBIN")
