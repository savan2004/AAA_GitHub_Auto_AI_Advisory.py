"""
peer_groups.py — Curated peer fallback for companies outside the bot's sector map
────────────────────────────────────────────────────────────────────────────────
The old logic only found peers for the ~250 stocks in SECTOR_STOCKS, so lenders
like SBFC showed "Peers: not available". Resolution order:

  1. EXPLICIT       hand-picked group for a specific symbol
  2. KIND + SIZE    lender kind (Bank / HFC / Microfinance / NBFC) and market cap
  3. SECTOR MAP     the bot's existing sector groups (non-lenders)

Symbols are NSE tickers. A symbol that no longer exists simply fails the
fundamentals fetch and is skipped, so a stale entry is harmless.
"""

from typing import List, Optional, Tuple

GROUPS = {
    "Small-cap NBFC / MSME lenders": ["FIVESTAR", "MASFIN", "FUSION", "UGROCAP", "SATIN", "SPANDANA",
                                      "CREDITACC", "AAVAS", "APTUS", "HOMEFIRST", "INDIASHLTR", "AADHARHFC"],
    "Microfinance lenders": ["CREDITACC", "SPANDANA", "FUSION", "SATIN", "MANAPPURAM", "MUTHOOTFIN"],
    "Housing finance companies": ["AAVAS", "APTUS", "HOMEFIRST", "INDIASHLTR", "AADHARHFC", "CANFINHOME",
                                  "REPCOHOME", "LICHSGFIN", "PNBHOUSING"],
    "Large NBFCs": ["BAJFINANCE", "CHOLAFIN", "SHRIRAMFIN", "MUTHOOTFIN", "MANAPPURAM", "LTF", "M&MFIN",
                    "POONAWALLA", "SUNDARMFIN", "ABCAPITAL", "IIFL"],
    "Banks": ["HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "SBIN", "FEDERALBNK", "IDFCFIRSTB",
              "INDUSINDBK", "BANDHANBNK", "CUB", "DCBBANK"],
}

# symbol → group name (hand-picked)
EXPLICIT = {
    "SBFC": "Small-cap NBFC / MSME lenders",
}

SMALL_NBFC_MAX_MCAP = 3.0e11          # ₹30,000 Cr (raw rupees)


def resolve(sym: str, lender_kind: Optional[str], mcap: Optional[float]) -> Tuple[Optional[str], List[str], str]:
    """→ (group label, candidate symbols excluding `sym`, method)."""
    sym = sym.upper()
    if sym in EXPLICIT:
        g = EXPLICIT[sym]
        return g, [s for s in GROUPS[g] if s != sym], "curated peer group"
    if lender_kind:
        if lender_kind == "Bank":
            g = "Banks"
        elif lender_kind == "HFC":
            g = "Housing finance companies"
        elif lender_kind == "Microfinance":
            g = "Microfinance lenders"
        else:
            g = "Small-cap NBFC / MSME lenders" if (mcap or 0) < SMALL_NBFC_MAX_MCAP else "Large NBFCs"
        return g, [s for s in GROUPS[g] if s != sym], f"lender peer group ({lender_kind}, by size)"
    return None, [], "none"
