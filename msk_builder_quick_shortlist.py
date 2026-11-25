"""Fast three-bucket MSK research shortlist, never a customer release.

An AI may propose stock symbols; it does not certify fundamentals, market-cap
category, suitability or prices. Official NSE index membership and an
independently reviewed research record are the gates, not an AI rating.

Stages 5 each from Nifty 50, Nifty Midcap 150 and Nifty Smallcap 250.
Customer publication is deliberately handled only by the existing
msk_ai_builder_research_release audited gate.
"""
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import re

BUCKETS = {
    "large": ("ind_nifty50list.csv", 50),
    "mid": ("ind_niftymidcap150list.csv", 150),
    "small": ("ind_niftysmallcap250list.csv", 250),
}
_ROOT = "https://nsearchives.nseindia.com/content/indices/"
_SYMBOL = re.compile(r"[A-Z0-9&-]{2,20}\Z")
_ISIN = re.compile(r"IN[A-Z0-9]{10}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_BYTES = 160_000


class ShortlistUnavailable(ValueError):
    """Missing/stale/ambiguous source evidence, no stock guessed."""


@dataclass(frozen=True)
class IndexMembership:
    tier: str
    members: dict
    fetched_on: date
    source_url: str
    sha256: str


def parse_membership(raw, tier, fetched_on):
    """Validate an actual, complete NSE index CSV; no hand-written stock list."""
    if tier not in BUCKETS or type(fetched_on) is not date:
        raise ShortlistUnavailable("INDEX_INPUT_INVALID")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ShortlistUnavailable("INDEX_DOCUMENT_INVALID")
    try:
        decoded = raw.decode("utf-8-sig")
        reader = csv.DictReader(StringIO(decoded))
        required = {"Symbol", "ISIN Code"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ShortlistUnavailable("INDEX_SCHEMA_INVALID")
        rows = list(reader)
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ShortlistUnavailable("INDEX_SCHEMA_INVALID") from None
    if len(rows) != BUCKETS[tier][1]:
        raise ShortlistUnavailable("INDEX_COVERAGE_INVALID")
    members = {}
    identities = set()
    for row in rows:
        symbol, isin = row.get("Symbol"), row.get("ISIN Code")
        if (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                or not isinstance(isin, str) or not _ISIN.fullmatch(isin)
                or symbol in members or isin in identities
                or ("Series" in row and row["Series"] != "EQ")):
            raise ShortlistUnavailable("INDEX_IDENTITY_INVALID")
        members[symbol] = isin
        identities.add(isin)
    return IndexMembership(tier, members, fetched_on,
                           _ROOT + BUCKETS[tier][0], sha256(raw).hexdigest())


def fetch_memberships(*, session_date, fetch=None):
    """Three bounded exchange CSV fetches, no redirects, no silent stale cache."""
    if type(session_date) is not date:
        raise ShortlistUnavailable("INDEX_DATE_INVALID")
    if fetch is None:
        import requests
        def fetch(url):
            try:
                with requests.get(
                    url, timeout=(4, 9), allow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"},
                    stream=True,
                ) as response:
                    if response.status_code != 200 or response.url != url:
                        raise ShortlistUnavailable("INDEX_HTTP_UNAVAILABLE")
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        if len(data) + len(chunk) > MAX_BYTES:
                            raise ShortlistUnavailable("INDEX_DOCUMENT_INVALID")
                        data.extend(chunk)
                    return bytes(data)
            except requests.RequestException:
                raise ShortlistUnavailable("INDEX_HTTP_UNAVAILABLE") from None
    results = {}
    for tier, (filename, _) in BUCKETS.items():
        raw = fetch(_ROOT + filename)
        results[tier] = parse_membership(raw, tier, session_date)
    identities = [v for snap in results.values() for v in snap.members.values()]
    if len(set(identities)) != len(identities):
        raise ShortlistUnavailable("INDEX_TIER_OVERLAP")
    return results


def _qualified(symbol, isin, record, close_date):
    """Require existing independently reviewed-company evidence for SHORTLIST,
    not automatic publication. A string label alone is not proof of authenticity.
    """
    if not isinstance(record, dict):
        return False
    try:
        if (record["symbol"] != symbol or record["isin"] != isin
                or record["close_date"] != close_date.isoformat()
                or record["evidence_status"] != "ISSUER_AUTHENTICATED"
                or record["price_status"] != "EXCHANGE_VERIFIED"
                or record["valuation_status"] != "VERIFIED"
                or record["risk_status"] != "VERIFIED"
                or record["corporate_actions_status"] != "VERIFIED"
                or record["issuer_identity_verified"] is not True
                or type(record["verified_annual_periods"]) is not int
                or record["verified_annual_periods"] < 5
                or type(record["verified_quarter_periods"]) is not int
                or record["verified_quarter_periods"] < 8
                or not _HASH.fullmatch(record["filing_sha256"])):
            return False
        price = Decimal(str(record["close_rupees"]))
        return price.is_finite() and price > 0
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return False


def shortlist_index_ideas(snapshots, suggestions, reviewed, *, close_date, per_tier=5):
    """Choose up to five *evidence-ready* ideas per tier, with automatic skip.

    AI candidates are only proposals. This cannot publish research or
    fabricate a replacement for an issuer with unavailable financials.
    """
    if (type(close_date) is not date or type(per_tier) is not int
            or not 1 <= per_tier <= 5 or set(snapshots) != set(BUCKETS)
            or set(suggestions) != set(BUCKETS)
            or not isinstance(reviewed, dict)):
        raise ShortlistUnavailable("SHORTLIST_INPUT_INVALID")
    if any(not isinstance(s, IndexMembership) or s.tier != k
           or s.fetched_on != close_date or s.source_url != _ROOT + BUCKETS[k][0]
           for k, s in snapshots.items()):
        raise ShortlistUnavailable("INDEX_SESSION_INVALID")
    all_members = [i for s in snapshots.values() for i in s.members.values()]
    if len(set(all_members)) != len(all_members):
        raise ShortlistUnavailable("INDEX_TIER_OVERLAP")
    chosen, rejected = {}, {}
    for tier in BUCKETS:
        ideas = suggestions[tier]
        if not isinstance(ideas, (list, tuple)) or len(ideas) > 40:
            raise ShortlistUnavailable("AI_IDEAS_INVALID")
        found, skipped, seen = [], {}, set()
        for idea in ideas:
            if not isinstance(idea, str) or not _SYMBOL.fullmatch(idea):
                raise ShortlistUnavailable("AI_IDEAS_INVALID")
            if idea in seen:
                continue
            seen.add(idea)
            isin = snapshots[tier].members.get(idea)
            if isin is None:
                skipped[idea] = "NOT_IN_OFFICIAL_INDEX"
            elif not _qualified(idea, isin, reviewed.get(idea), close_date):
                skipped[idea] = "FINANCIAL_EVIDENCE_NOT_APPROVED"
            else:
                found.append(idea)
            if len(found) == per_tier:
                break
        chosen[tier] = found
        rejected[tier] = skipped
    return {
        "publication_status": "WITHHELD",
        "status": ("RESEARCH_SHORTLIST_READY_FOR_RELEASE_REVIEW"
                   if all(len(chosen[k]) == per_tier for k in BUCKETS)
                   else "RESEARCH_SHORTLIST_INCOMPLETE"),
        "close_date": close_date.isoformat(),
        "target_per_tier": per_tier,
        "shortlists": chosen,
        "skipped": rejected,
        "next_action": "Review original issuer/source documents and operator approval; never publish this shortlist directly.",
    }
