"""Source-bound monthly cover-story headlines; never copy festival titles forward.

Editorial picks the topic using the per-issue cover_feature in the dated,
checked financial metrics pack. This module turns a validated event into an
engaging cover line without claiming records, price targets, gold moves or
other facts that the input did not verify.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit


class CoverStoryError(ValueError):
    pass


_ISSUE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_SOURCE_URL = "https://www.niftyindices.com/Index_Dashboard/Index_Dashboard_OCT2026.pdf"


def _number(raw) -> Decimal:
    if isinstance(raw, bool):
        raise CoverStoryError("Boolean not valid as a return")
    try:
        val = Decimal(str(raw))
    except (ValueError, InvalidOperation) as exc:
        raise CoverStoryError("Invalid cover-story return") from exc
    if not val.is_finite() or abs(val) > 200:
        raise CoverStoryError("Non-finite/out-of-range cover-story return")
    return val


def _month_label(year_month: str) -> str:
    y, m = year_month.split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def select_cover_story(issue: str, metrics: dict | None) -> dict | None:
    """Return a verifiable per-issue lead story, or None (generic draft).

    A gold decline or record high must have an independent, date-scoped
    primary-source verifier; never assert either from these index returns.
    """
    if not _ISSUE.fullmatch(issue):
        raise CoverStoryError("Issue must be YYYY-MM")
    if not metrics:
        return None
    if metrics.get("issue") != issue:
        raise CoverStoryError("Cover data from wrong month")
    feature = metrics.get("cover_feature")
    if feature is None:
        return None
    if not isinstance(feature, dict):
        raise CoverStoryError("Cover feature must be a typed source-backed event")
    source = metrics.get("market_source", {})
    url = source.get("url")
    if url != _SOURCE_URL or urlsplit(url).hostname != "www.niftyindices.com":
        raise CoverStoryError("Unsupported cover-story evidence source")
    if source.get("report_date") != "2026-10-31":
        raise CoverStoryError("Unsupported/unverified dashboard observation date")
    observation = feature.get("observation_month")
    if observation != "2026-10" or observation > issue:
        raise CoverStoryError("Cover observation must be a verified prior month")
    if feature.get("special_edition", False):
        raise CoverStoryError("Diwali/festival theme requires a separate explicitly approved special issue")
    kind = feature.get("kind")
    if kind == "index_divergence":
        primary, secondary = feature.get("primary"), feature.get("secondary")
        if primary != "Nifty 50" or secondary != "Nifty Smallcap 250":
            raise CoverStoryError("Unverified index pair")
        rows = {r.get("name"): r for r in metrics.get("market_indices", [])}
        if primary not in rows or secondary not in rows:
            raise CoverStoryError("Required index source rows missing")
        a, b = _number(rows[primary]["m1"]), _number(rows[secondary]["m1"])
        if a > 0 and b < 0:
            title = "NIFTY RISES. SMALL CAPS FALL."
            kicker = "THE MARKET DIVIDE"
            deck = (f"{_month_label(observation)}: Nifty 50 +{a:.2f}% "
                    f"while Nifty Smallcap 250 {b:.2f}%")
        elif a < 0 and b > 0:
            title = "SMALL CAPS DEFY THE NIFTY"
            kicker = "THE MARKET DIVIDE"
            deck = (f"{_month_label(observation)}: Nifty 50 {a:.2f}% "
                    f"while Smallcap 250 +{b:.2f}%")
        else:
            raise CoverStoryError("Index divergence narrative not supported by actual return signs")
        delta = abs(a - b)
        if delta < Decimal("2.00"):
            raise CoverStoryError("Insufficient evidence of noteworthy index divergence")
        evidence = [
            {"index": primary, "field": "m1", "value": float(a)},
            {"index": secondary, "field": "m1", "value": float(b)},
        ]
    elif kind == "sector_movement":
        sector_name = feature.get("sector")
        rows = {r.get("name"): r for r in metrics.get("sectors", [])}
        if sector_name not in rows or sector_name not in {
            "Nifty IT", "Nifty Pharma", "Nifty PSU Bank", "Nifty Healthcare Index",
            "Nifty Energy", "Nifty Auto", "Nifty Metal", "Nifty Capital Goods"
        }:
            raise CoverStoryError("Unverified sector index")
        change = _number(rows[sector_name]["m1"])
        if abs(change) < Decimal("5"):
            raise CoverStoryError("Sector movement below cover-story materiality threshold")
        label = sector_name.replace("Nifty ", "").upper()
        title = f"{label} LEADS THE RALLY" if change > 0 else f"{label} UNDER PRESSURE"
        kicker = "THE SECTOR STORY"
        deck = (f"{_month_label(observation)}: {sector_name} total return "
                f"{change:+.2f}% (NSE Indices)")
        evidence = [{"index": sector_name, "field": "m1", "value": float(change)}]
    else:
        # "Bank Nifty hits an all-time high" requires independently verified
        # historical levels; "Gold plunges" requires official gold prices.
        # These types are deliberately not faked from a return dashboard.
        raise CoverStoryError("Cover theme has no verified data contract")
    if len(title) > 62 or len(deck) > 150:
        raise CoverStoryError("Cover feature exceeds safe print lengths")
    return {
        "issue": issue, "kind": kind, "headline": title, "kicker": kicker,
        "deck": deck, "observation_month": observation,
        "source_url": url, "source_date": source["report_date"],
        "evidence": evidence, "status": "SOURCE_MATCHED_COVER_DRAFT_NOT_PUBLICATION_APPROVED",
    }
