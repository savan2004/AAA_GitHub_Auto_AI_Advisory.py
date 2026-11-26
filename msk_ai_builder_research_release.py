"""Reviewed-research release fence for MSK Builder (standard-library only).

A JSON row that says APPROVED is not verified issuer research. Before ANY
customer allocation, require a matching SHA-256 release record committed in
the separately reviewed repository registry, plus a matching immutable
issuer-evidence audit bundle. No environment variable or customer input may
choose the registry or override this fence.

This is a traceability / change-control gate, NOT independent authentication
of NSE filings. A separate human or authenticated collector MUST verify each
filing's bytes, issuer identity, currency, accounting basis, periods, closing
prices, dividends/corporate actions and quality before approving a registry
entry. An empty registry MUST deny all publication.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re


class UnreviewedResearch(ValueError):
    """No immutable and independently reviewed source-evidence release."""


_ROOT = Path(__file__).resolve().parent
_REGISTRY = _ROOT / "msk_builder_research_release_registry.json"
_AUDITS = _ROOT / "research_audits"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_AUDIT_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
_MAX_REGISTRY = 16384
_MAX_AUDIT = 131072


def _read_document(path, *, limit):
    # All locations are chosen by the code, never by customer-controlled input.
    raw = path.read_bytes()
    if not raw or len(raw) > limit:
        raise UnreviewedResearch("REVIEW_DOCUMENT_INVALID")
    return json.loads(raw)


def require_reviewed_research(raw, *, latest_session, companies):
    """Prevent operator-supplied labels, hashes and URLs from self-approving.

    The registry is pinned to committed repository content. A source audit is
    a separate, reviewed record and must cover every selected company. This
    does not independently prove that the stated NSE filings are genuine.
    """
    try:
        if not isinstance(raw, str) or type(latest_session) is not date:
            raise ValueError("invalid inputs")
        if not isinstance(companies, list) or not 1 <= len(companies) <= 20:
            raise ValueError("invalid company bank")
        digest = sha256(raw.encode("utf-8")).hexdigest()
        registry = _read_document(_REGISTRY, limit=_MAX_REGISTRY)
        if (not isinstance(registry, dict)
                or set(registry) != {"schema_version", "publication_status", "approved"}
                or type(registry["schema_version"]) is not int
                or registry["schema_version"] != 1
                or registry["publication_status"] != "APPROVED"
                or not isinstance(registry["approved"], list)
                or not 1 <= len(registry["approved"]) <= 8):
            raise ValueError("unreviewed registry")
        matches = []
        for item in registry["approved"]:
            if (not isinstance(item, dict)
                    or set(item) != {"artifact_sha256", "close_date", "audit_sha256", "regulatory_review_id"}
                    or not all(isinstance(item[k], str) for k in item)
                    or not _DIGEST.fullmatch(item["artifact_sha256"])
                    or not _DIGEST.fullmatch(item["audit_sha256"])
                    or not 8 <= len(item["regulatory_review_id"]) <= 128
                    or not re.fullmatch(r"[A-Za-z0-9_:/-]+", item["regulatory_review_id"])
                    or date.fromisoformat(item["close_date"]) > latest_session):
                raise ValueError("invalid registry entry")
            if item["artifact_sha256"] == digest and item["close_date"] == latest_session.isoformat():
                matches.append(item)
        if len(matches) != 1:
            raise ValueError("unsigned or ambiguous release")
        entry = matches[0]
        # Filename is the digest, never a supplied filesystem path; no traversal.
        name = entry["audit_sha256"] + ".json"
        if not _AUDIT_NAME.fullmatch(name):
            raise ValueError("audit path invalid")
        document_bytes = (_AUDITS / name).read_bytes()
        if not 0 < len(document_bytes) <= _MAX_AUDIT:
            raise ValueError("audit exceeds bounds")
        if sha256(document_bytes).hexdigest() != entry["audit_sha256"]:
            raise ValueError("audit bytes mismatch")
        bundle = json.loads(document_bytes)
        if (not isinstance(bundle, dict)
                or set(bundle) != {"schema_version", "close_date", "review_status", "companies"}
                or bundle["schema_version"] != 1
                or bundle["close_date"] != latest_session.isoformat()
                or bundle["review_status"] != "PRIMARY_DOCUMENTS_RECONCILED"
                or not isinstance(bundle["companies"], list)
                or len(bundle["companies"]) != len(companies)):
            raise ValueError("audit incomplete")
        by_symbol = {}
        for row in bundle["companies"]:
            if (not isinstance(row, dict)
                    or set(row) != {"symbol", "isin", "filing_sha256", "close_rupees"}
                    or not isinstance(row["symbol"], str)
                    or row["symbol"] in by_symbol
                    or not isinstance(row["isin"], str)
                    or not isinstance(row["filing_sha256"], str)
                    or not _DIGEST.fullmatch(row["filing_sha256"])
                    or not isinstance(row["close_rupees"], str)):
                raise ValueError("invalid audit company")
            try:
                close = Decimal(row["close_rupees"])
            except InvalidOperation as exc:
                raise ValueError("invalid audit close") from exc
            if not close.is_finite() or close <= 0:
                raise ValueError("invalid audit close")
            by_symbol[row["symbol"]] = row
        if {row["symbol"] for row in companies} != set(by_symbol):
            raise ValueError("audit issuer coverage")
        for row in companies:
            audit = by_symbol[row["symbol"]]
            if (row["isin"] != audit["isin"]
                    or row["filing_sha256"].lower() != audit["filing_sha256"]
                    or Decimal(str(row["close_rupees"])) != Decimal(audit["close_rupees"])):
                raise ValueError("audit mismatch")
    except (UnreviewedResearch, OSError, UnicodeError, ValueError, TypeError,
            KeyError, OverflowError, ArithmeticError) as exc:
        raise UnreviewedResearch("RESEARCH_RELEASE_NOT_INDEPENDENTLY_REVIEWED") from None
    return True
