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
    },
}


def _period(label):
    """Parse only explicit completed Indian financial-quarter end labels."""
    if not isinstance(label, str):
        return None
    m = re.fullmatch(r"(Mar|Jun|Sep|Dec)\\s+(20\\d{2})", label.strip(), flags=re.I)
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
