"""Deterministic, fail-closed stock analysis scoring. No external calls."""
from math import isfinite

def _num(value):
    try:
        v = float(value)
        return v if isfinite(v) else None
    except (TypeError, ValueError):
        return None

def assess_stock(metrics):
    """Return evidence-based score, coverage and risk flags; never infer missing data."""
    rules = (
        ("roe", lambda x: x >= 15, "ROE ≥15%"),
        ("debt_equity", lambda x: x <= 0.5, "Debt/equity ≤0.5"),
        ("revenue_growth", lambda x: x > 0, "Positive revenue growth"),
        ("earnings_growth", lambda x: x > 0, "Positive earnings growth"),
        ("operating_margin", lambda x: x >= 15, "Operating margin ≥15%"),
        ("free_cash_flow", lambda x: x > 0, "Positive free cash flow"),
    )
    passed, observed, flags = [], [], []
    for key, predicate, description in rules:
        value = _num(metrics.get(key))
        if value is None:
            continue
        observed.append(key)
        if predicate(value):
            passed.append(description)
        else:
            flags.append(description + " not met")
    coverage = len(observed) / len(rules)
    score = round(10 * len(passed) / len(observed), 1) if observed else None
    return {
        "score": score if len(observed) >= 4 else None,
        "coverage": round(coverage * 100),
        "observed": observed,
        "strengths": passed,
        "risks": flags,
        "verdict": ("Insufficient verified data" if len(observed) < 4 else
                    "Strong fundamentals" if score >= 8 else
                    "Mixed fundamentals" if score >= 5 else "Weak fundamentals"),
    }
