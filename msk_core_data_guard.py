"""Core numerical/financial evidence contracts. Missing data never becomes zero."""
from decimal import Decimal, InvalidOperation
from datetime import date
from urllib.parse import urlsplit
import math

def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value).strip())
        if not n.is_finite() or not math.isfinite(float(n)):
            return None
        return n
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None

def reconcile_fact(primary, secondary, *, tolerance="0", today=None):
    """Compare explicitly sourced, same issuer/metric/period/unit/scope records.
    No claim of primary-source authenticity is made by this schema check.
    """
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        return "MISSING"
    keys = ("isin", "metric", "period_start", "period_end", "unit", "scope")
    for rec in (primary, secondary):
        if any(not isinstance(rec.get(k), str) or not rec[k].strip() for k in keys):
            return "MISSING_METADATA"
        if rec["scope"] not in ("consolidated", "standalone"):
            return "MISSING_METADATA"
        try:
            start, end, published = (date.fromisoformat(rec[k]) for k in
                                      ("period_start", "period_end", "published"))
        except (KeyError, ValueError, TypeError):
            return "MISSING_METADATA"
        if not start <= end <= published <= (today or date.today()):
            return "INVALID_PERIOD"
    if any(primary[k] != secondary[k] for k in keys):
        return "NOT_COMPARABLE"
    url = primary.get("source_url")
    try:
        u = urlsplit(url) if isinstance(url, str) else None
        if (not u or u.scheme != "https" or u.hostname != "nsearchives.nseindia.com"
                or u.username or u.password or not u.path.startswith("/corporate/")):
            return "UNAPPROVED_SOURCE"
    except ValueError:
        return "UNAPPROVED_SOURCE"
    a, b, tol = (finite_number(v) for v in (primary.get("value"), secondary.get("value"), tolerance))
    if a is None or b is None or tol is None or tol < 0:
        return "MISSING"
    return "MATCH" if abs(a-b) <= tol else "MISMATCH"

def wilder_rsi_values(closes, period=14):
    """One SMA-seeded Wilder RSI algorithm; None until enough valid closes.
    Invalid observations reset the seed. A fully flat sequence is neutral.
    No third-party imports: portfolio valuation can use the same engine.
    """
    if type(period) is not int or period < 2:
        raise ValueError("invalid RSI period")
    out = []
    prev = None
    count = 0
    gain = loss = 0.0
    for raw in closes:
        try:
            v = float(raw) if not isinstance(raw, bool) else float("nan")
        except (TypeError, ValueError, OverflowError):
            v = float("nan")
        if not math.isfinite(v) or v <= 0:
            prev = None
            count = 0
            gain = loss = 0.0
            out.append(None)
            continue
        if prev is None:
            prev = v
            out.append(None)
            continue
        d = v-prev
        prev = v
        g, l = max(d, 0.0), max(-d, 0.0)
        count += 1
        if count <= period:
            gain += g
            loss += l
            if count < period:
                out.append(None)
                continue
            gain /= period
            loss /= period
        else:
            gain = (gain*(period-1)+g)/period
            loss = (loss*(period-1)+l)/period
        total = gain + loss
        if not math.isfinite(total):
            prev = None
            count = 0
            gain = loss = 0.0
            out.append(None)
            continue
        out.append(50.0 if total == 0 else 100.0 * (gain / total))
    return out

def wilder_rsi_series(close, period=14):
    """Pandas compatibility adapter for the shared pure calculation."""
    import pandas as pd
    return pd.Series(wilder_rsi_values(close, period), index=close.index,
                     name=close.name, dtype=float)
