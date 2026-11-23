"""Source-byte archival and contextual numeric verification for MSK magazine.

Each official HTML document is downloaded, content-hashed, and matched to
source-specific words and figures. A link or copied number alone is not
sufficient. Independent editorial approval remains a separate mandatory gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests


MAX_HTML_BYTES = 3_000_000
# Match on the original issuer/government host, never a third-party summary.
SOURCES = {
    "rbi_repo": {
        "url": "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63742",
        "hosts": ("rbi.org.in", "www.rbi.org.in"),
        "patterns": (r"monetary policy", r"october", r"2026",
                     r"policy repo rate.{0,230}5[.]50"),
    },
    "mospi_cpi": {
        "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2335416&lang=1&reg=3",
        "hosts": ("pib.gov.in", "www.pib.gov.in"),
        "patterns": (r"consumer price index", r"october.{0,45}2026",
                     r"6[.]09", r"8[.]94", r"food inflation"),
    },
    "mospi_iip": {
        "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2329475&lang=2&reg=48",
        "hosts": ("pib.gov.in", "www.pib.gov.in"),
        "patterns": (r"index of industrial production", r"september.{0,45}2026",
                     r"6[.]7", r"quick estimate"),
    },
    "tcs_q2": {
        # Official TCS *consolidated* September 2026 filing at NSE.
        # TCS.com's public release responded 403 to GitHub Actions.
        # Unlike the press release this exchange filing reports INR
        # amounts; the magazine must disclose that different basis.
        "url": "https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_200121_08102026220810_iXBRL_WEB.html",
        "hosts": ("nsearchives.nseindia.com",),
        "patterns": (r"tcs", r"consolidated", r"01-07-2026",
                     r"30-09-2026", r"revenue from operations.{0,115}73,18,800[.]00"),
    },
    "persistent_q2": {
        "url": "https://www.persistent.com/media/press-releases/q2-fy27-results/",
        "hosts": ("persistent.com", "www.persistent.com"),
        "patterns": (r"q2.{0,8}fy.{0,8}27", r"487[.]6", r"20[.]0",
                     r"15[.]0", r"october.{0,30}2026"),
    },
    "coforge_q2": {
        "url": "https://news.coforge.com/newsroom/press-release/coforge-delivers-an-outstanding-performance-revenue-growth-536-yoy-raises-ebit-margin-guidance-for-the-year?hs_amp=true",
        "hosts": ("coforge.com", "www.coforge.com", "news.coforge.com"),
        "patterns": (r"q2\s*fy\s*27", r"652[.]2", r"40[.]1",
                     r"ebit margin", r"16\s*%", r"october.{0,30}2026"),
    },
    "mospi_gdp_calendar": {
        "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2304949&lang=1&reg=3",
        "hosts": ("pib.gov.in", "www.pib.gov.in"),
        "patterns": (r"second quarter", r"2026-27", r"november.{0,35}2026"),
    },
}


class PrimarySourceError(ValueError):
    pass


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.words = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.words.append(data)


def _body_text(body: bytes) -> str:
    try:
        markup = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrimarySourceError("Non-UTF8 official HTML") from exc
    parser = _HTMLText()
    parser.feed(markup)
    return " ".join(" ".join(parser.words).split()).casefold()


def verify_source_html(key: str, url: str, body: bytes) -> dict:
    if key not in SOURCES:
        raise PrimarySourceError("Unknown primary source identity")
    config = SOURCES[key]
    if url != config["url"]:
        raise PrimarySourceError("Primary source URL mismatch")
    if len(body) < 150 or len(body) > MAX_HTML_BYTES:
        raise PrimarySourceError("Invalid primary source size")
    text = _body_text(body)
    for pattern in config["patterns"]:
        if re.search(pattern, text, flags=re.IGNORECASE) is None:
            raise PrimarySourceError(f"Source {key}: missing context / figure: {pattern}")
    return {"key": key, "url": url, "source_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "verified_context_pattern_count": len(config["patterns"]),
            "verification_status": "SOURCE_TEXT_CONTEXT_MATCH_NOT_HUMAN_APPROVAL"}


def _download(session, config: dict) -> bytes:
    try:
        with session.get(
            config["url"], timeout=(12, 35), allow_redirects=True, stream=True,
            headers={"User-Agent": "MSK-Magazine-Evidence/1.0",
                     "Accept": "text/html,application/xhtml+xml"},
        ) as response:
            response.raise_for_status()
            u = urlsplit(response.url)
            if (u.scheme != "https" or u.hostname not in config["hosts"] or
                    u.username or u.password or u.port):
                raise PrimarySourceError("Official source redirected to unapproved host")
            data = bytearray()
            for block in response.iter_content(chunk_size=256 * 1024):
                if block:
                    data.extend(block)
                    if len(data) > MAX_HTML_BYTES:
                        raise PrimarySourceError("Source response size exceeds 3MB")
            if b"<html" not in bytes(data[:1500]).lower() and b"<!doctype" not in bytes(data[:1500]).lower():
                raise PrimarySourceError("Source response is not HTML")
            return bytes(data)
    except (requests.RequestException, OSError) as exc:
        raise PrimarySourceError("Unable to retrieve official primary-source HTML") from exc


def archive_primary_sources(output_dir: Path, *, session=None) -> dict:
    root = Path(output_dir)
    if root.exists():
        raise PrimarySourceError("Cannot overwrite primary source archive")
    root.mkdir(parents=True, exist_ok=False)
    conn = session or requests.Session()
    entries = []
    for key, config in SOURCES.items():
        body = _download(conn, config)
        result = verify_source_html(key, config["url"], body)
        rel = key + ".html"
        (root / rel).write_bytes(body)
        result["path"] = rel
        entries.append(result)
    ledger = {
        "issue": "2026-11",
        "archived_utc": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(entries),
        "status": "ORIGINAL_BYTES_CAPTURED_AND_SOURCE_FACTS_MATCHED_NOT_PUBLICATION_APPROVAL",
        "entries": entries,
    }
    (root / "primary_source_ledger.json").write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ledger


def recheck_primary_sources(root: Path) -> dict:
    folder = Path(root)
    ledger_file = folder / "primary_source_ledger.json"
    if ledger_file.is_symlink() or not ledger_file.is_file():
        raise PrimarySourceError("Missing safe original primary-source ledger")
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    entries = ledger.get("entries", [])
    if len(entries) != len(SOURCES) or {x.get("key") for x in entries} != set(SOURCES):
        raise PrimarySourceError("Missing required source evidence")
    for record in entries:
        key = record["key"]
        file = folder / (key + ".html")
        if file.is_symlink() or not file.is_file():
            raise PrimarySourceError("Missing original HTML file")
        receipt = verify_source_html(key, record.get("url", ""), file.read_bytes())
        if receipt["source_sha256"] != record.get("source_sha256"):
            raise PrimarySourceError("Original HTML bytes changed since verification")
    return ledger


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = archive_primary_sources(args.output_dir)
    print(json.dumps({"sources_checked": result["sources_checked"],
                      "status": result["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
