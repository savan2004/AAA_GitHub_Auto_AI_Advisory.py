"""Current exchange classification and optional approved Builder V2 comparison."""
from datetime import date
from decimal import Decimal
import os
import re

UNKNOWN = "Unknown / unclassified"
# NSE Indices Industry Classification Structure, July 2023, MES_Code / Sect_Code.
# https://www.niftyindices.com/docs/default-source/default-document-library/industry-classification/nse-indices_industry-classification-structure-2023-07.pdf
# Match official roster labels to sector names, never symbols to sectors.
GROUPS = {
    "Commodities": ("Chemicals", "Construction Materials", "Metals & Mining", "Forest Materials"),
    "Consumer Discretionary": ("Automobile and Auto Components", "Consumer Durables", "Textiles",
                               "Media Entertainment & Publication", "Realty", "Consumer Services"),
    "Energy": ("Oil Gas & Consumable Fuels",),
    "Fast Moving Consumer Goods": ("Fast Moving Consumer Goods",),
    "Financial Services": ("Financial Services",), "Healthcare": ("Healthcare",),
    "Industrials": ("Construction", "Capital Goods"),
    "Information Technology": ("Information Technology",), "Services": ("Services",),
    "Telecommunication": ("Telecommunication",), "Utilities": ("Power", "Utilities"),
    "Diversified": ("Diversified",),
}
MACRO = {label: group for group, labels in GROUPS.items() for label in labels}


def parse_preferences(text):
    match = re.fullmatch(r"model\s+(low|medium|high)\s+(6|18|36|60)", (text or "").strip(), re.I)
    if not match:
        raise ValueError("Use Model low/medium/high and 6/18/36/60 months.")
    return match[1].lower(), int(match[2])


def allocation_review(rows, peers, preferences=None):
    """Rows have passed the report's Decimal close, quantity and session checks."""
    from msk_ai_builder_automatic import approved_plan, automatic_enabled
    from msk_ai_builder_profile import ResearchProfile
    from portfolio_doctor_live import PortfolioDoctorError, expected_doctor_session

    labels = peers.get("held_industries", {}) if peers.get("roster_verified") else {}
    sectors, stocks = {}, {}
    total = sum(r["value"] for r in rows)
    for row in rows:
        symbol = row["symbol"]
        exchange = row.get("exchange", "NSE")
        sector = MACRO.get(labels.get(symbol), UNKNOWN) if exchange == "NSE" else UNKNOWN
        sectors[sector] = sectors.get(sector, 0) + row["value"]
        stocks[exchange, symbol] = (row["qty"], row["value"], row["close"])
    sectors.setdefault(UNKNOWN, Decimal(0))
    out = dict(total=total, sectors=sectors, stocks=stocks, proposed={}, targets={},
               cash=None, status="Unavailable: select a risk preference and horizon.")
    if not preferences:
        return out
    out["status"] = "Unavailable: no approved research release for this profile and session."
    if not automatic_enabled():
        return out
    session = date.fromisoformat(rows[0]["date"])
    try:
        if session != expected_doctor_session():
            return out
        # Builder accepts integer rupees. Preserve the fractional balance in cash.
        profile = ResearchProfile(int(total), *preferences)
        plan, _ = approved_plan(profile, os.environ.get("MSK_BUILDER_V2_APPROVED_RESEARCH"),
                                latest_session=session)
    except (ValueError, PortfolioDoctorError):
        return out
    proposed, targets = {}, {}
    for p in plan.positions:
        key = ("NSE", p.candidate.symbol)
        if key in stocks and stocks[key][2] != p.candidate.close_rupees:
            out["status"] = "Unavailable: approved and portfolio closes differ."
            return out
        proposed[key] = (p.shares, p.cost, p.candidate.close_rupees)
        sector = MACRO.get(p.candidate.industry.replace(",", ""), UNKNOWN)
        targets[sector] = targets.get(sector, 0) + p.cost
    cash = plan.cash + total - Decimal(profile.capital_rupees)
    if cash < 0 or sum(targets.values()) + cash != total:
        return out
    out.update(proposed=proposed, targets=targets, cash=cash,
               status=f"Illustrative approved model | {profile.risk} risk | {profile.months} months")
    return out
