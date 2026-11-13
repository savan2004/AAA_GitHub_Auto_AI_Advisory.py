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


def main():
    symbol = "SBIN"
    session = _current_session()
    original = None
    recovered = None
    try:
        ok, result, _ = generate_stock_report_pdf(symbol)
        if not ok:
            raise RuntimeError("Real SBIN research PDF generation withheld; no snapshot written")
        original = Path(result)
        quality = _verified(original, symbol)
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
    main()
