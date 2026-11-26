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
from urllib.parse import urlsplit, urljoin
from html.parser import HTMLParser

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


# Reviewed issuer identity and official archive. A new company needs a
# separately reviewed symbol/ISIN/host mapping, never a model-generated host.
_ISSUER_ARCHIVES = {
    ("MCX", "INE745G01043"): "https://www.mcxindia.com/investor-relations/archive",
}
_ISSUER_PRESS = {
    ("MCX", "INE745G01043", date(2026, 9, 30)):
        "https://www.mcxindia.com/docs/default-source/media/press-release/english/2026/november/q2-fy27---press-release---03nov2026-_final.pdf",
}

class _ArchiveRows(HTMLParser):
    """Read actual archive table rows; never guess a document filename."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row = [], None
    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = {"text": [], "links": []}
        elif self.row is not None and tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.row["links"].append(href)
    def handle_data(self, data):
        if self.row is not None:
            self.row["text"].append(data)
    def handle_endtag(self, tag):
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def issuer_source_hints(member, period, *, fetch=None):
    """Bounded official issuer discovery, independent of AI and exchange listings."""
    key = (member.symbol, member.isin)
    archive = _ISSUER_ARCHIVES.get(key)
    if archive is None or type(period) is not date or (period.month, period.day) not in (
            (3,31), (6,30), (9,30), (12,31)):
        return {"status":"NO_REVIEWED_ISSUER_ROUTE", "urls":[]}
    press = _ISSUER_PRESS.get((*key, period))
    result = {"status":"ISSUER_ARCHIVE_UNAVAILABLE", "archive_url":archive,
              "press_release_url":press, "urls":[]}
    try:
        if fetch is None:
            import requests
            def fetch(url):
                with requests.get(url, headers={"User-Agent":"Mozilla/5.0"},
                                  timeout=(3,6), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200 or response.url != url:
                        raise ValueError("archive unavailable")
                    body = bytearray()
                    for chunk in response.iter_content(16384):
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ValueError("archive oversized")
                    return bytes(body)
        body = fetch(archive)
        if not isinstance(body,bytes) or not 0 < len(body) <= 2_000_000:
            raise ValueError("archive invalid")
        parser = _ArchiveRows()
        parser.feed(body.decode("utf-8-sig"))
        q = {6:1,9:2,12:3,3:4}[period.month]
        start = period.year if period.month != 3 else period.year-1
        title = f"financial results q{q} fy {start}-{start+1}"
        links = []
        for row in parser.rows:
            label = " ".join(" ".join(row["text"]).split()).lower()
            if label not in (title, title+" view pdf"):
                continue
            for href in row["links"]:
                url = urljoin(archive, href)
                u = urlsplit(url)
                if (u.scheme == "https" and u.netloc == "www.mcxindia.com"
                        and u.path.startswith("/docs/default-source/")
                        and u.path.lower().endswith(".pdf")
                        and not u.query and not u.fragment and len(url)<=512
                        and ".." not in u.path and "%" not in u.path
                        and url not in links):
                    links.append(url)
        result["status"] = "ISSUER_DOCUMENT_DISCOVERED_ONLY" if len(links)==1 else "ISSUER_DOCUMENT_NOT_UNIQUE"
        result["urls"] = links if len(links)==1 else []
    except Exception:
        pass
    return result


def ai_source_hints(symbol, period, scope, *, environ=None, request=None):
    """Opt-in source discovery only; no AI numbers, downloads or approval."""
    env = os.environ if environ is None else environ
    result = {"status": "DISABLED", "urls": []}
    if env.get("MSK_BUILDER_AI_SOURCE_HINTS") != "1":
        return result
    from msk_ai_builder_primary import SYMBOL
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or type(period) is not date or scope not in ("consolidated", "standalone"):
        return {"status": "INVALID_REQUEST", "urls": []}
    # Restrict to existing configured free-provider candidates. Never inherit
    # OpenAI/AskFuzz paid opt-ins or send customer holdings or financial data.
    free_env = {k: v for k, v in env.items() if k in (
        "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "MSK_GROQ_MODEL", "MSK_GEMINI_MODEL")}
    try:
        if request is None:
            from msk_ai_connector_v2 import request_ai
            request = request_ai
        response = request([{"role": "user", "content":
            f"Find exact original public exchange filing document URLs for NSE symbol {symbol}, "
            f"quarter ended {period.isoformat()}, {scope} results. NSE/BSE only. "
            "Return JSON array of up to three HTTPS PDF, XML or iXBRL document URLs. "
            "If you cannot locate exact URLs, return []. No financial numbers or guesses."}],
            system="Source discovery only. URLs are unverified leads, never financial evidence.",
            max_tokens=450, financial=False, env=free_env)
        if not response.ok:
            return {"status": "AI_UNAVAILABLE", "urls": []}
        values = json.loads(response.text)
        if not isinstance(values, list) or len(values) > 3:
            raise ValueError("invalid hints")
        urls = []
        for value in values:
            if not isinstance(value, str) or len(value) > 512 or not re.fullmatch(r"https://[A-Za-z0-9./_-]+", value):
                continue
            u = urlsplit(value)
            if u.netloc in ("www.bseindia.com", "bseindia.com"):
                valid = (u.path.startswith("/xml-data/corpfiling/AttachLive/") and u.path.lower().endswith(".pdf")
                         or u.path.startswith("/XBRLFILES/") and u.path.lower().endswith(".xml"))
            elif u.netloc in ("nsearchives.nseindia.com", "nsearchives.nseindia.in", "archives.nseindia.com"):
                valid = u.path.startswith("/corporate/") and u.path.lower().endswith((".pdf", ".xml", ".html"))
            else:
                valid = False
            if valid and ".." not in u.path and value not in urls:
                urls.append(value)
        return {"status": "UNVERIFIED_SOURCE_HINTS" if urls else "NO_SOURCE_HINTS", "urls": urls}
    except Exception:
        return {"status": "AI_HINTS_UNAVAILABLE", "urls": []}


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
        failure = {"symbol": symbol, "status": diagnostic["reason_code"],
                   "stage": diagnostic["stage"], "next_action": diagnostic["next_action"],
                   "listing_total": coverage.get("listing_total"),
                   "listing_rows": coverage.get("listing_rows"),
                   "publication_status": "WITHHELD"}
        # Missing exchange listing only. Prefer reviewed issuer archive
        # discovery, then optional AI leads. Never promote a press summary.
        if (failure["status"] == "FILING_METADATA_COVERAGE"
                and failure["listing_total"] == 0 and failure["listing_rows"] == 0):
            try:
                period = evidence.quarterly[-1].end
                if (member.symbol, member.isin) in _ISSUER_ARCHIVES:
                    failure["issuer_source_hints"] = issuer_source_hints(member, period)
                if (not failure.get("issuer_source_hints", {}).get("urls")
                        and os.getenv("MSK_BUILDER_AI_SOURCE_HINTS") == "1"):
                    failure["ai_source_hints"] = ai_source_hints(symbol, period, evidence.scope)
            except Exception:
                failure["source_discovery_status"] = "SOURCE_HINTS_UNAVAILABLE"
        return failure


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
                      "symbol", "status", "stage", "next_action", "listing_total", "listing_rows", "issuer_source_hints", "ai_source_hints")}
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
        if out["status"] == "FILING_METADATA_COVERAGE" and os.getenv("MSK_BUILDER_AI_SOURCE_HINTS") == "1":
            out["ai_source_hints"] = ai_source_hints(args.symbol, period, args.scope)
        status = 1
    print(json.dumps(out, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
