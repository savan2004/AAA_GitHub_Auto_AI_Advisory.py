"""MSK-only weekly research bank. Internal staging, not a public stock shortlist.

Stores public issuer evidence only. No Telegram IDs, holdings, customer profiles
or API credentials. No live provider calls on reads. Authentic filing and
publication eligibility are intentionally NOT granted by this storage layer.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
import re
from urllib.parse import urlsplit

from msk_ai_builder_evidence import SecondarySnapshot
from msk_ai_builder_primary import IndexMember, SYMBOL, ISIN
from postgres_portfolio import _connect_postgres, _validate_dsn

IST=timezone(timedelta(hours=5,minutes=30))
MAX_BYTES=512_000
LOCK=628705164119
SCHEMA=(
    "CREATE SCHEMA IF NOT EXISTS msk_builder_research",
    """CREATE TABLE IF NOT EXISTS msk_builder_research.snapshots (
      version CHAR(64) PRIMARY KEY,
      reviewed_at TIMESTAMPTZ NOT NULL,
      close_date DATE NOT NULL,
      payload TEXT NOT NULL CHECK(octet_length(payload)<=512000),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS msk_builder_research.head (
      id SMALLINT PRIMARY KEY CHECK(id=1),
      version CHAR(64) REFERENCES msk_builder_research.snapshots(version)
    )""",
    "INSERT INTO msk_builder_research.head(id) VALUES(1) ON CONFLICT(id) DO NOTHING",
)

class BankError(ValueError):
    """Stable operator-facing failure; never expose connection details."""

@dataclass(frozen=True)
class StagedCompany:
    member: IndexMember
    sector: str
    company_name: str
    evidence: SecondarySnapshot
    close_date: date
    close_rupees: Decimal
    price_source_url: str

def _aware(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise BankError("TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)

def _text(value, limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(x)<32 for x in value):
        raise BankError("INVALID_TEXT")
    return value.strip()

def _decimal(value,*,positive=False):
    if type(value) not in (int,float,Decimal,str):
        raise BankError("INVALID_NUMBER")
    try:
        d=Decimal(str(value))
    except (InvalidOperation,ValueError) as exc:
        raise BankError("INVALID_NUMBER") from exc
    if not d.is_finite() or abs(d)>Decimal("1e14") or (positive and d<=0):
        raise BankError("INVALID_NUMBER")
    return format(d.normalize(),"f")

def _source(url):
    url=_text(url,500)
    u=urlsplit(url)
    if u.scheme!="https" or u.netloc not in ("www.nseindia.com","nsearchives.nseindia.com","archives.nseindia.com","www.bseindia.com") or not u.path.strip("/") or u.fragment:
        raise BankError("PRICE_SOURCE_UNSUPPORTED")
    return url

def _periods(rows,count,cadence,as_of):
    if len(rows)!=count:
        raise BankError("INCOMPLETE_FINANCIAL_HISTORY")
    ends=[p.end for p in rows]
    if any(type(d) is not date or d>as_of for d in ends):
        raise BankError("INVALID_PERIOD")
    if any((b.year-a.year)*12+b.month-a.month!=cadence for a,b in zip(ends,ends[1:])):
        raise BankError("INVALID_PERIOD")
    return [{"end":p.end.isoformat(),"revenue_cr":_decimal(p.revenue_cr,positive=True),
             "profit_cr":_decimal(p.profit_cr)} for p in rows]

def encode_snapshot(companies, *, reviewed_at, close_date, reviews, now):
    """Canonical internal snapshot. Close date must come from exchange calendar upstream.

    Reviews map every previously held symbol to retained or a removal reason.
    No source is promoted beyond secondary/unverified by supplied metadata.
    """
    reviewed_at,now=_aware(reviewed_at),_aware(now)
    day=reviewed_at.astimezone(IST).date()
    if reviewed_at>now or now-reviewed_at>timedelta(hours=24):
        raise BankError("REVIEW_TIME_INVALID")
    if type(close_date) is not date or not 0<=(day-close_date).days<=4:
        raise BankError("CLOSE_DATE_INVALID")
    companies=tuple(companies)
    if not 1<=len(companies)<=20:
        raise BankError("COMPANY_COUNT")
    if not isinstance(reviews,dict) or len(reviews)>20:
        raise BankError("REVIEWS_INVALID")
    review_rows={}
    for symbol,decision in reviews.items():
        if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol):
            raise BankError("REVIEWS_INVALID")
        decision=_text(decision,250)
        if decision!="retained" and not decision.startswith("removed: "):
            raise BankError("REVIEWS_INVALID")
        if decision=="removed:":
            raise BankError("REVIEWS_INVALID")
        review_rows[symbol]=decision
    rows=[]
    symbols=set()
    isins=set()
    for item in companies:
        if not isinstance(item,StagedCompany) or not isinstance(item.member,IndexMember):
            raise BankError("COMPANY_INVALID")
        m=item.member
        if not isinstance(m.symbol,str) or not SYMBOL.fullmatch(m.symbol) or not isinstance(m.isin,str) or not ISIN.fullmatch(m.isin):
            raise BankError("COMPANY_ID_INVALID")
        if m.symbol in symbols or m.isin in isins:
            raise BankError("DUPLICATE_COMPANY")
        symbols.add(m.symbol)
        isins.add(m.isin)
        e=item.evidence
        if not isinstance(e,SecondarySnapshot) or e.symbol!=m.symbol or e.eligible_for_selection:
            raise BankError("EVIDENCE_ID_INVALID")
        suffix={"standalone":"/","consolidated":"/consolidated/"}.get(e.scope)
        if suffix is None or e.source_url!="https://www.screener.in/company/"+m.symbol+suffix:
            raise BankError("EVIDENCE_SOURCE_INVALID")
        retrieved=_aware(e.retrieved_at)
        if not timedelta(0)<=reviewed_at-retrieved<=timedelta(hours=24):
            raise BankError("EVIDENCE_STALE")
        if item.close_date!=close_date:
            raise BankError("MIXED_CLOSE_DATE")
        rows.append({"symbol":m.symbol,"isin":m.isin,
            "company_name":_text(item.company_name,180),"sector":_text(item.sector,100),
            "annual":_periods(e.annual,5,12,day),"quarterly":_periods(e.quarterly,8,3,day),
            "scope":e.scope,"financial_source_url":e.source_url,
            "financial_retrieved_at":retrieved.isoformat(),
            "close_date":close_date.isoformat(),"close_rupees":_decimal(item.close_rupees,positive=True),
            "price_source_url":_source(item.price_source_url),
            "evidence_status":"SECONDARY_ONLY","price_status":"SOURCE_REFERENCE_NOT_RECONCILED"})
    if any((d=="retained")!=(s in symbols) for s,d in review_rows.items()):
        raise BankError("REVIEW_DECISION_MISMATCH")
    payload={"schema_version":1,"publication_status":"WITHHELD",
        "reviewed_at":reviewed_at.isoformat(),"close_date":close_date.isoformat(),
        "companies":sorted(rows,key=lambda row:row["symbol"]),"reviews":review_rows}
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False,ensure_ascii=True)
    if len(raw.encode())>MAX_BYTES:
        raise BankError("SNAPSHOT_TOO_LARGE")
    return sha256(raw.encode()).hexdigest(),raw

class WeeklyResearchBank:
    """Explicitly initialized MSK schema. Transactional immutable snapshot + head.

    Stored snapshots are staging records, never approved investment research.
    A failed refresh does not erase history or move the head.
    """
    def __init__(self,dsn,*,connector=None):
        self._dsn=_validate_dsn(dsn)
        self._connect=connector or _connect_postgres
        with self._connect(self._dsn) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)",(LOCK,))
            for sql in SCHEMA:
                conn.execute(sql)

    def replace(self,companies,*,reviewed_at,close_date,reviews,expected_version,now=None):
        now=_aware(now or datetime.now(timezone.utc))
        version,raw=encode_snapshot(companies,reviewed_at=reviewed_at,close_date=close_date,reviews=reviews,now=now)
        payload=json.loads(raw)
        with self._connect(self._dsn) as conn:
            head=conn.execute("SELECT version FROM msk_builder_research.head WHERE id=1 FOR UPDATE").fetchone()
            if head is None:
                raise BankError("HEAD_MISSING")
            current=head[0]
            if current==version:
                return version
            if current!=expected_version:
                raise BankError("VERSION_CONFLICT")
            if current is not None:
                old=conn.execute("SELECT reviewed_at,close_date,payload FROM msk_builder_research.snapshots WHERE version=%s",(current,)).fetchone()
                if old is None or sha256(old[2].encode()).hexdigest()!=current:
                    raise BankError("SNAPSHOT_CORRUPT")
                if reviewed_at<=old[0] or close_date<old[1]:
                    raise BankError("REVIEW_ROLLBACK")
                old_symbols={row["symbol"] for row in json.loads(old[2])["companies"]}
                if set(reviews)!=old_symbols:
                    raise BankError("PREVIOUS_COMPANY_REVIEW_INCOMPLETE")
            elif reviews:
                raise BankError("INITIAL_REVIEWS_MUST_BE_EMPTY")
            conn.execute("""INSERT INTO msk_builder_research.snapshots(version,reviewed_at,close_date,payload)
                            VALUES(%s,%s,%s,%s)""",(version,reviewed_at,close_date,raw))
            conn.execute("UPDATE msk_builder_research.head SET version=%s WHERE id=1",(version,))
        return version

    def _read_head(self,*,now=None,allow_stale=False):
        now=_aware(now or datetime.now(timezone.utc))
        with self._connect(self._dsn) as conn:
            row=conn.execute("""SELECT s.version,s.reviewed_at,s.close_date,s.payload
              FROM msk_builder_research.head h JOIN msk_builder_research.snapshots s ON h.version=s.version
              WHERE h.id=1""").fetchone()
        if row is None:
            raise BankError("BANK_EMPTY")
        version,reviewed,close,raw=row
        if sha256(raw.encode()).hexdigest()!=version:
            raise BankError("SNAPSHOT_CORRUPT")
        if reviewed>now or close>now.astimezone(IST).date():
            raise BankError("BANK_FUTURE")
        if not allow_stale and (now-reviewed>timedelta(days=8) or (now.astimezone(IST).date()-close).days>8):
            raise BankError("BANK_STALE")
        return version,json.loads(raw)

    def read_staging(self,*,now=None):
        return self._read_head(now=now)

    def read_for_review(self,*,now=None):
        # Internal recovery only; stale evidence can identify incumbents, never serve customers.
        return self._read_head(now=now,allow_stale=True)

    def read_for_customer(self):
        raise BankError("PRODUCTION_PUBLICATION_DISABLED")

def build_weekly_bank(*,environ=None,connector=None):
    env=os.environ if environ is None else environ
    flag=env.get("MSK_BUILDER_BANK_ENABLED","false")
    if flag not in ("true","false"):
        raise BankError("INVALID_BANK_FLAG")
    if flag=="false":
        return None
    dsn=env.get("MSK_BUILDER_BANK_DATABASE_URL")
    if not dsn:
        raise BankError("MSK_BANK_DSN_REQUIRED")
    return WeeklyResearchBank(dsn,connector=connector)
