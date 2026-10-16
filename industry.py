"""
industry.py — Is this company a lender? (NBFC / HFC / MFI / Bank)
────────────────────────────────────────────────────────────────────────────────
Why this exists
  The report's sector-aware rules (no OCF-based earnings-quality alarm, no
  Debt/Equity red flag, balance-sheet metrics instead) depend on knowing the
  company is a lender. The old check used only the bot's 250-stock sector map,
  so any lender outside it (e.g. SBFC Finance) was analysed like a factory.

Signals, strongest first (any one is enough; all matches are recorded):
  1. layout   — Screener reports lenders with "Financing Profit / Financing
                Margin %" rows instead of Operating Profit / OPM.   (data-driven)
  2. yahoo    — Yahoo industry: Credit Services, Banks - *, Mortgage Finance
  3. map      — the bot's sector map says "Banking & Finance"
  4. name     — company name contains finance / bank / housing finance / micro…
                (deliberately NOT bare "Capital" — too many false positives)

Output
  {"is_financial": bool, "is_lender": bool, "kind": "Bank"|"HFC"|"Microfinance"|"NBFC"|None,
   "reasons": [...]}
"""

import re
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_LENDER_INDUSTRIES = ("credit services", "banks - regional", "banks - diversified", "mortgage finance",
                      "banks", "consumer lending", "financial conglomerates")
_NON_LENDER_FIN = ("insurance", "asset management", "capital markets", "financial data", "stock exchanges",
                   "brokerage", "investment banking")

_NAME_LENDER = re.compile(r"\b(bank|financ\w*|fin(serv|corp|vest)\w*|housing\s+fin\w*|micro\s?fin\w*|"
                          r"home\s+fin\w*|credit|leasing|nbfc)\b", re.I)
_NAME_BANK = re.compile(r"\bbank\b", re.I)
_NAME_HFC = re.compile(r"\b(housing|home\s+fin|homefirst|shelter|aavas|aptus|repco|canfin)\b", re.I)
_NAME_MFI = re.compile(r"(micro\s?fin|microfinance|grameen|spandana|satin|fusion|jana|credit\s*access)", re.I)

_yinfo_cache = {}
_lock = threading.Lock()


def fetch_yahoo_industry(sym: str) -> dict:
    """Best-effort {'sector': ..., 'industry': ...} from Yahoo. Never raises."""
    sym = sym.upper().replace(".NS", "")
    with _lock:
        e = _yinfo_cache.get(sym)
    if e and time.time() - e[0] < 24 * 3600:
        return e[1]
    out = {}
    try:
        import yfinance as yf
        info = yf.Ticker(f"{sym}.NS").info or {}
        out = {"sector": info.get("sector"), "industry": info.get("industry")}
    except Exception as ex:
        logger.info(f"[industry] yahoo lookup {sym} failed: {str(ex)[:80]}")
    with _lock:
        _yinfo_cache[sym] = (time.time(), out)
    return out


def classify(name: str = "", sector_label: Optional[str] = None, yahoo: Optional[dict] = None,
             statements: Optional[dict] = None) -> dict:
    reasons = []
    lender = False
    financial = False
    kind = None
    name = name or ""
    ind = ((yahoo or {}).get("industry") or "").lower()
    sec = ((yahoo or {}).get("sector") or "").lower()

    if statements and statements.get("layout") == "lender":
        lender = financial = True
        reasons.append("Screener reports financing profit/margin (lender layout)")
    if ind and any(k in ind for k in _LENDER_INDUSTRIES):
        lender = financial = True
        reasons.append(f"Yahoo industry: {(yahoo or {}).get('industry')}")
    elif sec == "financial services" or (ind and any(k in ind for k in _NON_LENDER_FIN)):
        financial = True
        reasons.append(f"Yahoo sector: {(yahoo or {}).get('sector') or 'Financial Services'}"
                       + (f" / {(yahoo or {}).get('industry')}" if (yahoo or {}).get("industry") else ""))
    if sector_label and "banking" in sector_label.lower():
        lender = financial = True
        reasons.append("bot sector map: Banking & Finance")
    non_lender_named = bool(ind and any(k in ind for k in _NON_LENDER_FIN))
    if _NAME_LENDER.search(name) and not non_lender_named:
        lender = financial = True
        reasons.append("company name indicates a lender")

    if lender:
        if _NAME_BANK.search(name) or "banks" in ind:
            kind = "Bank"
        elif _NAME_MFI.search(name):
            kind = "Microfinance"
        elif _NAME_HFC.search(name) or "mortgage" in ind:
            kind = "HFC"
        else:
            kind = "NBFC"
    return {"is_financial": financial, "is_lender": lender, "kind": kind, "reasons": reasons}
