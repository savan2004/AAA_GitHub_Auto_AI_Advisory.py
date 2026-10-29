"""Evidence-based fundamental gate for NSE large/mid/small-cap Top-5 lists.

Uses existing free-source fundamentals/statement collectors. No AI, paid API,
inferred 'missing = zero' substitutions, or invented ratios. This is not an
audit of issuer filings or an investment-success probability.
"""
from datetime import date, datetime, timedelta, timezone
import logging
import math
import threading
import time

LOG = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
MAX_CHECKS = 18  # technical pre-shortlist only; not 500 serial fundamentals requests
CACHE_TTL = 12 * 3600
_CACHE = {}
_LOCK = threading.RLock()

POLICY = {
    "conservative": {"min_roe": 12., "max_pe": 65., "max_de": 1.0,
                     "min_profit_cagr": 5., "min_sales_cagr": 3.},
    "moderate": {"min_roe": 13., "max_pe": 75., "max_de": 1.0,
                 "min_profit_cagr": 7., "min_sales_cagr": 4.},
    "aggressive": {"min_roe": 14., "max_pe": 80., "max_de": 0.8,
                   "min_profit_cagr": 9., "min_sales_cagr": 5.},
}
_FIN_WORDS = ("bank", "finance", "financial", "nbfc", "lending",
              "insurance", "credit", "asset management")


def _numeric(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _annual_periods(statements, today):
    """Only dated, completed fiscal years, never TTM or future periods."""
    from research_data import period_end
    result = {}
    for rec in (statements or {}).get("years") or []:
        end = period_end(str(rec.get("label") or ""))
        if end is not None and end <= today:
            result[end] = rec
    return sorted(result.items())


def _cagr(old, new, years):
    a, b = _numeric(old), _numeric(new)
    if a is None or b is None or a <= 0 or b <= 0 or years < 2.5:
        return None
    growth = ((b / a) ** (1 / years) - 1) * 100
    return growth if math.isfinite(growth) and abs(growth) < 1000 else None


def _is_financial(industry):
    return any(word in (industry or "").casefold() for word in _FIN_WORDS)


def assess_fundamentals(profile, fundamentals, statements, industry, today=None):
    """Return (eligible_snapshot, reason). Missing mandatory evidence always fails.

    Non-financial: ROE, debt/equity from same annual statements, 3Y sales and
    profit CAGR, positive EPS, P/E and fresh quarterly statements.
    Financial: P/B and ROE in place of a non-comparable corporate debt gate;
    at least three years of profit growth and fresh quarterly statements.
    """
    if profile not in POLICY:
        return None, "unsupported_profile"
    today = today or datetime.now(IST).date()
    if isinstance(today, datetime):
        today = today.date()
    fv = fundamentals or {}
    stm = statements or {}
    from research_data import freshness

    quarters = stm.get("quarters") or []
    fresh = freshness(quarters, today)
    if fresh.get("status") != "FRESH":
        return None, "quarterly_" + str(fresh.get("status", "MISSING")).lower()
    quarter = fresh.get("latest")
    if not isinstance(quarter, date) or quarter > today:
        return None, "quarter_date_invalid"

    annual = _annual_periods(stm, today)
    if len(annual) < 4:
        return None, "annual_history_missing"
    old_end, old = annual[-4]
    new_end, new = annual[-1]
    if (today - new_end).days > 550:
        return None, "annual_results_stale"
    years = (new_end - old_end).days / 365.25
    if not 2.5 <= years <= 3.6:
        return None, "annual_years_not_comparable"

    p = POLICY[profile]
    roe = _numeric(fv.get("roe"))
    pe = _numeric(fv.get("pe"))
    eps = _numeric(fv.get("eps"))
    netprofit = _numeric(new.get("net_profit"))
    if roe is None or roe < p["min_roe"] or roe > 150:
        return None, "roe_missing_or_weak"
    if pe is None or not 0 < pe <= p["max_pe"]:
        return None, "valuation_missing_or_expensive"
    # A missing EPS feed is not evidence of a loss-making company.
    # Keep the existing hard gate, but identify the precise missing fact.
    if eps is None:
        return None, "eps_unavailable"
    if eps <= 0:
        return None, "eps_nonpositive"
    if netprofit is None:
        return None, "annual_profit_unavailable"
    if netprofit <= 0:
        return None, "annual_loss"

    profit_growth = _cagr(old.get("net_profit"), netprofit, years)
    if profit_growth is None or profit_growth < p["min_profit_cagr"]:
        return None, "profit_growth_missing_or_weak"
    is_fin = _is_financial(industry)
    sales_growth = _cagr(old.get("revenue"), new.get("revenue"), years)
    debt_equity = None
    pb = _numeric(fv.get("pb"))
    if is_fin:
        if pb is None or not 0 < pb <= 6:
            return None, "financial_pb_missing_or_expensive"
    else:
        borrowings = _numeric(new.get("borrowings"))
        networth = _numeric(new.get("networth"))
        if networth is None or networth <= 0 or borrowings is None or borrowings < 0:
            return None, "debt_or_equity_missing"
        debt_equity = borrowings / networth
        if debt_equity > p["max_de"]:
            return None, "debt_too_high"
        if sales_growth is None or sales_growth < p["min_sales_cagr"]:
            return None, "sales_growth_missing_or_weak"

    # Transparent heuristic: profitable history + reasonable valuation + ROE.
    # Never call it a backtested win probability or forecast.
    growth = min(max(profit_growth, 0), 25)
    score = round(min(100, max(0,
        35 + min((roe - p["min_roe"]) * 1.5, 23)
        + min(growth * 0.9, 22)
        + max(0, 12 - (pe / p["max_pe"]) * 12)
        + (8 if (is_fin or (debt_equity is not None and debt_equity <= .4)) else 3))))
    return {
        "fund_score": score, "roe": round(roe, 1), "pe": round(pe, 1),
        "pb": round(pb, 2) if is_fin else None,
        "de": round(debt_equity, 2) if debt_equity is not None else None,
        "profit_growth": round(profit_growth, 1),
        "sales_growth": round(sales_growth, 1) if sales_growth is not None else None,
        "quarter": quarter.isoformat(), "annual": new_end.isoformat(),
        "fin": is_fin,
        "source": str(stm.get("source") or "Financial statement source unavailable"),
        "sources": stm.get("sources") or {},
    }, "qualified"


def fetch_and_assess(symbol, industry, profile, today=None):
    """Best-effort existing free sources; cache only complete accepted snapshots."""
    if profile not in POLICY:
        return None, "unsupported_profile"
    today = today or datetime.now(IST).date()
    key = (str(symbol).upper(), profile, industry, today.isoformat())
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1], "qualified"
    try:
        from fundamentals import get_fundamentals
        from research_data import get_statements
        fundamentals = get_fundamentals(symbol)
        statements = get_statements(symbol)
        result, reason = assess_fundamentals(profile, fundamentals, statements, industry, today)
        if result is not None:
            with _LOCK:
                _CACHE[key] = (time.monotonic(), result)
        return result, reason
    except Exception as exc:
        LOG.warning("MSK Top5 fundamental data %s unavailable (%s)", symbol, type(exc).__name__)
        return None, "provider_unavailable"
