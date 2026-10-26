"""Authenticated, fail-closed bridge exposing the EXISTING full MSK PDF engine.

The Channel calls this internal endpoint using its own bot-service credentials.
No Telegram post, webhook ownership change, AI commentary, fallback mini PDF,
or fabricated company facts happen here.
"""
from __future__ import annotations
import hmac
import io
import logging
import os
import re
import threading
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, send_file

bp = Blueprint("full_research_bridge", __name__)
_lock = threading.Lock()
log = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9&.-]{0,19}$")


def validate_full_inputs(inp: dict, as_of=None) -> tuple[bool, str]:
    """A 3-page ratio snapshot is never eligible for 'full research'.

    Require actual 3+ complete yearly and 3+ quarterly financial observations,
    at least 200 EOD OHLCV candles, and non-stale financial statements.
    """
    df = inp.get("df")
    if df is None or len(df) < 200:
        return False, "need_200_completed_price_sessions"
    stm = inp.get("stm") or {}
    years, quarters = stm.get("years") or [], stm.get("quarters") or []
    valid_annual = [v for v in years
                    if isinstance(v, dict)
                    and isinstance(v.get("revenue"), (int, float)) and v["revenue"] > 0
                    and isinstance(v.get("net_profit"), (int, float))]
    valid_quarters = [v for v in quarters
                      if isinstance(v, dict)
                      and isinstance(v.get("revenue"), (int, float)) and v["revenue"] > 0
                      and isinstance(v.get("net_profit"), (int, float))]
    if len(valid_annual) < 3:
        return False, "need_three_verified_annual_statements"
    if len(valid_quarters) < 3:
        return False, "need_three_verified_quarterly_statements"
    # Honor existing research-data accounting freshness determination.
    if (stm.get("freshness") or {}).get("status") in ("STALE", "MISSING"):
        return False, "financial_statements_stale"
    try:
        current = as_of or datetime.now(_IST)
        last = df.index[-1]
        day = last.date() if hasattr(last, "date") else datetime.fromisoformat(str(last)[:10]).date()
        if (current.date() - day).days > 7 or day > current.date():
            return False, "price_session_stale"
    except Exception:
        return False, "unverifiable_price_session"
    return True, ""


def approx_pdf_pages(raw: bytes) -> int:
    """ReportLab writes /Type /Page page dictionaries outside compressed streams."""
    if not raw.startswith(b"%PDF-"):
        return 0
    return len(re.findall(rb"/Type\s*/Page\b", raw))


@bp.route("/api/internal/full-research-pdf", methods=["POST"])
def full_research_pdf():
    secret = os.getenv("MSK_REPORT_BRIDGE_KEY", "").strip()
    supplied = request.headers.get("X-MSK-Report-Key", "")
    if len(secret) < 40:
        return jsonify(error="research_bridge_not_configured"), 503
    if not supplied or not hmac.compare_digest(secret, supplied):
        return jsonify(error="unauthorized"), 401
    payload = request.get_json(silent=True)
    sym = str((payload or {}).get("symbol", "")).upper().strip() if isinstance(payload, dict) else ""
    if not _SYMBOL.fullmatch(sym):
        return jsonify(error="invalid_nse_symbol"), 400
    if not _lock.acquire(blocking=False):
        return jsonify(error="report_generator_busy"), 503
    out = None
    try:
        # Import inside request to preserve MSK startup/telegram flow.
        from report_generator import collect_inputs, assemble, build_pdf
        from tempfile import NamedTemporaryFile
        inputs = collect_inputs(sym)
        ok, why = validate_full_inputs(inputs)
        if not ok:
            log.warning("[Full report bridge] %s withheld: %s", sym, why)
            return jsonify(error="insufficient_full_report_evidence", detail=why), 422
        report = assemble(inputs, use_ai=False)
        if (report.get("sc") or {}).get("insufficient_evidence"):
            return jsonify(error="insufficient_fundamental_score_evidence"), 422
        if not (report.get("ch") or {}).get("tech_price") or not (report.get("ch") or {}).get("tech_momentum"):
            return jsonify(error="advanced_technical_chart_not_verified"), 422
        with NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        build_pdf(report, out)
        with open(out, "rb") as f:
            pdf = f.read()
        pages = approx_pdf_pages(pdf)
        if pages < 8 or len(pdf) < 45_000 or len(pdf) > 25_000_000:
            log.warning("[Full report bridge] %s PDF failed document integrity (pages=%d)", sym, pages)
            return jsonify(error="full_report_document_integrity_failed"), 422
        response = send_file(io.BytesIO(pdf), mimetype="application/pdf",
                             as_attachment=True, download_name=f"{sym}_MSK_Full_Research.pdf")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-MSK-Report-Pages"] = str(pages)
        response.headers["X-MSK-Report-Source"] = "MSK full research engine"
        log.info("[Full report bridge] %s generated approved %d-page research PDF", sym, pages)
        return response
    except Exception:
        log.exception("[Full report bridge] %s generation unavailable", sym)
        return jsonify(error="full_report_generation_unavailable"), 503
    finally:
        if out:
            try: os.unlink(out)
            except OSError: pass
        _lock.release()
