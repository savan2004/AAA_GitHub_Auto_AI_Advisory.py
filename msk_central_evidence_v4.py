"""Isolated evidence retrieval and recovery contract; no live wiring.

All adapters and verification functions are injected by trusted application
code, never selected or created by model output. No database or paid AI calls.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable
from urllib.parse import urlsplit

from msk_central_data_v4 import Dataset, normalize_symbol

MAX_PROVIDERS = 4
MAX_CANDIDATES = 12
MAX_LEADS = 3
OFFICIAL_HOSTS = frozenset((
    "nsearchives.nseindia.com", "archives.nseindia.com", "www.nseindia.com",
    "www.bseindia.com", "api.bseindia.com", "www.bseindia.com",
))
SCOPES = frozenset(("raw", "adjusted", "consolidated", "standalone", "not_applicable"))
UNITS = frozenset(("INR", "INR_CRORE", "INR_LAKH", "PERCENT", "RATIO", "SHARES", "COUNT"))


@dataclass(frozen=True)
class EvidenceKey:
    symbol: str
    dataset: Dataset
    field: str
    period_end: date
    scope: str
    unit: str
    # Period start distinguishes quarter, YTD, annual and daily observations.
    period_start: date
    isin: str

    def __post_init__(self):
        if normalize_symbol(self.symbol) != self.symbol or not isinstance(self.dataset, Dataset):
            raise ValueError("invalid identity")
        if not isinstance(self.field, str) or not self.field or len(self.field) > 64:
            raise ValueError("invalid field")
        if type(self.period_start) is not date or type(self.period_end) is not date or self.period_start > self.period_end:
            raise ValueError("invalid period")
        if self.scope not in SCOPES or self.unit not in UNITS:
            raise ValueError("invalid scope or unit")
        if not isinstance(self.isin, str) or len(self.isin) != 12 or not self.isin.isascii() or not self.isin.isalnum() or self.isin != self.isin.upper():
            raise ValueError("invalid ISIN syntax; membership verification required separately")


@dataclass(frozen=True)
class Receipt:
    key: EvidenceKey
    value: Decimal
    source_url: str
    document_sha256: str
    published: date
    locator: str


@dataclass(frozen=True)
class Resolution:
    status: str
    receipts: tuple[Receipt, ...] = ()
    attempts: int = 0

    @property
    def value(self):
        return self.receipts[0].value if self.status == "verified" else None


def official_url(url):
    """Exact HTTPS host allowlist; redirects must also be checked by adapter."""
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        return False
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname in OFFICIAL_HOSTS
                and p.netloc == p.hostname and bool(p.path.strip("/"))
                and not p.fragment and not p.query)
    except ValueError:
        return False


def valid_receipt(candidate, key, as_of):
    if not isinstance(candidate, Receipt) or candidate.key != key:
        return False
    if type(candidate.published) is not date or not key.period_end <= candidate.published <= as_of:
        return False
    if not official_url(candidate.source_url):
        return False
    h = candidate.document_sha256
    if not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        return False
    if not isinstance(candidate.locator, str) or not candidate.locator.strip() or len(candidate.locator) > 512:
        return False
    return isinstance(candidate.value, Decimal) and candidate.value.is_finite()


def parse_ai_leads(output):
    """Strict structured output: only source URLs, never numeric observations."""
    if not isinstance(output, dict) or set(output) != {"source_urls"}:
        return ()
    urls = output["source_urls"]
    if not isinstance(urls, list) or len(urls) > MAX_LEADS or not all(official_url(u) for u in urls):
        return ()
    return tuple(dict.fromkeys(urls))


def resolve(key: EvidenceKey, *, as_of: date, retrieve: Callable,
            providers: Iterable[Callable], verify: Callable,
            discover=None, fetch_lead=None) -> Resolution:
    """Exact-key retrieval, bounded recovery, independent source verification.

    retrieve/providers return iterables of candidate Receipt objects, not trusted
    records. verify MUST independently validate original document bytes, issuer,
    field, unit, publication time and locator; only literal True is accepted.
    An AI lead is optionally fetched by a trusted adapter, then checked by the
    same verifier. No AI call if deterministic evidence exists or conflicts.
    This contract is not a persistent RAG store or a live provider adapter.
    """
    from itertools import islice
    if type(as_of) is not date or key.period_end > as_of:
        return Resolution("withheld_future_period")
    checked = []
    attempts = 0
    exhausted = False

    def ingest(loader):
        nonlocal attempts, exhausted
        attempts += 1
        try:
            candidates = tuple(islice(iter(loader()), MAX_CANDIDATES + 1))
            if len(candidates) > MAX_CANDIDATES:
                exhausted = True
                return
            for c in candidates:
                if valid_receipt(c, key, as_of) and verify(c, key) is True and c not in checked:
                    checked.append(c)
        except Exception:
            # Provider failures are isolated; never substitute an estimate.
            return

    def result():
        if exhausted:
            return Resolution("withheld_candidate_limit", tuple(checked), attempts)
        if len({r.value for r in checked}) > 1:
            return Resolution("withheld_conflict", tuple(checked), attempts)
        if checked:
            return Resolution("verified", tuple(checked), attempts)
        return None

    ingest(lambda: retrieve(key))
    # Check the configured deterministic sources even on a cache hit, so an
    # amended filing or a conflicting receipt is not silently ignored.
    for provider in islice(providers, MAX_PROVIDERS):
        ingest(lambda: provider(key))
    existing = result()
    if existing is not None:
        return existing
    if discover is not None and fetch_lead is not None:
        try:
            leads = parse_ai_leads(discover(key))
        except Exception:
            leads = ()
        for url in leads:
            ingest(lambda: fetch_lead(key, url))
    return result() or Resolution("withheld_missing", (), attempts)
