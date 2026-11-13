"""MSK client-facing research PDF structural and disclosure release gate.

A PDF with missing sections, broken pages or omitted financial disclaimers must
NOT be sent as a completed report. Pure offline inspection; no issuer certification
is implied and this does not check numbers against NSE financial filings.
"""
from pathlib import Path
import re


class ResearchPDFRefused(ValueError):
    pass


def validate_research_pdf(path, *, expected_symbol=None, min_pages=8):
    from pypdf import PdfReader

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ResearchPDFRefused("Research PDF missing or unsafe")
    size = path.stat().st_size
    if size < 25_000 or size > 25_000_000:
        raise ResearchPDFRefused("Research PDF size outside delivery bounds")
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ResearchPDFRefused("Invalid PDF signature")
    try:
        reader = PdfReader(str(path), strict=True)
        pages = reader.pages
        if reader.is_encrypted or len(pages) < min_pages or len(pages) > 25:
            raise ResearchPDFRefused("Research report page count or encryption invalid")
        text_by_page = [(page.extract_text() or "").strip() for page in pages]
    except ResearchPDFRefused:
        raise
    except Exception as exc:
        raise ResearchPDFRefused("Research PDF parse or page extraction failed") from exc

    text = " ".join(" ".join(page.split()) for page in text_by_page)
    if any(len(page) < 24 for page in text_by_page):
        raise ResearchPDFRefused("Empty or unreadable report page")
    # ReportLab may silently push a final glossary/disclaimer onto an almost
    # empty trailing page; a valid footer does not make that editorially okay.
    if len(text_by_page[-1]) < 1100:
        raise ResearchPDFRefused("Nearly blank trailing research PDF page")
    required = (
        "Technical Analysis",
        "ONE-PAGE DECISION SNAPSHOT",
        "Not SEBI-registered advice",
        "Financial figures and ratios were not independently reconciled",
    )
    if any(needle.lower() not in text.lower() for needle in required):
        raise ResearchPDFRefused("Required financial methodology or risk disclaimer missing")
    if expected_symbol is not None:
        symbol = str(expected_symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", symbol):
            raise ResearchPDFRefused("Invalid report instrument")
        # Reject substring matches (e.g. "SBIN" inside "XSBIN").
        ticker_chars = r"A-Z0-9&._-"
        if not re.search(
                rf"(?<![{ticker_chars}]){re.escape(symbol)}(?![{ticker_chars}])",
                text.upper()):
            raise ResearchPDFRefused("PDF instrument identity does not match request")
    return {"pages": len(text_by_page), "bytes": size,
            "status": "structure_and_disclosure_only",
            "issuer_filing_verified": False,
            "real_data_reconciled": False,
            "telegram_delivered": False}
