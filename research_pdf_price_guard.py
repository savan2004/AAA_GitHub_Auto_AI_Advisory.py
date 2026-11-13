"""Independent guard for EOD prices used by real research PDFs.

Reports must use the latest completed NSE session; weekend never means
today's nonexistent candle. A PDF text extractor is not a data validator.
"""
import math


def verified_completed_price_session(symbol, required_session, fetch_history, context):
    if symbol not in ("SBIN", "DLF"):
        raise ValueError("Issuer not permitted in automatic PDF acceptance")
    history = fetch_history(symbol, "1y")
    if history is None or getattr(history, "empty", True):
        raise RuntimeError("Real company EOD history not available")
    last = history.index[-1]
    if not callable(getattr(last, "date", None)):
        raise RuntimeError("EOD history has no verified dated candle")
    actual_date = str(last.date())
    if actual_date != str(required_session):
        raise RuntimeError("Real issuer history is not the latest completed session")
    checked = context(history)
    if not isinstance(checked, dict):
        raise RuntimeError("Real company price safety check failed")
    try:
        close = float(checked["historical_close"])
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError("Actual completed-session close unavailable") from exc
    if not math.isfinite(close) or close <= 0:
        raise RuntimeError("Actual completed-session close invalid")
    return actual_date, close
