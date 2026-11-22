"""Independent latest-quarter comparison; never approves an entire company."""
from datetime import datetime, timezone
from decimal import Decimal
from msk_ai_builder_evidence import EvidenceError, SecondarySnapshot
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_discovery import fetch_listing, parse_listing
from msk_ai_builder_filings import download_filing, extract_quarter


def check_latest_quarter(member, evidence, *, listing=fetch_listing,
                         download=download_filing, clock=lambda: datetime.now(timezone.utc)):
    if (not isinstance(member, IndexMember) or not isinstance(evidence, SecondarySnapshot)
            or member.symbol != evidence.symbol or evidence.eligible_for_selection
            or len(evidence.quarterly) != 8):
        raise EvidenceError("QUARTER_CHECK_IDENTITY")
    if evidence.scope not in ("standalone", "consolidated"):
        raise EvidenceError("QUARTER_CHECK_SCOPE_UNSUPPORTED")
    now = clock()
    if not 0 <= (now - evidence.retrieved_at).total_seconds() <= 86400:
        raise EvidenceError("QUARTER_CHECK_SECONDARY_STALE")
    period = evidence.quarterly[-1]
    payload = listing(member.symbol, period.end)
    ref = parse_listing(payload, symbol=member.symbol, period_end=period.end,
                        scope=evidence.scope, now=clock())
    doc = download(ref.url)
    if doc.url != ref.url:
        raise EvidenceError("QUARTER_CHECK_DOCUMENT_MISMATCH")
    result = extract_quarter(doc, member, expected_end=period.end, now=clock(), scope=evidence.scope)
    if result.board_approval > ref.broadcast_at.date():
        raise EvidenceError("QUARTER_CHECK_PUBLICATION_MISMATCH")
    for primary, secondary in ((result.revenue_cr, period.revenue_cr),
                               (result.profit_cr, period.profit_cr)):
        if abs(primary - Decimal(str(secondary))) > Decimal("0.51"):
            raise EvidenceError("QUARTER_CHECK_VALUE_MISMATCH")
    return {"symbol": member.symbol, "isin": member.isin,
            "period": period.end.isoformat(), "scope": evidence.scope,
            "filing_url": ref.url, "document_sha256": result.digest,
            "broadcast_at": ref.broadcast_at.isoformat(),
            "revised_at": ref.revised_at.isoformat() if ref.revised_at else None,
            "issuer_revenue_cr": str(result.revenue_cr),
            "issuer_profit_cr": str(result.profit_cr),
            "issuer_profit_definition": "Total profit (loss) for period",
            "limitation": "Numeric comparison only; profit definition equivalence and full financial history not certified",
            "secondary_revenue_cr": str(period.revenue_cr),
            "secondary_profit_cr": str(period.profit_cr),
            "status": "LATEST_QUARTER_MATCHED_ONLY", "publication_status": "WITHHELD"}
