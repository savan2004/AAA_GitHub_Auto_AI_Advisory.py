"""Bounded public filing discovery for missing MSK financial evidence.

AI output is a lead only, never a financial observation. No URL is fetched
here; independent issuer/period/scope/metric verification must occur before
any downstream financial approval. No customer data, Neon or Telegram.
"""
from collections import OrderedDict
from datetime import date
import json
import os
import re
import threading

_MAX_WEEKLY = 20
_lock = threading.Lock()
_week = None
_attempts = OrderedDict()
_SYMBOL = re.compile(r"[A-Z0-9&-]{2,20}\Z")
_URL = re.compile(
    r"https://nsearchives\.nseindia\.com/corporate/ixbrl/"
    r"INTEGRATED_FILING_INDAS_[0-9]{1,30}_[0-9]{8,20}_iXBRL_WEB\.html\Z"
)
_FIELDS = frozenset(("revenue", "net_profit", "eps", "annual_history",
                     "latest_quarter"))
_PROVIDERS = ("groq", "gemini")

def refresh_due(last_review, *, today, kind, new_filing=False):
    """Calendar review policy, not a scheduler or a market-price TTL."""
    if kind not in ("missing", "technical", "fundamental"):
        raise ValueError("unknown review kind")
    if last_review is None or new_filing:
        return True
    if not isinstance(last_review, date) or last_review > today:
        return True
    if kind == "fundamental":
        return (last_review.year, last_review.month) != (today.year, today.month)
    return last_review.isocalendar()[:2] != today.isocalendar()[:2]

def discover_filing(symbol, period_end, missing, *, today=None, env=None, ask=None):
    """At most one configured free-provider attempt per stock per ISO week.

    Return a strictly shaped public source lead or a withholding reason.
    Process-local limits reset on restart; deployment remains opt-in until
    free entitlement and a durable shared review cadence are confirmed.
    """
    global _week
    env = os.environ if env is None else env
    provider = env.get("MSK_AI_RECOVERY_FREE_PROVIDER", "").strip().lower()
    if provider not in _PROVIDERS:
        return {"status": "disabled"}
    from msk_ai_connector_v2 import configured_providers, request_ai
    # Copy only the explicitly selected provider key/model, never allow paid
    # or AskFuzz fallbacks even if enabled for unrelated features.
    keys = ("GROQ_API_KEY", "MSK_GROQ_MODEL") if provider == "groq" else (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "MSK_GEMINI_MODEL")
    safe_env = {k: env[k] for k in keys if k in env}
    if not configured_providers(safe_env):
        return {"status": "provider_unavailable"}
    today = today or date.today()
    if (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
            or not isinstance(period_end, date) or period_end > today
            or not missing or any(x not in _FIELDS for x in missing)):
        return {"status": "invalid_request"}
    week = today.isocalendar()[:2]
    with _lock:
        if _week != week:
            _attempts.clear()
            _week = week
        previous = _attempts.get(symbol)
        if previous is not None:
            old_period, outcome = previous
            return dict(outcome) if old_period == period_end else {"status": "weekly_limit"}
        if len(_attempts) >= _MAX_WEEKLY:
            return {"status": "weekly_limit"}
        _attempts[symbol] = (period_end, {"status": "in_progress"})
    result = {"status": "withheld_no_lead"}
    try:
        response = (ask or request_ai)(
            [{"role": "user", "content":
              f"Public NSE ticker {symbol}; quarter ended {period_end.isoformat()}; "
              f"missing evidence: {', '.join(sorted(set(missing)))}. "
              "If you know an exact official NSE integrated financial filing URL, "
              "return JSON with only source_url. Otherwise return null. "
              "Never guess a link, report a financial figure or claim it is verified."}],
            system="Find source leads only. Output JSON object with one source_url key or null.",
            max_tokens=180, financial=False, env=safe_env)
        if response.ok:
            candidate = json.loads(response.text)
            if (isinstance(candidate, dict) and set(candidate) == {"source_url"}
                    and isinstance(candidate["source_url"], str)
                    and _URL.fullmatch(candidate["source_url"])):
                result = {"status": "unverified_lead", "source_url": candidate["source_url"],
                          "period_end": period_end.isoformat()}
        else:
            result = {"status": "provider_unavailable"}
    except Exception:
        result = {"status": "withheld_no_lead"}
    with _lock:
        if _week == week:
            _attempts[symbol] = (period_end, result)
    return dict(result)
