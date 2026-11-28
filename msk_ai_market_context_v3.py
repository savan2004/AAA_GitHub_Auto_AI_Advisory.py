"""Render dated index observations using the existing verified quote boundary."""
from math import isfinite

def market_context(quote=None):
    if quote is None:
        try:
            from data_engine import get_verified_quote as quote
        except Exception:
            return "Live market context unavailable; state missing values explicitly."
    rows = []
    for label, symbol in (("Nifty 50", "^NSEI"), ("Bank Nifty", "^NSEBANK")):
        try:
            q = quote(symbol)
            if not isinstance(q, dict):
                raise ValueError
            price = q.get("price")
            stamp, source, status = (q.get(k) for k in ("quote_asof_ist", "quote_source", "quote_status"))
            if (type(price) not in (int, float) or not isfinite(price) or price <= 0
                    or not isinstance(stamp, str) or not stamp
                    or not isinstance(source, str) or not source
                    or status not in ("recent_observation", "last_observed_session")):
                raise ValueError
            rows.append(f"{label}: Rs {price:,.2f} | {stamp} | {source} | {status.replace('_', ' ')}")
        except Exception:
            rows.append(f"{label}: timestamp-verified observation unavailable.")
    return "\n".join(rows)
