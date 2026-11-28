"""Read-only NSE daily candidate adapter; never sends, saves or publishes.

Reuses the existing final archive parser once per requested session for all
admitted symbols. It returns candidates, NOT independently verified receipts:
the current parser does not expose exact source URL or document bytes/hash.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping

from msk_central_data_v4 import Universe, normalize_symbol


@dataclass(frozen=True)
class DailyCandidate:
    symbol: str
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def collect_candidates(universe: Universe, session: date, *, as_of: date,
                       is_completed_session, fetch=None, requested=None):
    if not isinstance(universe, Universe) or type(session) is not date or type(as_of) is not date:
        raise ValueError("invalid universe/session")
    symbols = universe.core if requested is None else tuple(dict.fromkeys(normalize_symbol(s) for s in requested))
    if not set(symbols).issubset(set(universe.symbols)):
        raise ValueError("unadmitted symbol")
    if session > as_of or not symbols or is_completed_session(session) is not True:
        return {}, symbols
    if fetch is None:
        from smart_chart_eod_recovery import final_archive_rows
        fetch = final_archive_rows
    try:
        rows = fetch(session, tuple(s + ".NS" for s in symbols))
    except Exception:
        return {}, symbols
    if not isinstance(rows, Mapping):
        return {}, symbols
    accepted = {}
    for s in symbols:
        row = rows.get(s + ".NS")
        try:
            o,h,l,c,v = (Decimal(str(row[k])) for k in ("Open","High","Low","Close","Volume"))
            if (not all(x.is_finite() for x in (o,h,l,c,v))
                or min(o,h,l,c) <= 0 or v < 0 or v != v.to_integral_value()
                or h < max(o,l,c) or l > min(o,h,c)):
                continue
            accepted[s] = DailyCandidate(s, session, o,h,l,c,int(v))
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            continue
    return accepted, tuple(s for s in symbols if s not in accepted)
