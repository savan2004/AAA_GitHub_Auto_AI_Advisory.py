"""Strict, command-free MSK group stock-request detection.

No external calls here: only explicitly addressed messages trigger expensive work.
"""
import re

ALIASES = {
    "reliance industries": "RELIANCE", "reliance": "RELIANCE",
    "tata motors": "TATAMOTORS", "hdfc bank": "HDFCBANK",
    "icici bank": "ICICIBANK", "state bank of india": "SBIN",
    "sbi": "SBIN", "infosys": "INFY", "tcs": "TCS",
    "tata consultancy services": "TCS", "wipro": "WIPRO",
    "itc": "ITC", "bharti airtel": "BHARTIARTL",
    "bajaj finance": "BAJFINANCE", "larsen and toubro": "LT",
}
REQUEST_WORDS = re.compile(
    r"\\b(review|analyse|analyze|analysis|technical|technicals|chart|trend|outlook|"
    r"bullish|bearish|what about|how is|check|share|view|opinion)\\b", re.I
)
ADDRESS = re.compile(r"(?<![\\w])(?:msk|@mskbot)(?![\\w])", re.I)
TICKER = re.compile(r"^[A-Z][A-Z0-9&-]{1,14}$")


def extract_stock_request(text):
    """Return a ticker/known company name or None. Never guess from group chatter."""
    if not isinstance(text, str) or len(text) > 280:
        return None
    if not ADDRESS.search(text) or not REQUEST_WORDS.search(text):
        return None
    clean = ADDRESS.sub(" ", text)
    clean = re.sub(r"[^A-Za-z0-9&.\\- ]+", " ", clean).strip()
    lowered = clean.lower()
    found = [(alias, sym) for alias, sym in ALIASES.items()
             if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered)]
    unique = {sym for _, sym in found}
    if len(unique) == 1:
        return next(iter(unique))
    if len(unique) > 1:
        return None
    # Explicit ticker formats only. A bare English word must never become a ticker.
    match = re.search(r"\\b(?:NSE[: ]+|stock[: ]+|ticker[: ]+|\\$)([A-Za-z][A-Za-z0-9&-]{1,14})(?:\\.NS)?\\b", clean, re.I)
    if match and TICKER.fullmatch(match.group(1).upper()):
        return match.group(1).upper()
    return None
