"""Professional stock-analysis decision gates, separate from data collection."""
from datetime import datetime, timezone
from math import isfinite

def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if isfinite(number) else None

def assess_valuation(metrics):
    """Never declare cheap/expensive from PE alone or from missing peers."""
    pe = finite_number(metrics.get("pe"))
    peer = finite_number(metrics.get("peer_median_pe"))
    if pe is None or peer is None or pe <= 0 or peer <= 0:
        return {"verdict":"Insufficient comparable valuation data", "premium_pct":None}
    premium = round((pe / peer - 1) * 100, 1)
    return {"verdict":("Premium to peers" if premium > 15 else
                        "Discount to peers" if premium < -15 else
                        "Near peer median"),
            "premium_pct":premium}

def assess_data_quality(fields, required, as_of=None, max_age_days=120, now=None):
    """Explicit completeness and freshness; missing timestamp is not fresh."""
    present = [key for key in required if finite_number(fields.get(key)) is not None]
    missing = [key for key in required if key not in present]
    timestamp = None
    try:
        timestamp = datetime.fromisoformat(as_of.replace("Z","+00:00")) if as_of else None
        if timestamp and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        timestamp = None
    current = now or datetime.now(timezone.utc)
    age = (current - timestamp).total_seconds() / 86400 if timestamp else None
    fresh = age is not None and 0 <= age <= max_age_days
    return {"coverage_pct":round(100 * len(present)/len(required)) if required else 100,
            "missing":missing,"fresh":fresh,"age_days":round(age,1) if age is not None else None,
            "usable_for_verdict":fresh and not missing}

def decision_gate(financial, valuation, technical, quality):
    """Avoid actionable recommendations without validated evidence."""
    if not quality.get("usable_for_verdict"):
        return {"stance":"DATA INSUFFICIENT","reason":"Missing or stale verified data"}
    if financial.get("score") is None:
        return {"stance":"DATA INSUFFICIENT","reason":"Financial coverage below minimum"}
    if technical.get("status") != "OK":
        return {"stance":"DATA INSUFFICIENT","reason":"Technical history not validated"}
    if valuation.get("premium_pct") is None:
        return {"stance":"RESEARCH ONLY","reason":"Comparable valuation unavailable"}
    score = financial["score"]
    trend = technical.get("trend")
    premium = valuation["premium_pct"]
    if score >= 8 and trend == "Bullish" and premium <= 15:
        return {"stance":"POSITIVE WATCHLIST","reason":"Quality, trend and peer valuation aligned"}
    if score < 5 or (premium > 40 and trend == "Bearish"):
        return {"stance":"HIGH CAUTION","reason":"Weak fundamentals or stretched valuation with weak trend"}
    return {"stance":"NEUTRAL WATCHLIST","reason":"Evidence is mixed; no clear edge"}
