"""NSE integrated Ind-AS HTML ingestion, research staging only.

Supports standalone discrete quarters in the observed NSE iXBRL display format.
Consolidated, bank, legacy, annual and changed layouts are explicitly withheld.
A downloaded document, digest and matching issuer do not establish publication
time, revision completeness or regulatory acceptance.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urlsplit
import calendar
import re
import time

from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_primary import IndexMember
from research_data import _tables_in

MAX_BYTES = 2_000_000
PATH = re.compile(r"/corporate/ixbrl/INTEGRATED_FILING_INDAS_[0-9]+_[0-9]{14}_iXBRL_WEB\.html")

@dataclass(frozen=True)
class DownloadedFiling:
    url: str
    body: bytes
    retrieved_at: datetime

    @property
    def digest(self):
        return sha256(self.body).hexdigest()

@dataclass(frozen=True)
class QuarterExtract:
    member: IndexMember
    company_name: str
    start: date
    end: date
    board_approval: date
    audited: bool
    revenue_cr: Decimal
    profit_cr: Decimal
    url: str
    digest: str
    retrieved_at: datetime

    @property
    def status(self):
        return "EXTRACTED_PUBLICATION_TIME_UNVERIFIED"

def check_url(url):
    if not isinstance(url,str):
        raise EvidenceError("FILING_URL_INVALID")
    u=urlsplit(url)
    if u.scheme!="https" or u.netloc!="nsearchives.nseindia.com" or u.query or u.fragment or not PATH.fullmatch(u.path):
        raise EvidenceError("FILING_URL_INVALID")
    return url

def download_filing(url, *, session=None, clock=time.monotonic):
    """HTTPS allowlist; redirect refusal, byte cap and cooperative time limit."""
    import requests
    check_url(url)
    own=session is None
    session=session or requests.Session()
    start=clock()
    try:
        with session.get(url, headers={"User-Agent":"Mozilla/5.0",
                         "Accept":"text/html"}, timeout=(4,8),
                         allow_redirects=False, stream=True) as resp:
            if resp.status_code!=200 or resp.url!=url:
                raise EvidenceError("FILING_HTTP_STATUS")
            if resp.headers.get("Content-Type","").split(";")[0].strip().lower()!="text/html":
                raise EvidenceError("FILING_CONTENT_TYPE")
            size=resp.headers.get("Content-Length")
            if size is not None and (not size.isdecimal() or int(size)>MAX_BYTES):
                raise EvidenceError("FILING_SIZE")
            body=bytearray()
            for chunk in resp.iter_content(chunk_size=16384):
                if clock()-start>20:
                    raise EvidenceError("FILING_DEADLINE")
                if len(body)+len(chunk)>MAX_BYTES:
                    raise EvidenceError("FILING_SIZE")
                body.extend(chunk)
            if not body:
                raise EvidenceError("FILING_EMPTY")
            return DownloadedFiling(url,bytes(body),datetime.now(timezone.utc))
    except EvidenceError:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise EvidenceError("FILING_DOWNLOAD_FAILED") from exc
    finally:
        if own:
            session.close()

def _label(text):
    return " ".join(text.lower().split())

def _one(rows, label, *, columns):
    found=[]
    key=_label(label)
    for row in rows:
        # Accept leading sequence number or a blank sequence cell.
        for i in range(min(2,len(row))):
            if _label(row[i])==key and len(row)-i-1==columns:
                found.append(tuple(row[i+1:]))
    if len(found)!=1:
        raise EvidenceError("FILING_MISSING_OR_AMBIGUOUS_FIELD")
    return found[0]

def _date(text):
    try:
        return datetime.strptime(text,"%d-%m-%Y").date()
    except (ValueError,TypeError) as exc:
        raise EvidenceError("FILING_DATE_INVALID") from exc

def _amount(text, divisor):
    raw=text.strip().replace(",","")
    if raw.startswith("(") and raw.endswith(")"):
        raw="-"+raw[1:-1]
    if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?",raw):
        raise EvidenceError("FILING_AMOUNT_INVALID")
    try:
        return Decimal(raw)/divisor
    except InvalidOperation as exc:
        raise EvidenceError("FILING_AMOUNT_INVALID") from exc

def extract_quarter(document, member, *, expected_end, now):
    """Extract the quarter column, never the YTD column; strict standalone only."""
    if not isinstance(document,DownloadedFiling) or not isinstance(member,IndexMember):
        raise EvidenceError("FILING_INPUT_INVALID")
    check_url(document.url)
    if not isinstance(document.body,bytes) or not 0<len(document.body)<=MAX_BYTES:
        raise EvidenceError("FILING_SIZE")
    if not isinstance(now,datetime) or now.utcoffset() is None or document.retrieved_at.utcoffset() is None:
        raise EvidenceError("TIMEZONE_REQUIRED")
    if not 0<=(now-document.retrieved_at).total_seconds()<=86400:
        raise EvidenceError("FILING_RETRIEVAL_STALE")
    try:
        html=document.body.decode("utf-8-sig")
    except UnicodeError as exc:
        raise EvidenceError("FILING_ENCODING") from exc
    tables=_tables_in(html)
    general=[t for t in tables if any(_label(cell)=="nse symbol" for row in t for cell in row[:2])]
    if len(general)!=1:
        raise EvidenceError("FILING_GENERAL_LAYOUT")
    g=general[0]
    get=lambda key:_one(g,key,columns=1)[0]
    if (get("NSE Symbol"),get("ISIN"))!=(member.symbol,member.isin):
        raise EvidenceError("FILING_IDENTITY_MISMATCH")
    if get("Description of presentation currency")!="INR" or get("Nature of report standalone or consolidated")!="Standalone":
        raise EvidenceError("FILING_SCOPE_OR_CURRENCY_UNSUPPORTED")
    if get("Reporting Type")!="Quarterly":
        raise EvidenceError("FILING_PERIOD_UNSUPPORTED")
    unit=get("Level of rounding used in financial results")
    divisor={"Lakhs":Decimal(100),"Crores":Decimal(1),"Thousands":Decimal(10000),
             "Rupees":Decimal(10000000)}.get(unit)
    if divisor is None:
        raise EvidenceError("FILING_UNIT_UNSUPPORTED")
    name=get("Name of company")
    if not name:
        raise EvidenceError("FILING_IDENTITY_MISMATCH")
    approved=_date(get("Date of board meeting when results were approved"))
    candidates=[t for t in tables if any(_label(cell)=="revenue from operations" for row in t for cell in row[:2])
                and any(_label(cell)=="date of start of reporting period" for row in t for cell in row[:2])]
    if len(candidates)!=1:
        raise EvidenceError("FILING_FINANCIAL_LAYOUT")
    rows=candidates[0]
    def quarter(key):
        return _one(rows,key,columns=2)[0]
    start=_date(quarter("Date of start of reporting period"))
    end=_date(quarter("Date of end of reporting period"))
    if (end!=expected_end or end.month not in (3,6,9,12)
        or end.day!=calendar.monthrange(end.year,end.month)[1]
        or start!=date(end.year,end.month-2,1)
        or not end<=approved<=now.astimezone(timezone(timedelta(hours=5,minutes=30))).date()):
        raise EvidenceError("FILING_PERIOD_MISMATCH")
    if quarter("Nature of report standalone or consolidated")!="Standalone":
        raise EvidenceError("FILING_SCOPE_OR_CURRENCY_UNSUPPORTED")
    audit=quarter("Whether results are audited or unaudited")
    if audit not in ("Audited","Unaudited"):
        raise EvidenceError("FILING_AUDIT_UNKNOWN")
    revenue=_amount(quarter("Revenue from operations"),divisor)
    profit=_amount(quarter("Total profit (loss) for period"),divisor)
    if revenue<=0:
        raise EvidenceError("NONPOSITIVE_REVENUE")
    return QuarterExtract(member,name,start,end,approved,audit=="Audited",revenue,profit,
                          document.url,document.digest,document.retrieved_at)
