"""Secondary evidence staging for MSK V2, not a recommendation engine.

No customer details, persistence, scoring or Telegram dependencies. A complete
secondary snapshot is still NOT an authenticated issuer filing. Production
selection remains prohibited until a separate primary reconciliation gate exists.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import re

from research_data import period_end, freshness, expected_latest_quarter

class EvidenceError(ValueError):
    """Stable failure code; safe to use without exposing request details."""

@dataclass(frozen=True)
class FinancialPeriod:
    end: date
    revenue_cr: float
    profit_cr: float

@dataclass(frozen=True)
class SecondarySnapshot:
    symbol: str
    scope: str
    source_url: str
    retrieved_at: datetime
    annual: tuple[FinancialPeriod, ...]
    quarterly: tuple[FinancialPeriod, ...]

    @property
    def publication_status(self):
        return "SECONDARY_ONLY"

    @property
    def eligible_for_selection(self):
        return False

    @property
    def revenue_cagr_pct(self):
        return _cagr(self.annual[0].revenue_cr, self.annual[-1].revenue_cr)

    @property
    def profit_cagr_pct(self):
        return _cagr(self.annual[0].profit_cr, self.annual[-1].profit_cr)

def _cagr(first, last):
    if first <= 0 or last <= 0:
        return None
    result = 100 * (math.exp((math.log(last) - math.log(first)) / 4) - 1)
    return round(result, 2) if math.isfinite(result) else None

def _number(value):
    if type(value) not in (int, float):
        raise EvidenceError("MISSING_OR_INVALID_FINANCIAL_VALUE")
    try:
        value = float(value)
    except OverflowError as exc:
        raise EvidenceError("MISSING_OR_INVALID_FINANCIAL_VALUE") from exc
    if not math.isfinite(value):
        raise EvidenceError("MISSING_OR_INVALID_FINANCIAL_VALUE")
    return value

def _symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9&-]{2,20}", symbol):
        raise EvidenceError("INVALID_NSE_SYMBOL")
    return symbol

def _months(a, b):
    return (b.year - a.year) * 12 + b.month - a.month

def _periods(rows, *, as_of, count, cadence):
    if not isinstance(rows, (list, tuple)):
        raise EvidenceError("MISSING_PERIODS")
    dated = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("label"), str):
            raise EvidenceError("INVALID_PERIOD")
        end = period_end(row["label"])
        if end is None:
            raise EvidenceError("INVALID_PERIOD_LABEL")
        if end > as_of:
            raise EvidenceError("FUTURE_REPORTING_PERIOD")
        if end in dated:
            # Only collapse identical rows from the same issuer page and
            # reporting basis. Never combine conflicting revisions or count
            # a duplicate as an additional reporting period.
            prior = dated[end]
            if (prior != row or _number(prior.get("revenue")) != _number(row.get("revenue"))
                    or _number(prior.get("net_profit")) != _number(row.get("net_profit"))):
                raise EvidenceError("CONFLICTING_DUPLICATE_PERIOD")
            continue
        dated[end] = row
    ends = sorted(dated)[-count:]
    if len(ends) != count:
        raise EvidenceError("INSUFFICIENT_HISTORY")
    if any(_months(a, b) != cadence for a, b in zip(ends, ends[1:])):
        raise EvidenceError("NONCONTIGUOUS_HISTORY")
    result = []
    for end in ends:
        revenue = _number(dated[end].get("revenue"))
        profit = _number(dated[end].get("net_profit"))
        if revenue <= 0:
            raise EvidenceError("NONPOSITIVE_REVENUE")
        result.append(FinancialPeriod(end, revenue, profit))
    return tuple(result)

def normalize_secondary(symbol, extras, *, now):
    """Strict current snapshot validation. Not suitable for historical backtests."""
    symbol = _symbol(symbol)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise EvidenceError("TIMEZONE_REQUIRED")
    now = now.astimezone(timezone.utc)
    if not isinstance(extras, dict) or extras.get("_source") != "Screener.in":
        raise EvidenceError("UNSUPPORTED_SOURCE")
    scope = extras.get("_statement_scope")
    suffix = {"consolidated": "/consolidated/", "standalone": "/"}.get(scope) if isinstance(scope, str) else None
    if suffix is None:
        raise EvidenceError("UNVERIFIED_SCOPE")
    url = "https://www.screener.in/company/" + symbol + suffix
    if extras.get("_source_url") != url:
        raise EvidenceError("SOURCE_IDENTITY_MISMATCH")
    try:
        retrieved = datetime.fromisoformat(extras["_retrieved_utc"].replace("Z","+00:00"))
        if retrieved.utcoffset() is None:
            raise ValueError()
        retrieved = retrieved.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise EvidenceError("INVALID_RETRIEVAL_TIME") from exc
    age = (now - retrieved).total_seconds()
    if not 0 <= age <= 86400:
        raise EvidenceError("STALE_OR_FUTURE_RETRIEVAL")
    as_of = now.date()
    annual = _periods(extras.get("years"), as_of=as_of, count=5, cadence=12)
    quarterly = _periods(extras.get("quarters"), as_of=as_of, count=8, cadence=3)
    # Initial methodology supports standard Indian March FY issuers only.
    if any(p.end.month != 3 for p in annual) or any(p.end.month not in (3,6,9,12) for p in quarterly):
        raise EvidenceError("UNSUPPORTED_FISCAL_CALENDAR")
    expected = expected_latest_quarter(as_of)
    expected_march = date(expected.year if expected.month >= 3 else expected.year-1,3,31)
    if annual[-1].end < expected_march:
        raise EvidenceError("STALE_ANNUAL_HISTORY")
    if freshness(extras["quarters"], today=as_of)["status"] != "FRESH":
        raise EvidenceError("STALE_QUARTERLY_HISTORY")
    return SecondarySnapshot(symbol, scope, url, retrieved, annual, quarterly)

def _exact_issuer_get(url, *, headers, timeout):
    """Require the requested reporting basis and issuer URL; no redirect relabeling."""
    import requests
    response=requests.get(url,headers=headers,timeout=timeout,allow_redirects=False)
    if response.status_code!=200 or response.url!=url:
        response.close()
        raise requests.RequestException("ISSUER_PAGE_IDENTITY_UNVERIFIED")
    return response

def collect_secondary(symbol, *, collector=None, now=None, timeout=9):
    """Reuse existing same-page collector. No per-customer storage or network on import."""
    symbol = _symbol(symbol)
    if collector is None:
        from portfolio_doctor_advanced import _screener_same_page_evidence
        def collector(symbol, *, request_timeout):
            return _screener_same_page_evidence(symbol,get=_exact_issuer_get,request_timeout=request_timeout)
    try:
        extras, _ = collector(symbol, request_timeout=timeout)
    except Exception as exc:
        raise EvidenceError("PROVIDER_UNAVAILABLE") from exc
    return normalize_secondary(symbol, extras, now=now or datetime.now(timezone.utc))
