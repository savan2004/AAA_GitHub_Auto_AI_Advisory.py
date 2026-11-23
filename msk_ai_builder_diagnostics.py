"""Machine-readable, fail-closed diagnostics for MSK issuer-research staging.

Never treats a numerical quarter match as a stock approval. No credentials,
customer holdings, raw provider response bodies or exception strings are logged.
"""
from collections import Counter
from datetime import date, datetime, timezone
import argparse
import json
import os
import re

from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_discovery import fetch_listing, parse_listing
from msk_ai_builder_filings import download_filing
from msk_ai_builder_quarter_check import check_latest_quarter

APPROVED_STATUS = "LATEST_QUARTER_MATCHED_ONLY"  # observation, NOT customer approval

# Diagnose *which contract failed* without guessing its cause or relaxing it.
_FAILURE_RULES = (
    ("FILING_METADATA_COVERAGE", "DISCOVERY", "Inspect official NSE filing coverage; if zero, find a dated issuer/exchange source and build a separately validated parser."),
    ("FILING_METADATA_", "DISCOVERY", "Review exact NSE symbol, fiscal quarter, original/revised publication timestamps and complete listing pagination."),
    ("FILING_URL_", "SOURCE_URL", "Check the exact issuer filing template and HTTPS host allowlist; never accept an unverified redirect."),
    ("FILING_HTTP_", "SOURCE_FETCH", "Inspect the official source HTTP response and rate limits; keep missing evidence withheld."),
    ("FILING_DOWNLOAD_", "SOURCE_FETCH", "Check the official filing response, content type, byte limits and transport."),
    ("FILING_MISSING_OR_AMBIGUOUS_FIELD_", "PARSER_FIELD", "Compare the named field with the source's original HTML; add a narrowly scoped contract and regression fixture."),
    ("FILING_FINANCIAL_LAYOUT", "PARSER_LAYOUT", "Inspect income-statement table identity, reporting period and scope; do not fall back to segment data."),
    ("FILING_COLUMN_ORDER_UNSUPPORTED", "PARSER_LAYOUT", "Inspect the source's quarter versus year-to-date column labels and order."),
    ("FILING_GENERAL_LAYOUT", "PARSER_LAYOUT", "Review the issuer's general identity table; different financial templates require separate tests."),
    ("FILING_", "FILING_VALIDATION", "Inspect the documented primary-source schema, units, period, identity or rounding contract."),
    ("BANK_", "BANK_RECONCILIATION", "Check exact bank interest, total income and profit identities in both quarter and YTD columns."),
    ("EXCISE_", "EXCISE_RECONCILIATION", "Check the issuer's excise and other-expenses subtotal for both source columns."),
    ("QUARTER_CHECK_VALUE_MISMATCH", "NUMERIC_COMPARISON", "Compare original issuer and secondary numbers with identical scope, definition and period; do not alter either figure."),
    ("QUARTER_CHECK_PUBLICATION_MISMATCH", "PUBLICATION_DATE", "Check issuer board date against original exchange broadcast timestamp."),
    ("QUARTER_CHECK_SECONDARY_STALE", "SECONDARY_FRESHNESS", "Refresh the dated secondary data before another comparison."),
    ("QUARTER_CHECK_", "QUARTER_VALIDATION", "Check source identity, fiscal period, reporting scope and comparison preconditions."),
)
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,119}\Z")


def classify_failure(error):
    """Return only known, static guidance and a safe bounded reason code."""
    code = str(error)
    if not _SAFE_CODE.fullmatch(code):
        code = "UNRECOGNIZED_EVIDENCE_ERROR"
    for prefix, stage, action in _FAILURE_RULES:
        if code.startswith(prefix):
            return {"reason_code": code, "stage": stage, "next_action": action}
    return {"reason_code": code, "stage": "UNKNOWN_VERIFICATION",
            "next_action": "Inspect the isolated failing source contract; publication stays withheld."}


def diagnose_quarter(member, evidence, *, listing=fetch_listing,
                     download=download_filing, clock=None, audit=None):
    """Run the same strict production comparison with a bounded failure record.

    Wrappers retain useful metadata (count and source template) without saving
    provider bodies. No retries, bypasses or financial-data substitutions.
    """
    from msk_ai_builder_primary import IndexMember
    symbol = member.symbol if isinstance(member, IndexMember) else "UNKNOWN"
    phase = {"name": "EVIDENCE_VALIDATION"}
    coverage = {}
    def observed_listing(sym, end):
        phase["name"] = "DISCOVERY"
        payload = listing(sym, end)
        if isinstance(payload, dict):
            count = payload.get("totalCount")
            coverage["listing_total"] = count if type(count) is int and 0 <= count <= 10000 else None
            rows = payload.get("data")
            coverage["listing_rows"] = len(rows) if isinstance(rows, list) else None
        phase["name"] = "SOURCE_URL"
        return payload
    def observed_download(url):
        phase["name"] = "SOURCE_FETCH"
        result = download(url)
        # No raw URL text leaks; the validated URL is already stored in a
        # successful comparison by check_latest_quarter itself.
        phase["name"] = "FILING_VALIDATION"
        return result
    def observed_audit(data):
        phase["name"] = "NUMERIC_COMPARISON"
        if audit is not None:
            audit(data)
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    try:
        result = check_latest_quarter(
            member, evidence, listing=observed_listing,
            download=observed_download, clock=clock, audit=observed_audit)
        return {**result, "stage": "LATEST_QUARTER_ONLY",
                "next_action": "Complete all historic-quarter, annual, valuation, corporate-action and release verification.",
                "listing_total": coverage.get("listing_total"),
                "publication_status": "WITHHELD"}
    except EvidenceError as exc:
        diagnostic = classify_failure(exc)
        return {"symbol": symbol, "status": diagnostic["reason_code"],
                "stage": diagnostic["stage"], "next_action": diagnostic["next_action"],
                "listing_total": coverage.get("listing_total"),
                "listing_rows": coverage.get("listing_rows"),
                "publication_status": "WITHHELD"}


def diagnostic_summary(rows, *, requested=None):
    """Small actionable result for CI: exactly which issuer and contract failed."""
    checked = list(rows)
    errors = [r for r in checked if r.get("status") != APPROVED_STATUS]
    counts = Counter(r.get("stage", "UNKNOWN_VERIFICATION") for r in errors)
    output = {"publication_status": "WITHHELD", "stage": "LATEST_QUARTER_ONLY",
              "checked": len(checked),
              "matched": len(checked) - len(errors),
              "requested": requested if requested is not None else len(checked),
              "failure_counts_by_stage": dict(sorted(counts.items())),
              "failures": [
                  {k: r.get(k) for k in (
                      "symbol", "status", "stage", "next_action", "listing_total", "listing_rows")}
                  for r in errors
              ]}
    output["rehearsal_passed"] = (
        type(output["requested"]) is int and output["requested"] > 0
        and len(checked) == output["requested"] and not errors)
    return output


def diagnostic_markdown(summary):
    """GitHub step summary; static corrective hints, no provider payload."""
    lines = [
        "## MSK Builder — Primary Quarter Source Diagnostic",
        "",
        "Publication: **WITHHELD** (a numeric quarter match is not stock approval).",
        "",
        "**Latest-quarter numerical matches: {}/{}**".format(
            summary["matched"], summary["requested"]),
        "",
        "| Company | Verification stage | Failure code | Next investigation |",
        "|---|---|---|---|",
    ]
    for r in summary["failures"]:
        # Symbol/status/stage validated by the internal checks; neutralize
        # any unlikely markdown delimiters rather than generating HTML.
        def safe(x):
            return re.sub(r"[^A-Za-z0-9_ .,/;:()\-]", "", str(x or ""))[:240]
        lines.append("| {} | {} | {} | {} |".format(
            safe(r["symbol"]), safe(r["stage"]), safe(r["status"]), safe(r["next_action"])))
    if not summary["failures"]:
        lines.append("| — | Latest-quarter matching only | — | Complete remaining primary-source and approval gates |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    """Fast targeted source check: does not run the slow 20-issuer refresh."""
    parser = argparse.ArgumentParser(description="Verify one NSE issuer filing reference only")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--scope", choices=("standalone", "consolidated"), required=True)
    args = parser.parse_args(argv)
    from msk_ai_builder_primary import SYMBOL
    try:
        period = date.fromisoformat(args.period_end)
        if not SYMBOL.fullmatch(args.symbol):
            raise ValueError("invalid symbol")
    except ValueError:
        parser.error("Expected NSE symbol and ISO date YYYY-MM-DD")
    now = datetime.now(timezone.utc)
    try:
        payload = fetch_listing(args.symbol, period)
        ref = parse_listing(payload, symbol=args.symbol, period_end=period,
                            scope=args.scope, now=now)
        out = {"symbol": args.symbol, "status": "REFERENCE_DISCOVERED_ONLY",
               "source_template": ref.url.split("/corporate/ixbrl/")[-1].split("_", 3)[:3],
               "publication_status": "WITHHELD"}
        status = 0
    except EvidenceError as exc:
        out = {"symbol": args.symbol, "status": classify_failure(exc)["reason_code"],
               **classify_failure(exc), "publication_status": "WITHHELD"}
        status = 1
    print(json.dumps(out, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
