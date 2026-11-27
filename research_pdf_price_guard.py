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


class PublicPDFEvidenceError(ValueError):
    """Fixed, non-sensitive acceptance error codes."""


def public_pdf_reference(session, symbols, *, fetch=None, archive=None):
    """One primary official request and one final-archive fallback for missing rows.

    Never replace an observed primary row or use a different exchange/date.
    No persistent cache or customer data. Both sources are official NSE files.
    """
    import re
    from datetime import date
    wanted = tuple(dict.fromkeys(symbols))
    if (not isinstance(session, date) or not 1 <= len(wanted) <= 10
            or not all(isinstance(s, str) and re.fullmatch(r"[A-Z0-9&-]{2,25}", s)
                       for s in wanted)):
        raise PublicPDFEvidenceError("INVALID_PUBLIC_SAMPLE")
    if fetch is None:
        from portfolio_doctor_bhavcopy import fetch_nse_bhavcopy_rows
        fetch = fetch_nse_bhavcopy_rows
    if archive is None:
        from smart_chart_eod_recovery import final_archive_rows
        archive = final_archive_rows
    rows = fetch(session, wanted) or {}
    result = {s: rows[s] for s in wanted if s in rows}
    missing = [s for s in wanted if s not in result]
    if missing:
        secondary = archive(session, [s + ".NS" for s in missing]) or {}
        result.update({s: secondary[s + ".NS"] for s in missing if s + ".NS" in secondary})
    return result


def verify_public_pdf_price(symbol, expected, evidence, reference):
    """Check the exact bar that generated the PDF against the official record."""
    from datetime import datetime
    from portfolio_doctor_bhavcopy import official_daily_bar_matches
    if not isinstance(evidence, dict) or evidence.get("symbol") != symbol:
        raise PublicPDFEvidenceError("REPORT_SYMBOL_MISMATCH")
    try:
        actual = datetime.strptime(evidence["price_session"], "%d-%b-%Y").date()
    except (KeyError, TypeError, ValueError):
        raise PublicPDFEvidenceError("REPORT_SESSION_MISSING") from None
    if actual != expected:
        raise PublicPDFEvidenceError("REPORT_SESSION_MISMATCH")
    if symbol not in reference:
        raise PublicPDFEvidenceError("OFFICIAL_REFERENCE_MISSING")
    try:
        close = float(evidence["historical_close"])
        official_close = float(reference[symbol]["Close"])
        raw_close = float(evidence["price_bar"]["Close"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PublicPDFEvidenceError("REPORT_PRICE_EVIDENCE_MISSING") from None
    if not all(math.isfinite(x) and x > 0 for x in (close, official_close, raw_close)):
        raise PublicPDFEvidenceError("REPORT_PRICE_INVALID")
    if abs(close - raw_close) > .005 + 1e-9:
        raise PublicPDFEvidenceError("REPORT_RENDER_PRICE_MISMATCH")
    if abs(close - official_close) > .05 + 1e-9:
        raise PublicPDFEvidenceError("OFFICIAL_CLOSE_MISMATCH")
    if not official_daily_bar_matches(evidence["price_bar"], reference[symbol]):
        raise PublicPDFEvidenceError("OFFICIAL_OHLCV_MISMATCH")
    return actual, close
