"""NSE issuer-filing quarter cross-checks: narrow, dated, fail-closed evidence.

Only field-level reconciliations are claimed. Historical filing values were
manually compared to the official NSE iXBRL on 2026-11-23; this is NOT a live
filing-discovery service, audited annual statements or regulatory certification.

To extend coverage, register the issuer's primary source period and test the
new values against the exchange's public filing. Never interpolate a quarter.
"""
from __future__ import annotations

from datetime import date
import math
import re


# Official primary source: NSE iXBRL (not an analyst estimate).
# Financial statement amounts are originally in ₹ lakh and are represented
# below in ₹ crore (divide exchange values by 100).
# DMART FY27 Q2: INR lakh rev 19,64,401; PAT 74,298; basic EPS 11.40.
OFFICIAL_QUARTERS = {
    "DMART": {
        "issuer": "Avenue Supermarts Limited",
        "isin": "INE192R01011",
        "period_end": "2026-09-30",
        "filed": "2026-10-10",
        "scope": "consolidated",
        "assurance": "unaudited / limited review",
        "reviewed_date": "2026-11-23",
        "source_url": (
            "https://nsearchives.nseindia.com/corporate/ixbrl/"
            "INTEGRATED_FILING_INDAS_200410_10102026170830_iXBRL_WEB.html"
        ),
        "metrics_in_crore": {
            "revenue": 19644.01,
            "net_profit": 742.98,
        },
        "eps_rupees": 11.40,
        # Explicit exchange debt/equity ratio is a DIFFERENT field and
        # must not silently replace vendor debt/equity without definition.
        "exchange_debt_equity": 0.16,
        # Same NSE consolidated unaudited iXBRL (six months to Sep 2026):
        # CFO 55,257 lakh = ₹552.57 Cr; consolidated PAT 1,60,342 lakh.
        # This H1 period ratio is NOT the same calculation as 3Y conversion.
        "h1_operating_cash_flow_crore": 552.57,
        "h1_profit_crore": 1603.42,
        # DMART's investor presentation published in October 2026:
        "operating_kpis": {
            "period_end": "2026-09-30",
            "stores": 518,
            "stores_added_h1": 18,
            "like_for_like_q2_pct": 9.5,
            "like_for_like_q1_pct": 5.5,
            "retail_area_million_sqft": 21.4,
            "total_bills_crore_q2": 11.1,
            "source_url": (
                "https://api.dmartindia.com/corporate/content/file/v1/2/"
                "x3O3SiYbxPWrdpch4CFoWmvF1791627002/"
                "Investor%20Presentation%20for%20the%20year%20ended%20"
                "September%2030%2C%202026.pdf"
            ),
        },
    },
}


# Authenticated issuer filing submitted 2 May 2026 with audited consolidated
# FY26 result; see page 'Audited consolidated financial results' (not the
# standalone statements earlier in the same PDF).
OFFICIAL_ANNUAL = {
    "DMART": {
        "period_end": "2026-03-31",
        "source_date": "2026-05-02",
        "scope": "consolidated",
        "assurance": "audited",
        "source_url": (
            "https://api.dmartindia.com/corporate/content/file/v1/2/"
            "jZtJ4gV9JIDY9CjsdXXbQxhE1777722483/"
            "Outcome%20of%20the%20Board%20Meeting%20held%20on%20May%202%2C%202026.pdf"
        ),
        "revenue": 68820.74,
        "net_profit": 2969.86,
        "eps": 45.65,
        "de": 0.10,
    }
}

# Basic per-quarter reported consolidated EPS. Each number is tied to the
# 2 May 2026 audited FY26 filing and 10 Oct 2026 reviewed FY27 H1 filing.
# Adding quarterly EPS is an approximation to TTM basic EPS when the weighted-
# average share count changes; this must be visibly labelled as a SUM.
DMART_QUARTERLY_BASIC_EPS = {
    "2025-12-31": 13.15,
    "2026-03-31": 10.09,
    "2026-06-30": 13.20,
    "2026-09-30": 11.40,
}


def annual_exchange_comparison(symbol, statements):
    """Check FY26 audited annual revenue/PAT, report any EPS discrepancy.

    Core FY26 revenue and PAT must both agree within half a crore, tolerating
    rounding by secondary providers; audited EPS wins only as a separately
    sourced display field, not silently rewritten in raw source input.
    """
    key = str(symbol or "").upper().replace(".NS", "")
    official = OFFICIAL_ANNUAL.get(key)
    if official is None:
        return {"status": "not_registered"}
    base = dict(official)
    years = (statements or {}).get("years") or []
    same = [row for row in years
            if isinstance(row, dict) and _period(row.get("label")) == official["period_end"]]
    if len(same) != 1:
        return {**base, "status": "period_not_comparable"}
    row = same[0]
    for field in ("revenue", "net_profit"):
        try:
            observed = float(row[field])
        except (KeyError, ValueError, TypeError):
            return {**base, "status": "missing_core", "missing": field}
        if not math.isfinite(observed) or abs(observed - official[field]) > 0.55:
            return {**base, "status": "mismatch", "field": field,
                    "observed": observed}
    try:
        vendor_eps = float(row["eps"])
    except (KeyError, ValueError, TypeError):
        vendor_eps = None
    if vendor_eps is not None and not math.isfinite(vendor_eps):
        vendor_eps = None
    return {
        **base, "status": "core_match",
        "secondary_eps": vendor_eps,
        "eps_difference": (
            round(vendor_eps - official["eps"], 2) if vendor_eps is not None else None
        ),
    }


def official_four_quarter_eps(symbol, latest_quarter_end):
    """Return documented basic EPS sum; NOT audited full-year TTM EPS."""
    if (str(symbol or "").upper().replace(".NS", "") != "DMART" or
            latest_quarter_end != "2026-09-30"):
        return None
    return round(sum(DMART_QUARTERLY_BASIC_EPS.values()), 2)


def _period(label):
    """Parse only explicit completed Indian financial-quarter end labels."""
    if not isinstance(label, str):
        return None
    m = re.fullmatch(r"(Mar|Jun|Sep|Dec)\s+(20\d{2})", label.strip(), flags=re.I)
    if not m:
        return None
    mm = {"mar": 3, "jun": 6, "sep": 9, "dec": 12}[m.group(1).lower()]
    yyyy = int(m.group(2))
    days = {3: 31, 6: 30, 9: 30, 12: 31}
    return date(yyyy, mm, days[mm]).isoformat()


def reconcile_official_quarter(symbol, statements):
    """Compare the latest same-period secondary quarter with an NSE snapshot.

    A match requires exact reporting period and at least two financial metrics.
    This does not validate historical ratios, company guidance, the market
    quote, live retrieval, or the underlying independent review itself.
    """
    key = str(symbol or "").upper().replace(".NS", "")
    official = OFFICIAL_QUARTERS.get(key)
    if not official:
        return {"status": "not_registered", "matched": [], "mismatched": []}

    base = {
        "issuer": official["issuer"],
        "period_end": official["period_end"],
        "filed": official["filed"],
        "scope": official["scope"],
        "assurance": official["assurance"],
        "reviewed_date": official["reviewed_date"],
        "source_url": official["source_url"],
        "exchange_debt_equity": official["exchange_debt_equity"],
        "h1_operating_cash_flow_crore": official["h1_operating_cash_flow_crore"],
        "h1_profit_crore": official["h1_profit_crore"],
        "operating_kpis": official["operating_kpis"],
    }
    qs = (statements or {}).get("quarters") or []
    if not qs:
        return {**base, "status": "secondary_unavailable", "matched": [], "mismatched": []}
    latest = qs[-1]
    latest_day = _period(latest.get("label")) if isinstance(latest, dict) else None
    if latest_day != official["period_end"]:
        return {**base, "status": "period_not_comparable", "matched": [], "mismatched": [],
                "secondary_period_end": latest_day}

    matches, mismatches, unknown = [], [], []
    fields = {
        "revenue": (official["metrics_in_crore"]["revenue"], 0.55, "₹ Cr"),
        "net_profit": (official["metrics_in_crore"]["net_profit"], 0.55, "₹ Cr"),
        "eps": (official["eps_rupees"], 0.02, "₹ per share"),
    }
    for name, (reference, tolerance, unit) in fields.items():
        raw = latest.get(name)
        try:
            observed = float(raw)
        except (ValueError, TypeError):
            unknown.append(name)
            continue
        if not math.isfinite(observed):
            unknown.append(name)
            continue
        row = {"field": name, "reported": round(observed, 2),
               "official": reference, "unit": unit}
        if abs(observed - reference) <= tolerance:
            matches.append(row)
        else:
            mismatches.append(row)
    # Fail closed on even one observed contradiction; absent data remains
    # explicitly missing rather than converting a partial match into 100%.
    status = ("mismatch" if mismatches else
              "partial_match" if len(matches) >= 2 else "insufficient_to_compare")
    return {**base, "status": status, "matched": matches,
            "mismatched": mismatches, "missing": unknown,
            "secondary_period_end": latest_day}
