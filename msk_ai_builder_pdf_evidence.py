"""Offline PDF filing extraction for operator-reviewed documents.

No automatic publication. Only machine-generated, explicitly mapped tables
are supported. A match is evidence for review, never a verification of an
issuer's actual release time, authenticity or rights to redistribute data.
Run outside the live Telegram worker.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlsplit
import re

from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_filings import _amount

MAX_BYTES=8_000_000
MAX_PAGES=120
HOSTS={"nsearchives.nseindia.com","nsearchives.nseindia.in","archives.nseindia.com","www.bseindia.com"}

@dataclass(frozen=True)
class PDFPeriodEvidence:
    member: IndexMember
    scope: str
    end: date
    basis: str
    revenue_cr: Decimal
    profit_cr: Decimal
    source_url: str
    document_sha256: str
    page_number: int
    status: str="EXTRACTED_PDF_REVIEW_REQUIRED"


def _norm(value):
    if not isinstance(value,str):
        raise EvidenceError("PDF_CELL_MISSING")
    return " ".join(value.split())


def extract_pdf_period(body, member, *, source_url, expected_sha256,
                       page_number, end, basis, scope, units, column_headers,
                       revenue_label, profit_label):
    """Extract an exact, explicitly mapped column and two exact financial rows.

    Required identity metadata is present on the same selected page, using
    exact labeled lines. A filing with other layouts is withheld; no fuzzy
    matching, OCR, first-table guess or automatic unit/scope detection.
    """
    if (not isinstance(member,IndexMember) or type(end) is not date
            or basis not in ("Q","FY") or scope not in ("Standalone","Consolidated")
            or not isinstance(body,bytes) or not body.startswith(b"%PDF-")
            or not 0<len(body)<=MAX_BYTES or type(page_number) is not int
            or not 1<=page_number<=MAX_PAGES):
        raise EvidenceError("PDF_INPUT_INVALID")
    if not isinstance(expected_sha256,str) or sha256(body).hexdigest()!=expected_sha256:
        raise EvidenceError("PDF_DIGEST_MISMATCH")
    if not isinstance(source_url,str):
        raise EvidenceError("PDF_SOURCE_INVALID")
    u=urlsplit(source_url)
    if (u.scheme!="https" or u.netloc not in HOSTS or u.fragment
            or not u.path.lower().endswith(".pdf")):
        raise EvidenceError("PDF_SOURCE_INVALID")
    divisor={"Crores":Decimal(1),"Lakhs":Decimal(100),"Thousands":Decimal(10000),
             "Rupees":Decimal(10000000)}.get(units)
    if divisor is None:
        raise EvidenceError("PDF_UNIT_UNSUPPORTED")
    if not isinstance(column_headers,(tuple,list)) or not 2<=len(column_headers)<=8:
        raise EvidenceError("PDF_COLUMN_MAPPING")
    headers=tuple(_norm(s) for s in column_headers)
    # The period label is explicit and unique. Quarterly and full FY are
    # different from cumulative YTD columns even when the end date matches.
    target=("Quarter ended " if basis=="Q" else "Year ended ")+end.isoformat()
    if headers[0]!="Particulars" or headers.count(target)!=1 or len(set(headers))!=len(headers):
        raise EvidenceError("PDF_COLUMN_MAPPING")
    if not revenue_label or not profit_label or revenue_label==profit_label:
        raise EvidenceError("PDF_ROW_MAPPING")
    col=headers.index(target)
    import pdfplumber
    try:
        with pdfplumber.open(BytesIO(body)) as pdf:
            if not page_number<=len(pdf.pages)<=MAX_PAGES:
                raise EvidenceError("PDF_PAGE_LIMIT")
            page=pdf.pages[page_number-1]
            lines={_norm(t) for t in (page.extract_text() or "").splitlines()}
            expected={"NSE Symbol: "+member.symbol,"ISIN: "+member.isin,
                      "Reporting scope: "+scope,"Currency: INR","Units: "+units}
            if not expected.issubset(lines):
                raise EvidenceError("PDF_IDENTITY_SCOPE_UNIT_UNVERIFIED")
            matches=[]
            for table in page.extract_tables():
                for i,row in enumerate(table):
                    if len(row)==len(headers) and tuple(_norm(x or "") for x in row)==headers:
                        matches.append(table[i+1:])
            if len(matches)!=1:
                raise EvidenceError("PDF_TABLE_AMBIGUOUS_OR_MISSING")
            def value(label):
                rows=[r for r in matches[0] if r and _norm(r[0] or "")==label]
                if len(rows)!=1 or len(rows[0])!=len(headers):
                    raise EvidenceError("PDF_FINANCIAL_ROW_AMBIGUOUS_OR_MISSING")
                return _amount(_norm(rows[0][col]),divisor)
            revenue,profit=value(revenue_label),value(profit_label)
            if revenue<=0:
                raise EvidenceError("NONPOSITIVE_REVENUE")
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceError("PDF_EXTRACTION_FAILED") from exc
    return PDFPeriodEvidence(member,scope,end,basis,revenue,profit,source_url,
                             expected_sha256,page_number)
