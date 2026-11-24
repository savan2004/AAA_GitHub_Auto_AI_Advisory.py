"""Optional third-party Indian financial-data probe; NEVER customer publication.

This adapter is run from a separate research/CI process, NOT inside the
always-on Telegram worker. Importing finstack may patch global HTTP clients;
the MSK bot and existing price/filing verifiers are deliberately unaffected.

No upstream source code is copied or vendored. Its project/code MIT licence
does NOT grant exchange data redistribution, nor does it authenticate filings.
All output is explicitly marked research-only/unverified.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import importlib
import json
import re
import sys


class ProbeRejected(ValueError):
    """Provider returned ambiguous, missing, inconsistent or unverifiable data."""


SYMBOL = re.compile(r"[A-Z][A-Z0-9&-]{0,19}\Z")
_MAX_ROWS = 100
_MAX_ISSUES = 5


def _symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        raise ProbeRejected("SYMBOL_INVALID")
    return value


def _value(value, *, positive=False):
    if value is None or isinstance(value, bool):
        raise ProbeRejected("VALUE_MISSING")
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ProbeRejected("VALUE_INVALID") from None
    if not x.is_finite() or (positive and x <= 0):
        raise ProbeRejected("VALUE_INVALID")
    return x


def _dated(value):
    # pandas Timestamp has date(); datetime is accepted too. Avoid
    # interpreted, ambiguous date text or silently using today's date.
    if isinstance(value, datetime):
        return value.date()
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]) if len(value) == 10 else None
        except ValueError:
            return None
    if hasattr(value, "date"):
        v = value.date()
        return v if type(v) is date else None
    return None


def _records(frame, *, required):
    """Accept a normalized DataFrame-like result without trusting metadata."""
    if frame is None or not hasattr(frame, "to_dict"):
        raise ProbeRejected("SOURCE_FRAME_UNAVAILABLE")
    records = frame.to_dict(orient="records")
    if not isinstance(records, list) or not 1 <= len(records) <= _MAX_ROWS:
        raise ProbeRejected("SOURCE_ROWS_INVALID")
    if any(not isinstance(row, dict) or not required.issubset(row) for row in records):
        raise ProbeRejected("SOURCE_FIELDS_MISSING")
    return records


def _provenance(frame):
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        raise ProbeRejected("SOURCE_PROVENANCE_MISSING")
    name = attrs.get("source")
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ProbeRejected("SOURCE_PROVENANCE_MISSING")
    # The library may use other upstream sources (including commercial
    # restricted sites), so its source label is descriptive, not licensed.
    return re.sub(r"[^A-Za-z0-9_./: -]", "", name)[:100]


def _fetch(provider):
    if provider is not None:
        return provider
    try:
        # Called only in an isolated research subprocess, not app startup.
        return importlib.import_module("finstack").fetch
    except (ImportError, AttributeError):
        raise ProbeRejected("FINSTACK_NOT_INSTALLED") from None


def quarter_observation(symbol, period, *, provider=None):
    """Return one exact-quarter candidate, never an independently verified row."""
    _symbol(symbol)
    if type(period) is not date:
        raise ProbeRejected("PERIOD_INVALID")
    fetch = _fetch(provider)
    try:
        frame = fetch("india_company_financials", symbol,
                      statement="quarterly", consolidated=True)
    except Exception:
        raise ProbeRejected("FINSTACK_FETCH_FAILED") from None
    source = _provenance(frame)
    rows = _records(frame, required={"period_end", "revenue", "net_profit"})
    selected = [row for row in rows if _dated(row["period_end"]) == period]
    if len(selected) != 1:
        raise ProbeRejected("PERIOD_NOT_UNIQUE_OR_MISSING")
    row = selected[0]
    if "symbol" in row and str(row["symbol"]).upper() != symbol:
        raise ProbeRejected("ISSUER_IDENTITY_MISMATCH")
    if "units" not in row or str(row["units"]) != "INR crore (EPS in INR)":
        raise ProbeRejected("SOURCE_UNITS_UNCONFIRMED")
    revenue = _value(row["revenue"], positive=True)
    profit = _value(row["net_profit"])
    # Consolidation basis may be unspecified or 'as filed' by this library.
    # In that case, never infer consolidated from a requested argument.
    basis = str(row.get("basis") or "").lower()
    scope = "consolidated" if basis == "consolidated" else (
        "standalone" if basis == "standalone" else "UNCONFIRMED")
    return {
        "symbol": symbol, "period": period.isoformat(),
        "revenue_cr": str(revenue), "profit_cr": str(profit),
        "reported_basis": scope, "provider": source,
        "publication_status": "WITHHELD",
        "source_verification": "UNVERIFIED_EXTERNAL_CANDIDATE",
        "next_action": "Compare issuer documents and reporting scope independently; check permission for commercial distribution.",
    }


def eod_observation(symbol, session, *, provider=None):
    """Check one exact completed historical day, not a quote or latest tick."""
    _symbol(symbol)
    if type(session) is not date:
        raise ProbeRejected("SESSION_INVALID")
    fetch = _fetch(provider)
    try:
        frame = fetch("india_daily_prices", symbol, start=session.isoformat(),
                      end=session.isoformat())
    except Exception:
        raise ProbeRejected("FINSTACK_FETCH_FAILED") from None
    source = _provenance(frame)
    rows = _records(frame, required={"date", "close"})
    selected = [row for row in rows if _dated(row["date"]) == session]
    if len(selected) != 1:
        raise ProbeRejected("SESSION_NOT_UNIQUE_OR_MISSING")
    row = selected[0]
    if "symbol" in row and str(row["symbol"]).upper() != symbol:
        raise ProbeRejected("ISSUER_IDENTITY_MISMATCH")
    price = _value(row["close"], positive=True)
    return {
        "symbol": symbol, "session": session.isoformat(),
        "close_rupees": str(price), "provider": source,
        "publication_status": "WITHHELD",
        "source_verification": "UNVERIFIED_EXTERNAL_CANDIDATE",
        "next_action": "Reconcile with official exchange bhavcopy and corporate actions before any customer report.",
    }


def main(argv=None):
    """Operator research probe. Never prints provider exception bodies."""
    import argparse
    p = argparse.ArgumentParser(description="MSK read-only external-source trial")
    p.add_argument("kind", choices=("quarter", "eod"))
    p.add_argument("symbol")
    p.add_argument("date", help="Exact ISO date YYYY-MM-DD")
    args = p.parse_args(argv)
    try:
        observation_date = date.fromisoformat(args.date)
        result = (quarter_observation(args.symbol, observation_date) if args.kind == "quarter"
                  else eod_observation(args.symbol, observation_date))
        status = 0
    except (ProbeRejected, ValueError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z_]{3,80}", code):
            code = "PROBE_UNAVAILABLE"
        result = {"symbol": args.symbol if SYMBOL.fullmatch(args.symbol) else "INVALID",
                  "status": code, "publication_status": "WITHHELD"}
        status = 1
    print(json.dumps(result, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
