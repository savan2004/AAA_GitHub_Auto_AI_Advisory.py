"""Independent discrete-quarter comparison; never approves an entire company."""
from datetime import date, datetime, timezone
from decimal import Decimal
from msk_ai_builder_evidence import EvidenceError, SecondarySnapshot
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_discovery import fetch_listing, parse_listing
from msk_ai_builder_filings import download_filing, extract_quarter


def check_latest_quarter(member, evidence, *, listing=fetch_listing,
                         download=download_filing, clock=lambda: datetime.now(timezone.utc),
                         audit=None):
    return _check_quarter(member, evidence, period_end=None, listing=listing,
                          download=download, clock=clock, audit=audit)


def check_historical_quarter(member, evidence, *, period_end, listing=fetch_listing,
                             download=download_filing, clock=lambda: datetime.now(timezone.utc),
                             audit=None):
    """Check one missing historical period, preserving existing latest evidence."""
    if type(period_end) is not date:
        raise EvidenceError("QUARTER_CHECK_PERIOD_NOT_IN_HISTORY")
    return _check_quarter(member, evidence, period_end=period_end, listing=listing,
                          download=download, clock=clock, audit=audit)


def _check_quarter(member, evidence, *, period_end, listing, download, clock, audit):
    if (not isinstance(member, IndexMember) or not isinstance(evidence, SecondarySnapshot)
            or member.symbol != evidence.symbol or evidence.eligible_for_selection
            or len(evidence.quarterly) != 8):
        raise EvidenceError("QUARTER_CHECK_IDENTITY")
    if evidence.scope not in ("standalone", "consolidated"):
        raise EvidenceError("QUARTER_CHECK_SCOPE_UNSUPPORTED")
    now = clock()
    if not 0 <= (now - evidence.retrieved_at).total_seconds() <= 86400:
        raise EvidenceError("QUARTER_CHECK_SECONDARY_STALE")
    latest = period_end is None
    if latest:
        period = evidence.quarterly[-1]
    else:
        matches = [row for row in evidence.quarterly[:-1] if row.end == period_end]
        if len(matches) != 1:
            raise EvidenceError("QUARTER_CHECK_PERIOD_NOT_IN_HISTORY")
        period = matches[0]
    payload = listing(member.symbol, period.end)
    ref = parse_listing(payload, symbol=member.symbol, period_end=period.end,
                        scope=evidence.scope, now=clock())
    doc = download(ref.url)
    if doc.url != ref.url:
        raise EvidenceError("QUARTER_CHECK_DOCUMENT_MISMATCH")
    result = extract_quarter(doc, member, expected_end=period.end, now=clock(), scope=evidence.scope)
    if result.board_approval > ref.broadcast_at.date():
        raise EvidenceError("QUARTER_CHECK_PUBLICATION_MISMATCH")
    # Compare the actual source definitions. Screener sometimes reports
    # revenue net of EXCISE, whereas issuer iXBRL reports gross operating
    # revenue and separately discloses excise under "Other Expenses".
    # This is allowed only after source-internal two-column reconciliation
    # in extract_quarter(); never invent an adjustment or edit vendor data.
    reported_revenue = Decimal(str(period.revenue_cr))
    raw_diff = abs(result.revenue_cr - reported_revenue)
    matched_basis = ("BANK_TOTAL_INTEREST_EARNED" if
                     result.revenue_definition == "Total interest earned" else
                     "ISSUER_REVENUE_FROM_OPERATIONS")
    comparable_revenue = result.revenue_cr
    if raw_diff > Decimal("0.51") and result.excise_cr is not None:
        comparable_revenue = result.revenue_cr - result.excise_cr
        matched_basis = "NET_OF_DISCLOSED_ISSUER_EXCISE"
    observation = {
        "symbol": member.symbol, "period": period.end.isoformat(),
        "scope": evidence.scope, "filing_url": ref.url,
        "document_sha256": result.digest,
        "issuer_revenue_cr": str(result.revenue_cr),
        "issuer_revenue_definition": result.revenue_definition,
        "reconciled_comparable_revenue_cr": str(comparable_revenue),
        "disclosed_excise_cr": (str(result.excise_cr)
                               if result.excise_cr is not None else None),
        "secondary_revenue_cr": str(period.revenue_cr),
        "revenue_comparison_basis": matched_basis,
        "issuer_profit_cr": str(result.profit_cr),
        "issuer_profit_definition": result.profit_definition,
        "secondary_profit_cr": str(period.profit_cr),
        "publication_status": "WITHHELD",
    }
    if audit is not None:
        audit(observation)
    if (comparable_revenue <= 0
            or abs(comparable_revenue - reported_revenue) > Decimal("0.51")
            or abs(result.profit_cr - Decimal(str(period.profit_cr))) > Decimal("0.51")):
        raise EvidenceError("QUARTER_CHECK_VALUE_MISMATCH")
    return {"symbol": member.symbol, "isin": member.isin,
            "period": period.end.isoformat(), "scope": evidence.scope,
            "filing_url": ref.url, "document_sha256": result.digest,
            "broadcast_at": ref.broadcast_at.isoformat(),
            "revised_at": ref.revised_at.isoformat() if ref.revised_at else None,
            **observation,
            "limitation": ("Numeric reconciliation for this discrete quarter only; full financial history, "
                           "source authenticity and profit-definition comparability not certified"),
            "status": ("LATEST_QUARTER_MATCHED_ONLY" if latest else "HISTORICAL_QUARTER_MATCHED_ONLY"),
            "publication_status": "WITHHELD"}
