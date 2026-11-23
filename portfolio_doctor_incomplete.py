"""Privacy-safe incomplete Portfolio Doctor diagnostic, NOT a financial report.

Only validated holding symbols and small whitelisted failure codes may cross
the disposable worker boundary. Never include prices, quantities, costs,
customer IDs, raw uploads, source URLs, or database credentials.
"""
import html
import json
import re

_KIND = "MSK_DOCTOR_INCOMPLETE_V1"
_CODES = frozenset(("NO_PRICES", "STALE_PRICES"))
_SYMBOL = re.compile(r"[A-Z0-9&-]{2,20}\Z")
_MAX_BYTES = 1024


def _clean_failures(failures, *, allowed_symbols=None):
    if not isinstance(failures, dict) or not 1 <= len(failures) <= 5:
        return {}
    allowed = None if allowed_symbols is None else set(allowed_symbols)
    cleaned = {}
    for symbol, code in failures.items():
        if (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                or not isinstance(code, str) or code not in _CODES
                or (allowed is not None and symbol not in allowed)):
            return {}
        cleaned[symbol] = code
    return dict(sorted(cleaned.items()))


def encode_incomplete_diagnostic(failures):
    """Whitelisted data for stdout ONLY on an incomplete worker failure."""
    clean = _clean_failures(failures)
    if not clean:
        return b""
    payload = json.dumps({"kind": _KIND, "unverified": clean},
                         sort_keys=True, separators=(",", ":")).encode("ascii")
    return payload if len(payload) <= _MAX_BYTES else b""


def decode_incomplete_diagnostic(raw, requested_holdings):
    """Do not trust a child-process response without validating every symbol."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_BYTES:
        return {}
    if (not isinstance(requested_holdings, list) or
            not 1 <= len(requested_holdings) <= 5):
        return {}
    allowed = [h.get("symbol") for h in requested_holdings if isinstance(h, dict)]
    if len(allowed) != len(requested_holdings):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, TypeError):
        return {}
    if not isinstance(data, dict) or set(data) != {"kind", "unverified"}:
        return {}
    if data["kind"] != _KIND:
        return {}
    return _clean_failures(data["unverified"], allowed_symbols=allowed)


def private_incomplete_message(failures):
    """For authenticated PRIVATE Telegram chat only. Never use in logs."""
    clean = _clean_failures(failures)
    if not clean:
        return None
    lines = [
        "🩺 <b>INCOMPLETE PRICE DIAGNOSTIC — NOT A PORTFOLIO REPORT</b>",
        "A complete valuation, allocation and P&amp;L report was NOT generated.",
        "",
        "<b>Holdings without verified current closing prices:</b>",
    ]
    for symbol, code in clean.items():
        reason = ("Latest completed closing session is missing or stale"
                  if code == "STALE_PRICES" else
                  "Verified completed daily price data is unavailable")
        lines.append("• <code>" + html.escape(symbol) + "</code>: " + reason)
    lines += [
        "",
        "Check the symbol and exchange. Try again when official closing data is available.",
        "No partial totals, estimated prices, investment advice or holdings storage.",
    ]
    return "\n".join(lines)
