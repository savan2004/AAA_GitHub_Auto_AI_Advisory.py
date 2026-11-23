"""Content contract for a complete MSK monthly editorial draft.

Structural validation only: links and text are NOT a substitute for checking
the original financial-source bytes, calculations or human editorial approval.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit


class EditorialPackError(ValueError):
    """The magazine's authored page material is missing or unsafe."""


ALLOWED_SOURCE_DOMAINS = {
    "rbi.org.in", "pib.gov.in", "mospi.gov.in", "nseindia.com", "nseindia.in",
    "niftyindices.com", "tcs.com", "persistent.com", "coforge.com",
    "sebi.gov.in", "bseindia.com", "mca.gov.in",
    "indiabudget.gov.in", "federalreserve.gov", "bea.gov", "bls.gov",
}


def _source_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 500:
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username
                and not parsed.password and parsed.port is None
                and any(host == domain or host.endswith("." + domain)
                        for domain in ALLOWED_SOURCE_DOMAINS))
    except ValueError:
        return False


def _nonempty_text(value: object, *, min_len: int, max_len: int, label: str) -> str:
    if not isinstance(value, str) or not min_len <= len(value.strip()) <= max_len:
        raise EditorialPackError(f"{label} must contain {min_len}-{max_len} characters")
    if any(ord(c) < 32 and c not in "\\n\\t" for c in value):
        raise EditorialPackError(f"{label} contains control characters")
    return value.strip()


def load_editorial_pack(path: Path, issue: str, *, as_of: date) -> dict[int, dict]:
    """Validate all 14 authored research pages (03-16) or reject the pack.

    Published material still requires separate original-source verification,
    numerical reconciliation, visual checking and owner approval.
    """
    pack = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(pack, dict) or pack.get("issue") != issue:
        raise EditorialPackError("Editorial issue does not match requested PDF issue")
    try:
        cutoff = date.fromisoformat(pack["as_of"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EditorialPackError("Editorial pack must declare an ISO as_of date") from exc
    if cutoff > as_of or cutoff.year < as_of.year - 1:
        raise EditorialPackError("Editorial source cutoff outside accepted date window")
    pages = pack.get("pages")
    if not isinstance(pages, list) or len(pages) != 14:
        raise EditorialPackError("Require exactly 14 unique authored research pages: 03-16")
    result = {}
    used_copy = set()
    for page in pages:
        if not isinstance(page, dict):
            raise EditorialPackError("Editorial page must be an object")
        n = page.get("page")
        if type(n) is not int or n not in range(3, 17) or n in result:
            raise EditorialPackError("Invalid or duplicate editorial page number")
        headline = _nonempty_text(page.get("headline"), min_len=12, max_len=62,
                                  label=f"Page {n} headline")
        lead = _nonempty_text(page.get("lead"), min_len=45, max_len=240,
                              label=f"Page {n} lead")
        points = page.get("analysis")
        if not isinstance(points, list) or len(points) != 2:
            raise EditorialPackError(f"Page {n} must include exactly two analysis points")
        analysis = [_nonempty_text(s, min_len=45, max_len=195,
                                   label=f"Page {n} analysis") for s in points]
        risk = _nonempty_text(page.get("risk_note"), min_len=28, max_len=180,
                              label=f"Page {n} risk note")
        sources = page.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
            raise EditorialPackError(f"Page {n} needs 1-3 dated primary sources")
        valid_sources = []
        for src in sources:
            if not isinstance(src, dict) or not _source_url(src.get("url")):
                raise EditorialPackError(f"Page {n} source URL is not an approved HTTPS domain")
            title = _nonempty_text(src.get("title"), min_len=6, max_len=90,
                                   label=f"Page {n} source title")
            try:
                published = date.fromisoformat(src["published_on"])
            except (TypeError, ValueError, KeyError) as exc:
                raise EditorialPackError(f"Page {n} source date is invalid") from exc
            if published > cutoff or published.year < cutoff.year - 2:
                raise EditorialPackError(f"Page {n} source is future-dated or too old")
            valid_sources.append({"title": title, "url": src["url"],
                                  "published_on": published.isoformat()})
        # Repeated research prose across different pages isn't a magazine.
        copy_key = (lead.casefold(), tuple(x.casefold() for x in analysis))
        if copy_key in used_copy:
            raise EditorialPackError(f"Page {n} repeats another page's research")
        used_copy.add(copy_key)
        result[n] = {"page": n, "headline": headline, "lead": lead,
                     "analysis": analysis, "risk_note": risk,
                     "sources": valid_sources}
    if set(result) != set(range(3, 17)):
        raise EditorialPackError("Missing authored research pages")
    return result
