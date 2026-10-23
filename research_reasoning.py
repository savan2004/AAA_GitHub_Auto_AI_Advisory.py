"""Explainable, conservative top-down + bottom-up stock research.

Inputs are computed market histories and retrieved vendor financial metrics.
These are not independently verified exchange statements; this module never
upgrades such evidence into a regulatory-compliant BUY recommendation.
"""
from __future__ import annotations
import math
from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def build_reasoning(nifty, technicals: dict, fundamentals: dict,
                    scores: dict, sector: str | None = None) -> dict:
    """Build source-limited independent reasons, risks and evidence gaps.

    No macro, industry or market-cap assertions are made without such data.
    This output is an educational draft, not an automated trade decision.
    """
    top: list[str] = []
    bottom: list[str] = []
    risks: list[str] = []
    missing: list[str] = []
    market_condition = "NOT VERIFIED"

    # TOP-DOWN: observed Nifty context, relative strength, then sector gap.
    try:
        closing = nifty["Close"].dropna() if nifty is not None else None
        if closing is None or len(closing) < 200:
            missing.append("Nifty 50: at least 200 complete benchmark daily closing prices")
        else:
            current = float(closing.iloc[-1]); avg50 = float(closing.iloc[-50:].mean())
            avg200 = float(closing.iloc[-200:].mean())
            if not all(math.isfinite(x) and x > 0 for x in (current, avg50, avg200)):
                missing.append("Valid and timestamp-aligned Nifty benchmark pricing")
            elif current > avg50 > avg200:
                market_condition = "BENCHMARK UPTREND"
                top.append(f"Nifty closes above its 50D and 200D averages ({current:.2f} > {avg50:.2f} > {avg200:.2f}).")
            elif current < avg50 < avg200:
                market_condition = "BENCHMARK DOWNTREND"
                risks.append(f"Nifty closes below its 50D and 200D averages ({current:.2f} < {avg50:.2f} < {avg200:.2f}).")
            else:
                market_condition = "BENCHMARK MIXED"
                top.append("Nifty 50 moving-average structure is mixed; broad-market confirmation is limited.")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        missing.append("Reliable Nifty benchmark series")

    relative = _num(((technicals.get("returns") or {}).get("3M") or {}).get("rel"))
    if relative is None:
        missing.append("Stock 3-month relative return versus Nifty, with matched dates")
    elif relative >= 5:
        top.append(f"Stock outperformed Nifty by {relative:.1f} percentage points over 3 months (observed).")
    elif relative <= -5:
        risks.append(f"Stock underperformed Nifty by {abs(relative):.1f} percentage points over 3 months (observed).")
    else:
        top.append(f"Three-month relative performance vs Nifty is {relative:+.1f} percentage points (near benchmark).")

    if sector:
        top.append(f"Sector classification: {sector}. Sector relative-strength data were not supplied.")
    missing.extend(["Sector index strength, breadth and earnings revisions",
                    "Verified RBI/inflation/FX/rates inputs and their observation dates"])

    # BOTTOM-UP: use ratios only when present, never invent strong balance sheets.
    roe = _num(fundamentals.get("roe"))
    if roe is None:
        missing.append("ROE from dated statements")
    elif roe >= 15:
        bottom.append(f"ROE is {roe:.1f}% — screen for capital efficiency; confirm leverage and source period.")
    elif roe < 8:
        risks.append(f"ROE is only {roe:.1f}%; investigate industry comparability and capital structure.")
    else:
        bottom.append(f"ROE is {roe:.1f}%; peer and history comparisons are required.")

    cash = _num(fundamentals.get("ocf_pat"))
    if cash is None:
        missing.append("Multi-year operating-cash-flow to net-profit ratio")
    elif cash >= 1:
        bottom.append(f"Operating cash flow / reported profit is {cash:.2f} (period must be verified).")
    elif cash < 0.6:
        risks.append(f"Operating cash flow / reported profit is {cash:.2f}; check earnings conversion.")
    else:
        bottom.append(f"Cash conversion ratio is {cash:.2f}; review its full time series.")

    de = _num(fundamentals.get("de"))
    if de is not None and de > 1:
        risks.append(f"Debt/equity is {de:.2f}; inspect interest cover and refinancing.")
    elif de is not None:
        bottom.append(f"Debt/equity is {de:.2f}; verify liabilities and sector norms.")
    elif not fundamentals.get("fin"):
        missing.append("Debt/equity, interest cover and maturity ladder")

    pe, peer_pe = _num(fundamentals.get("pe")), _num(fundamentals.get("peer_pe"))
    if pe is not None and pe > 0 and peer_pe is not None and peer_pe > 0:
        diff = (pe / peer_pe - 1) * 100
        bottom.append(f"Trailing P/E {pe:.1f}x versus listed peer median {peer_pe:.1f}x ({diff:+.1f}% premium/discount).")
    else:
        missing.append("Comparable earnings valuation and dated peer median")

    if not fundamentals.get("has_statements"):
        missing.append("Verified time-series of consolidated/standalone financial statements")
    if scores.get("insufficient_evidence"):
        risks.append("Fundamental scoring coverage below mandatory evidence threshold.")
    if market_condition == "NOT VERIFIED":
        risks.append("Benchmark market regime cannot be established.")

    return {
        "market_regime": market_condition,
        "top_down_evidence": top,
        "bottom_up_evidence": bottom,
        "counter_thesis": risks,
        "data_gaps": list(dict.fromkeys(missing)),
        "publication_eligible": False,
        "publication_reason": "Cross-provider/exchange filing provenance, freshness, and regulatory review are not implemented.",
    }
