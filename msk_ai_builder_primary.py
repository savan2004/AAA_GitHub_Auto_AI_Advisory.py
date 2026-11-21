"""Research staging only. No publication authorization or trading integration."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import re

from msk_ai_builder_evidence import EvidenceError, SecondarySnapshot

INDEX_URL = "https://www.nseindia.com/api/index-constituents?index=NIFTY%20500"
SYMBOL = re.compile(r"[A-Z0-9&-]{1,20}")
ISIN = re.compile(r"INE[A-Z0-9]{9}")

@dataclass(frozen=True)
class IndexMember:
    symbol: str
    isin: str

def parse_index(payload):
    """No static-sector fallback, no partial universe advertised as Nifty 500."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise EvidenceError("INDEX_UNAVAILABLE")
    members = []
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceError("INVALID_INDEX_ROW")
        if row.get("symbol") == "NIFTY 500":
            continue
        symbol, isin = row.get("symbol"), row.get("isin")
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise EvidenceError("INVALID_INDEX_SYMBOL")
        if not isinstance(isin, str) or not ISIN.fullmatch(isin):
            raise EvidenceError("INVALID_INDEX_ISIN")
        members.append(IndexMember(symbol, isin))
    if len(members) != 500 or len({m.symbol for m in members}) != 500 or len({m.isin for m in members}) != 500:
        raise EvidenceError("INCOMPLETE_OR_DUPLICATE_INDEX")
    return tuple(sorted(members, key=lambda m: m.symbol))

def fetch_index(*, session=None):
    """Bounded official request; failures withhold the universe."""
    import requests
    own = session is None
    session = session or requests.Session()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com/"}
    try:
        warm = session.get("https://www.nseindia.com/", headers=headers, timeout=(4,8))
        warm.raise_for_status()
        response = session.get(INDEX_URL, headers=headers, timeout=(4,12))
        response.raise_for_status()
        if response.url != INDEX_URL:
            raise EvidenceError("INDEX_SOURCE_MISMATCH")
        return parse_index(response.json())
    except EvidenceError:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise EvidenceError("INDEX_UNAVAILABLE") from exc
    finally:
        if own:
            session.close()

@dataclass(frozen=True)
class FilingPeriod:
    symbol: str
    isin: str
    scope: str
    basis: str
    end: date
    published_at: datetime
    filing_url: str
    document_sha256: str
    revenue_cr: Decimal
    profit_cr: Decimal

def _money(value):
    if isinstance(value, bool):
        raise EvidenceError("INVALID_PRIMARY_NUMBER")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceError("INVALID_PRIMARY_NUMBER") from exc
    if not result.is_finite():
        raise EvidenceError("INVALID_PRIMARY_NUMBER")
    return result

def reconcile_statements(member, secondary, filings, *, now):
    """Compare normalized filing extracts against every secondary period.

    This validates agreement, NOT document authenticity/extraction correctness.
    Callers must not claim the supplied digest was downloaded or authenticated.
    All amounts are INR crore, discrete quarter or full FY, same profit definition.
    No publication, allocation or advice permission is conferred by a match.
    """
    if not isinstance(secondary, SecondarySnapshot) or member.symbol != secondary.symbol:
        raise EvidenceError("ISSUER_MISMATCH")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise EvidenceError("TIMEZONE_REQUIRED")
    age = (now-secondary.retrieved_at).total_seconds()
    if not 0 <= age <= 86400:
        raise EvidenceError("STALE_SECONDARY")
    expected = {(basis,p.end): p for basis, periods in
                (("FY",secondary.annual),("Q",secondary.quarterly)) for p in periods}
    seen = set()
    for f in filings:
        if not isinstance(f, FilingPeriod) or (f.symbol,f.isin,f.scope) != (member.symbol,member.isin,secondary.scope):
            raise EvidenceError("FILING_IDENTITY_MISMATCH")
        key = (f.basis,f.end)
        if key in seen or key not in expected:
            raise EvidenceError("DUPLICATE_OR_UNEXPECTED_FILING")
        seen.add(key)
        if not isinstance(f.published_at,datetime) or f.published_at.utcoffset() is None or not f.end <= f.published_at.date() or f.published_at > now:
            raise EvidenceError("FILING_DATE_INVALID")
        url = urlsplit(f.filing_url)
        if url.scheme != "https" or url.netloc not in ("nsearchives.nseindia.com","nsearchives.nseindia.in","archives.nseindia.com","www.bseindia.com","www.nseindia.com") or not url.path.strip("/"):
            raise EvidenceError("FILING_SOURCE_INVALID")
        if not re.fullmatch(r"[0-9a-f]{64}",f.document_sha256):
            raise EvidenceError("FILING_DIGEST_INVALID")
        ref = expected[key]
        for raw, secondary_value in ((f.revenue_cr,ref.revenue_cr),(f.profit_cr,ref.profit_cr)):
            primary_value, observed = _money(raw), _money(secondary_value)
            # Allow published rounding, not percentage-wide discrepancies.
            if abs(primary_value-observed) > Decimal("0.51"):
                raise EvidenceError("FILING_VALUE_MISMATCH")
    if seen != set(expected):
        raise EvidenceError("MISSING_PRIMARY_PERIODS")
    return "MATCHED_EXTRACTS_NOT_AUTHENTICATED"
