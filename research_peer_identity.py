"""Dated NSE basic-industry peer identity snapshots (not market metrics).

This small pilot remedies the broad "Retail & Lifestyle" group, which mixes
jewellers, cinema and restaurants with DMART's diversified retail business.
It is intentionally a candidate screen, not proof of peer financial accuracy.
All current P/E, ROE and returns still need separate dated provider evidence.
"""
from datetime import date

# Sources: company NSE equity quote page, Basic Industry classification.
# Snapshot must expire; a stale industry assertion must not quietly persist.
NSE_BASIC_INDUSTRY = {
    "DMART": {
        "industry": "Diversified Retail",
        "asof": "2026-11-23",
        "issuer_url": "https://www.nseindia.in/get-quote/equity/DMART/Avenue-Supermarts",
        "candidates": (
            ("VMM", "Vishal Mega Mart Limited",
             "https://www.nseindia.in/get-quote/equity/VMM/Vishal-Mega-Mart-Ltd"),
            ("EMIL", "Electronics Mart India Limited",
             "https://www.nseindia.in/get-quote/equity/EMIL/Emil-Ns"),
            ("SHOPERSTOP", "Shoppers Stop Limited",
             "https://www.nseindia.in/get-quote/equity/SHOPERSTOP/Shoppers-Stop-Limited"),
        ),
    },
}


def verified_industry_candidates(symbol, today=None, max_age_days=45):
    """Return source-dated identities; never return them after expiry."""
    symbol = str(symbol or "").upper().replace(".NS", "")
    item = NSE_BASIC_INDUSTRY.get(symbol)
    if item is None:
        return None  # Unregistered: let caller use its existing policy.
    today = today or date.today()
    observed = date.fromisoformat(item["asof"])
    age = (today - observed).days
    return {
        "industry": item["industry"],
        "observed_date": item["asof"],
        "source_url": item["issuer_url"],
        "candidates": item["candidates"] if 0 <= age <= max_age_days else (),
        "expired": age < 0 or age > max_age_days,
    }
