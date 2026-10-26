"""Filing-backed business models and *illustrative* three-year segment scenarios.

Source facts and our hypothetical assumptions are deliberately separate.
Companies without verified segment evidence do not get invented business models,
growth estimates or price targets.
"""
from __future__ import annotations

import math
from typing import Any, Optional

RIL_FY26_REPORT = "https://www.ril.com/ar2025-26/index.html"
RIL_FY26_MDA = "https://www.ril.com/ar2025-26/mda.html"

# Consolidated annual report FY2025-26, rupees crore.
# Business segment figures cannot be added to consolidated revenue/EBITDA because
# inter-segment eliminations, segment classifications and adjustments differ.
PROFILES: dict[str, dict[str, Any]] = {
    "RELIANCE": {
        "name": "Reliance Industries Limited",
        "fiscal_year": "FY2025-26",
        "asof": "2026-03-31",
        "unit": "INR crore",
        "source": RIL_FY26_REPORT,
        "business_summary": (
            "Diversified group with energy and chemicals, telecom/digital "
            "platforms, retail, media and upstream oil and gas. "
            "The sources of growth and cash generation differ by segment."
        ),
        "capital_allocation": (
            "Track group capital expenditure, incremental returns and net debt "
            "alongside segment EBITDA. New energy requires separately verified "
            "capital deployment and commercial revenue before valuation."
        ),
        "segments": [
            {
                "name": "Oil to Chemicals",
                "revenue": 662401,
                "ebitda": 60546,
                "how_money_is_made": "Refining and sale of transport fuels, polymers, polyesters and petrochemicals.",
                "drivers": "Fuel demand, refining and petrochemical spreads, utilisation and product mix.",
                "risks": "Commodity spread compression, energy transition and operating costs.",
                "assumed_growth": {"Bear": -2.0, "Base": 5.0, "Bull": 10.0},
                "margin_shift_pp": {"Bear": -1.5, "Base": 0.0, "Bull": 1.0},
            },
            {
                "name": "Retail",
                "revenue": 371085,
                "ebitda": 27034,
                "how_money_is_made": "Omni-channel grocery, electronics, fashion and consumer retail.",
                "drivers": "Same-store productivity, new stores, mix, supply-chain efficiency and digital commerce.",
                "risks": "Consumer slowdown, pricing competition, expansion costs and working capital.",
                "assumed_growth": {"Bear": 6.0, "Base": 12.0, "Bull": 17.0},
                "margin_shift_pp": {"Bear": -1.0, "Base": 0.0, "Bull": 0.7},
            },
            {
                "name": "Digital Services",
                "revenue": 176164,
                "ebitda": 76560,
                "how_money_is_made": "Connectivity subscriptions and digital infrastructure/services through Jio.",
                "drivers": "Subscriber and ARPU growth, broadband adoption, utilisation and digital monetisation.",
                "risks": "Competitive tariff pressure, spectrum/network capex and regulatory changes.",
                "assumed_growth": {"Bear": 8.0, "Base": 14.0, "Bull": 19.0},
                "margin_shift_pp": {"Bear": -1.5, "Base": 0.0, "Bull": 1.0},
            },
            {
                "name": "Oil and Gas",
                "revenue": 23861,
                "ebitda": 19050,
                "how_money_is_made": "Exploration and production of natural gas and crude oil.",
                "drivers": "Production volumes, field output, realised energy prices and operating cost.",
                "risks": "Reservoir decline, commodity pricing and field/project execution.",
                "assumed_growth": {"Bear": -5.0, "Base": 3.0, "Bull": 9.0},
                "margin_shift_pp": {"Bear": -4.0, "Base": 0.0, "Bull": 1.0},
            },
            {
                "name": "Media and Entertainment",
                "revenue": 40682,
                "ebitda": 5842,
                "how_money_is_made": "Advertising, content, streaming and media distribution.",
                "drivers": "Advertising yield, subscription mix, audience engagement and content monetisation.",
                "risks": "High content/rights cost, digital competition and volatile advertising.",
                "assumed_growth": {"Bear": -5.0, "Base": 10.0, "Bull": 20.0},
                "margin_shift_pp": {"Bear": -2.0, "Base": 0.0, "Bull": 1.5},
            },
        ],
        "special_note": (
            "Reported FY2026 segment numbers are from the company annual report. "
            "They are NOT additive to consolidated results. "
            "New Energy has no separately verified FY2026 segment revenue in this model; "
            "no new-energy projection is shown. The Media growth baseline includes "
            "structural changes; projected growth is NOT mechanically extrapolated."
        ),
        "assumptions_note": (
            "Bear/Base/Bull growth and margin changes are analyst-chosen "
            "illustrations, NOT company guidance, management estimates, consensus "
            "forecasts, or investment price targets. They are constant annually "
            "for three years and exclude financing, tax, capex, corporate allocation "
            "and segment consolidation adjustments."
        ),
    },
}


def _finite(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def validate_profile(profile: dict) -> None:
    required = ("name", "fiscal_year", "asof", "unit", "source", "business_summary", "segments")
    if any(not profile.get(k) for k in required) or profile["unit"] != "INR crore":
        raise ValueError("Unverified business profile metadata")
    if not str(profile["source"]).startswith("https://www.ril.com/"):
        raise ValueError("Business model requires an official annual report source")
    segments = profile["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= 15:
        raise ValueError("Missing or excessively large segment table")
    names = set()
    for s in segments:
        if not all(s.get(k) for k in ("name", "how_money_is_made", "drivers", "risks")):
            raise ValueError("Incomplete segment business economics")
        if s["name"] in names:
            raise ValueError("Repeated segment")
        names.add(s["name"])
        for k in ("revenue", "ebitda"):
            if not _finite(s.get(k)) or s[k] < 0:
                raise ValueError("Invalid filing segment financials")
        if s["ebitda"] > s["revenue"] or s["revenue"] <= 0:
            raise ValueError("Unusable margin baseline")
        for key, low, high in (("assumed_growth", -50, 60), ("margin_shift_pp", -20, 20)):
            val = s.get(key) or {}
            if set(val) != {"Bear", "Base", "Bull"}:
                raise ValueError("Each scenario must have explicit assumptions")
            if any(not _finite(v) or not low <= v <= high for v in val.values()):
                raise ValueError("Invalid or non-finite scenario assumptions")
        growth = s["assumed_growth"]
        if not growth["Bear"] <= growth["Base"] <= growth["Bull"]:
            raise ValueError("Scenario growth order inconsistent")


def business_model_for(symbol: str) -> Optional[dict]:
    profile = PROFILES.get(str(symbol).upper().replace(".NS", "").replace(".BO", ""))
    if profile:
        validate_profile(profile)
    return profile


def forecast_segments(profile: dict, years: int = 3) -> dict:
    """Calculate deterministic segment revenue and EBITDA only.

    Do not aggregate to group PAT, EPS, FCF or target price: eliminations, tax,
    debt, minority interest, capex and depreciation are not modelled here.
    """
    validate_profile(profile)
    if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 3:
        raise ValueError("Only a 1–3-year illustration is supported")
    start_year = int(profile["asof"][:4])  # verified FY-end date, not formatted FY label
    output = []
    for segment in profile["segments"]:
        base_margin = segment["ebitda"] / segment["revenue"] * 100
        scenarios = {}
        for name in ("Bear", "Base", "Bull"):
            growth = segment["assumed_growth"][name]
            margin = max(0.0, min(100.0, base_margin + segment["margin_shift_pp"][name]))
            series = []
            for y in range(1, years + 1):
                revenue = segment["revenue"] * ((1 + growth / 100) ** y)
                series.append({"fiscal_year": f"FY{start_year + y}",
                               "revenue_cr": round(revenue),
                               "ebitda_cr": round(revenue * margin / 100),
                               "assumed_growth_pct": growth,
                               "assumed_ebitda_margin_pct": round(margin, 2)})
            scenarios[name] = series
        output.append({"name": segment["name"], "actual_revenue_cr": segment["revenue"],
                       "actual_ebitda_cr": segment["ebitda"], "reported_fy": profile["fiscal_year"],
                       "reported_ebitda_margin_pct": round(base_margin, 2),
                       "how_money_is_made": segment["how_money_is_made"],
                       "drivers": segment["drivers"], "risks": segment["risks"],
                       "scenarios": scenarios})
    return {"company": profile["name"], "source": profile["source"],
            "asof": profile["asof"], "segments": output,
            "special_note": profile["special_note"],
            "assumptions_note": profile["assumptions_note"]}


def explain_gap(symbol: str) -> str:
    return (f"Segment-level business economics and defensible forecast are not yet "
            f"verified from {str(symbol).upper()} official filings. No extrapolated "
            "revenue, EBITDA, EPS or fair-value target is published.")
