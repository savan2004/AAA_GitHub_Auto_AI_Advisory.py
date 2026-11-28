"""NSE integrated Ind-AS HTML ingestion, research staging only.

Supports standalone/consolidated discrete quarters under observed NSE IndAS,
NBFC IndAS and BANKING iXBRL contracts. Banks explicitly reconcile interest
income, other income, associate profit and minority interests. Missing or
changed layouts, annual and unobserved formats are withheld.
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
PATH = re.compile(r"/corporate/ixbrl/INTEGRATED_FILING_(?:(?:NBFC_)?INDAS|BANKING)_[0-9]+_[0-9]{14}_iXBRL_WEB\.html")

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
    # From the *same* unique NSE income statement, never a segment table.
    # Optional for issuers without a separately filed excise expense.
    excise_cr: Decimal | None = None
    revenue_definition: str = "Revenue from operations"
    profit_definition: str = "Total profit (loss) for period"

    @property
    def status(self):
        return "EXTRACTED_PUBLICATION_TIME_UNVERIFIED"

def check_url(url):
    if not isinstance(url,str):
        raise EvidenceError("FILING_URL_INVALID")
    u=urlsplit(url)
    if u.scheme!="https" or u.netloc not in ("nsearchives.nseindia.com","nsearchives.nseindia.in") or u.query or u.fragment or not PATH.fullmatch(u.path):
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

def extract_quarter(document, member, *, expected_end, now, scope="standalone"):
    """Extract only the discrete quarter and the requested reporting basis."""
    expected_scope = {"standalone": "Standalone", "consolidated": "Consolidated"}.get(scope)
    if expected_scope is None:
        raise EvidenceError("FILING_SCOPE_OR_CURRENCY_UNSUPPORTED")
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
    currency=get("Description of presentation currency")
    if currency not in ("INR","INR (in Actuals)") or get("Nature of report standalone or consolidated")!=expected_scope:
        raise EvidenceError("FILING_SCOPE_OR_CURRENCY_UNSUPPORTED")
    if get("Reporting Type")!="Quarterly":
        raise EvidenceError("FILING_PERIOD_UNSUPPORTED")
    # Official historical template explicitly declares unscaled INR, and
    # omits the modern rounding row. Observed in ADANIPORTS Jun 2025
    # (filing 108491), BEL Mar 2025 (91913), ICICIBANK Jun 2025 (104150).
    # Never infer units from magnitude or a secondary vendor's number.
    legacy_actuals=currency=="INR (in Actuals)"
    unit_label="Level of rounding used in financial results"
    if legacy_actuals:
        if any(_label(cell)==_label(unit_label) for row in g for cell in row[:2]):
            if get(unit_label) not in ("Rupees","Actuals"):
                raise EvidenceError("FILING_UNIT_CONFLICT")
        divisor=Decimal(10000000)
    else:
        unit=get(unit_label)
        divisor={"Lakhs":Decimal(100),"Crores":Decimal(1),"Thousands":Decimal(10000),
                 "Rupees":Decimal(10000000)}.get(unit)
        if divisor is None:
            raise EvidenceError("FILING_UNIT_UNSUPPORTED")
    path = urlsplit(document.url).path
    banking = "/INTEGRATED_FILING_BANKING_" in path
    # The NSE BANKING issuer header uses "Name of bank", unlike INDAS.
    # Do not accept either synonym on the other template.
    name=get("Name of bank" if banking else "Name of company")
    if not name:
        raise EvidenceError("FILING_IDENTITY_MISMATCH")
    approved=_date(get("Date of board meeting when results were approved"))
    # Bank Screener "Revenue" corresponds to interest earned, not total income.
    # Bank "Net Profit" corresponds to period PAT + associates *before* minority,
    # not the parent-only attributable result. Verify both identities below.
    revenue_label = ("Total interest earned" if banking else
                     "Total Revenue From Operations" if "/INTEGRATED_FILING_NBFC_INDAS_" in path
                     else "Revenue from operations")
    profit_label = ("Net profit (loss) for the period" if banking
                    else "Total profit (loss) for period")
    candidates=[t for t in tables if any(_label(cell)==_label(revenue_label) for row in t for cell in row[:2])
                and any(_label(cell)=="date of start of reporting period" for row in t for cell in row[:2])
                and any(_label(cell)==_label(profit_label) for row in t for cell in row[:2])]
    if len(candidates)!=1:
        raise EvidenceError("FILING_FINANCIAL_LAYOUT")
    rows=candidates[0]
    headers=[row for row in rows if len(row)>=3 and any(_label(c)=="particulars" for c in row[:2])]
    if (len(headers)!=1 or _label(headers[0][-2])!="3 months/ 6 months ended (dd-mm-yyyy)"
        or _label(headers[0][-1])!="year to date figures for current period ended (dd-mm-yyyy)"):
        raise EvidenceError("FILING_COLUMN_ORDER_UNSUPPORTED")
    def quarter(key):
        return _one(rows,key,columns=2)[0]
    start=_date(quarter("Date of start of reporting period"))
    end=_date(quarter("Date of end of reporting period"))
    if (end!=expected_end or end.month not in (3,6,9,12)
        or end.day!=calendar.monthrange(end.year,end.month)[1]
        or start!=date(end.year,end.month-2,1)
        or not end<=approved<=now.astimezone(timezone(timedelta(hours=5,minutes=30))).date()):
        raise EvidenceError("FILING_PERIOD_MISMATCH")
    if quarter("Nature of report standalone or consolidated")!=expected_scope:
        raise EvidenceError("FILING_SCOPE_OR_CURRENCY_UNSUPPORTED")
    audit=quarter("Whether results are audited or unaudited")
    if audit not in ("Audited","Unaudited"):
        raise EvidenceError("FILING_AUDIT_UNKNOWN")
    # Older first-quarter reports sometimes omit both YTD dates (the
    # discrete quarter itself is the FY-to-date period). Check the actual
    # quarterly column regardless; a partially missing/contradictory
    # YTD date pair must not authorize a relaxed reconciliation.
    ytd_start=_one(rows,"Date of start of reporting period",columns=2)[1].strip()
    ytd_end=_one(rows,"Date of end of reporting period",columns=2)[1].strip()
    legacy_q1_without_ytd_dates=(legacy_actuals and end.month==6 and not ytd_start and not ytd_end)
    if legacy_actuals and (not ytd_start or not ytd_end) and not legacy_q1_without_ytd_dates:
        raise EvidenceError("FILING_PERIOD_MISMATCH")
    def reconciliation_columns(fields):
        # If first-quarter YTD figures are absent in ALL the required
        # source fields, reconcile the single reported quarter. If even
        # one YTD figure is present, keep the original two-column checks.
        if legacy_q1_without_ytd_dates and all(not f[1].strip() for f in fields):
            return (0,)
        return (0,1)
    revenue=_amount(quarter(revenue_label),divisor)
    profit=_amount(quarter(profit_label),divisor)
    excise = None
    revenue_definition = revenue_label
    profit_definition = profit_label
    if banking:
        # Compare BOTH the discrete quarter and YTD, so a wrong column or
        # different accounting basis cannot accidentally match the vendor.
        def bank_field(label):
            try:
                return _one(rows,label,columns=2)
            except EvidenceError as exc:
                # Static expected labels only, never filing text/customer data.
                tag=re.sub(r"[^A-Z0-9]+","_",label.upper()).strip("_")[:60]
                raise EvidenceError("FILING_MISSING_OR_AMBIGUOUS_FIELD_"+tag) from exc
        interest=bank_field("Total interest earned")
        other=bank_field("Other income")
        total=bank_field("Total income")
        period_profit=bank_field("Net profit (loss) for the period")
        associates=bank_field("Share of profit (loss) of associates")
        minority=bank_field("Profit (loss) of minority interest")
        attributable=bank_field(
            "Net Profit (loss) after taxes minority interest and share of profit (loss) of associates")
        for k in reconciliation_columns((interest,other,total,period_profit,associates,minority,attributable)):
            earned=_amount(interest[k],divisor)
            others=_amount(other[k],divisor)
            reported_total=_amount(total[k],divisor)
            if abs(earned+others-reported_total)>Decimal("0.02"):
                raise EvidenceError("BANK_INCOME_RECONCILIATION")
            # Blank cells are allowed ONLY where the reported attributable
            # profit reconciles with a mathematically zero missing share.
            share=_amount(associates[k],divisor) if associates[k].strip() else Decimal(0)
            noncontrolling=_amount(minority[k],divisor) if minority[k].strip() else Decimal(0)
            reported_attributable=_amount(attributable[k],divisor)
            gross_profit=_amount(period_profit[k],divisor)
            if abs(gross_profit+share-noncontrolling-reported_attributable)>Decimal("0.02"):
                raise EvidenceError("BANK_PROFIT_RECONCILIATION")
            if k==0:
                # Screener's consolidated Net Profit includes share of associates
                # before deduction of non-controlling interest.
                profit=gross_profit+share
        profit_definition="Net profit for period plus share of associates, before minority"
    else:
        # Disclosed excise is an expense, not arbitrary data massaging. A
        # secondary vendor may report sales net of excise, but this adjustment
        # is allowed only if the audited financial table reconciles that exact
        # excise line to its other-expenses subtotal in BOTH source columns.
        excise_rows=[row for row in rows if any(_label(c)=="excise duty"
                                                  for c in row[:2])]
        if excise_rows:
            if len(excise_rows)!=1:
                raise EvidenceError("EXCISE_AMBIGUOUS")
            duties=_one(rows,"Excise Duty",columns=2)
            other=_one(rows,"Other Expenses",columns=2)
            subtotal=_one(rows,"Total other expenses",columns=2)
            for k in reconciliation_columns((duties,other,subtotal)):
                duty=_amount(duties[k],divisor)
                extra=_amount(other[k],divisor)
                reported=_amount(subtotal[k],divisor)
                if duty<0 or abs(duty+extra-reported)>Decimal("0.02"):
                    raise EvidenceError("EXCISE_EXPENSE_RECONCILIATION")
                if k==0:
                    excise=duty
            if excise>=revenue:
                raise EvidenceError("EXCISE_EXCEEDS_REVENUE")
    if revenue<=0:
        raise EvidenceError("NONPOSITIVE_REVENUE")
    return QuarterExtract(member,name,start,end,approved,audit=="Audited",revenue,profit,
                          document.url,document.digest,document.retrieved_at,
                          excise_cr=excise, revenue_definition=revenue_definition,
                          profit_definition=profit_definition)
