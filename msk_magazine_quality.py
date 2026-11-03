"""Strict editorial readiness gate for MSK magazine. No publishing or external calls."""
from collections import Counter
from datetime import date
from urllib.parse import urlsplit

BLOCKED_PHRASES = (
    "research content pending", "not release-ready", "synthetic fixture",
    "placeholder", "unpopulated design preview", "no verified data",
    "content intentionally withheld", "source validation",
)
RESEARCH_PAGE_INDICES = range(2, 15)


def audit_release_pack(pack):
    """Return actionable failures. Passing structural PDF tests is not release approval."""
    failures = []
    if not isinstance(pack, dict):
        return ["Missing structured research pack"]
    pages = pack.get("pages")
    if not isinstance(pages, list) or len(pages) != 16:
        return ["Magazine requires exactly 16 researched pages"]
    if pack.get("approved") is not True:
        failures.append("Human editorial approval not recorded")
    if not isinstance(pack.get("approved_by"), str) or len(pack["approved_by"].strip()) < 3 if isinstance(pack.get("approved_by"), str) else True:
        failures.append("Named editorial reviewer missing")
    summaries = []
    for idx in RESEARCH_PAGE_INDICES:
        page = pages[idx]
        if not isinstance(page, dict):
            failures.append(f"Page {idx + 1}: missing content")
            continue
        summary = page.get("summary", "")
        if not isinstance(summary, str) or len(summary.strip()) < 80:
            failures.append(f"Page {idx + 1}: insufficient original analysis")
            continue
        summaries.append(summary.strip().casefold())
        text = summary + " " + " ".join(
            str(f.get("statement", "")) for f in page.get("evidence", [])
            if isinstance(f, dict)
        )
        if any(phrase in text.casefold() for phrase in BLOCKED_PHRASES):
            failures.append(f"Page {idx + 1}: placeholder or synthetic content")
        facts = page.get("evidence")
        if not isinstance(facts, list) or len(facts) < 3:
            failures.append(f"Page {idx + 1}: fewer than three source-backed claims")
            continue
        hosts = set()
        for fact in facts:
            if not isinstance(fact, dict):
                failures.append(f"Page {idx + 1}: malformed evidence")
                continue
            url = fact.get("source_url", "")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname:
                failures.append(f"Page {idx + 1}: missing HTTPS source")
            else:
                hosts.add(parsed.hostname.removeprefix("www."))
            try:
                observed = date.fromisoformat(fact["observed_on"])
                cutoff = date.fromisoformat(pack["as_of"])
                if observed > cutoff:
                    failures.append(f"Page {idx + 1}: future observation")
            except (ValueError, KeyError, TypeError):
                failures.append(f"Page {idx + 1}: invalid source date")
        if len(hosts) < 2:
            failures.append(f"Page {idx + 1}: insufficient independent source hosts")
    for summary, count in Counter(summaries).items():
        if count > 1:
            failures.append(f"Repeated research text across {count} pages")
    return failures
