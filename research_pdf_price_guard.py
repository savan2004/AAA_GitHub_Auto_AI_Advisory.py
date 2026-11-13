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


def verified_generated_report_session(symbol, required_session, evidence):
    """Validate EXACT values used in build_pdf(), not a second market request."""
    from datetime import datetime
    if symbol not in ("SBIN", "DLF"):
        raise ValueError("Issuer not permitted in automatic PDF acceptance")
    if not isinstance(evidence, dict) or evidence.get("symbol") != symbol:
        raise RuntimeError("PDF generation provenance missing or wrong company")
    actual_label = evidence.get("price_session")
    try:
        actual = datetime.strptime(actual_label, "%d-%b-%Y").date()
        expected = datetime.strptime(required_session, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Actual PDF market session not recorded") from exc
    if actual != expected:
        raise RuntimeError(
            "PDF source session is not latest completed market date "
            "(actual={}, expected={})".format(actual.isoformat(), expected.isoformat()))
    try:
        close = float(evidence["historical_close"])
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("Actual PDF source close absent") from exc
    if not math.isfinite(close) or close <= 0:
        raise RuntimeError("Actual PDF source close invalid")
    return actual.isoformat(), close
