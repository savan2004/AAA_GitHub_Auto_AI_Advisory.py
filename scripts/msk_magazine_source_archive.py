"""Archive and reconcile source bytes for the November MSK Magazine.

Fail closed: a source URL, a search excerpt, or a claimed SHA in the issue
register is never proof that the original financial document was checked.
NSE Index Dashboard rows must match the *named row and named columns* of the
retrieved official PDF. These checks do not replace human financial approval.

No Telegram sends, production DB access, or publish action.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from pypdf import PdfReader
import requests


DASHBOARD_URL = "https://www.niftyindices.com/Index_Dashboard/Index_Dashboard_OCT2026.pdf"
MAX_SOURCE_BYTES = 14_000_000
COLUMN_KEYS = ("m1", "m3", "y1", "vol", "pe", "pb", "dy")
# NSE dashboard: 1M, 3M, 1Y, 3Y, 5Y, 1Y volatility,
# beta, correlation, R², PE, PB, dividend yield.
SOURCE_COLUMN_POSITIONS = {"m1": 0, "m3": 1, "y1": 2,
                           "vol": 5, "pe": 9, "pb": 10, "dy": 11}
_NUMBER = re.compile(r"^[+\-]?\d+(?:\.\d+)?$")
_EXPECTED_NAMES = {
    "Nifty 50", "Nifty Bank", "Nifty 500", "Nifty Midcap 150",
    "Nifty Smallcap 250", "Nifty 500 Equal Weight",
    "Nifty IT", "Nifty Pharma", "Nifty PSU Bank",
    "Nifty Healthcare Index", "Nifty Energy", "Nifty Auto",
    "Nifty Metal", "Nifty Capital Goods",
}


class SourceEvidenceError(ValueError):
    pass


def _date_tag(text: str) -> bool:
    norm = " ".join(text.lower().replace(",", " ").split())
    return "october 31 2026" in norm or "october 30 2026" in norm


def _safe_download(session, url: str) -> bytes:
    if url != DASHBOARD_URL:
        raise SourceEvidenceError("Unexpected NSE source URL")
    try:
        with session.get(url, timeout=(12, 35), allow_redirects=True,
                         stream=True,
                         headers={"User-Agent": "MSK-Magazine-Source-Verification/1.0",
                                  "Accept": "application/pdf"}) as response:
            response.raise_for_status()
            target = urlsplit(response.url)
            if (target.scheme != "https" or
                    target.hostname not in {"niftyindices.com", "www.niftyindices.com"}
                    or target.username or target.password or target.port):
                raise SourceEvidenceError("Official NSE URL redirected outside trusted origin")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=512 * 1024):
                if chunk:
                    body.extend(chunk)
                    if len(body) > MAX_SOURCE_BYTES:
                        raise SourceEvidenceError("Oversized NSE source PDF")
            if not body.startswith(b"%PDF-") or len(body) < 1000:
                raise SourceEvidenceError("NSE response was not a complete PDF")
            return bytes(body)
    except (requests.RequestException, OSError) as exc:
        raise SourceEvidenceError("Official NSE PDF download unavailable") from exc


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise SourceEvidenceError("Boolean cannot be a financial figure")
    try:
        x = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceEvidenceError("Invalid numeric source value") from exc
    if not x.is_finite():
        raise SourceEvidenceError("Non-finite source value")
    return x


def _extract_lines(pdf_bytes: bytes) -> tuple[list[str], bool]:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise SourceEvidenceError("PDF header missing")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if not 1 <= len(reader.pages) <= 40 or reader.is_encrypted:
            raise SourceEvidenceError("Unexpected NSE PDF structure")
        first = reader.pages[0].extract_text() or ""
        dated = _date_tag(first)
        lines = []
        for page in reader.pages:
            try:
                layout = page.extract_text(extraction_mode="layout",
                                           layout_mode_space_vertically=False) or ""
            except (TypeError, ValueError):
                layout = ""
            plain = page.extract_text() or ""
            # Parsed alternatives are evidence candidates; only a unique,
            # correctly ordered source row can validate financial figures.
            for txt in (layout, plain):
                for line in txt.replace("\u2212", "-").splitlines():
                    clean = " ".join(line.split())
                    if clean:
                        lines.append(clean)
        return list(dict.fromkeys(lines)), dated
    except SourceEvidenceError:
        raise
    except Exception as exc:
        raise SourceEvidenceError("Cannot extract NSE PDF text") from exc


def _find_named_row(lines: list[str], name: str) -> tuple[list[Decimal], str]:
    # A strict start-of-row match rejects substrings such as Nifty 500 within
    # Nifty 500 Equal Weight, and rejects numbers in unrelated rows.
    candidates = []
    for line in lines:
        if not line.startswith(name + " "):
            continue
        tail = line[len(name):].strip()
        cells = tail.split()
        if len(cells) < 12 or not all(_NUMBER.fullmatch(s) for s in cells[:12]):
            continue
        candidates.append((tuple(_decimal(s) for s in cells[:12]), line))
    unique = {numbers for numbers, _ in candidates}
    if len(unique) != 1:
        raise SourceEvidenceError(f"Missing or ambiguous dashboard row: {name}")
    values = next(iter(unique))
    original_line = next(line for cells, line in candidates if cells == values)
    return list(values), original_line


def verify_nse_dashboard(pdf_bytes: bytes, metrics: dict) -> dict:
    source = metrics.get("market_source", {})
    if source.get("url") != DASHBOARD_URL:
        raise SourceEvidenceError("Index dashboard URL does not match official source")
    if source.get("report_date") != "2026-10-31":
        raise SourceEvidenceError("Unrecognised index dashboard observation date")
    lines, dated = _extract_lines(pdf_bytes)
    if not dated:
        raise SourceEvidenceError("NSE dashboard PDF does not show October 2026 date")
    indices, sectors = metrics.get("market_indices"), metrics.get("sectors")
    if not isinstance(indices, list) or not isinstance(sectors, list):
        raise SourceEvidenceError("Missing named market indices or sector evidence")
    records = indices + sectors
    names = [r.get("name") for r in records]
    if len(records) != len(_EXPECTED_NAMES) or set(names) != _EXPECTED_NAMES:
        raise SourceEvidenceError("Metric roster does not match expected NSE index set")
    findings = []
    for row in records:
        numbers, original_line = _find_named_row(lines, row["name"])
        fields = COLUMN_KEYS if row in indices else ("m1",)
        for key in fields:
            expected = _decimal(row.get(key))
            observed = numbers[SOURCE_COLUMN_POSITIONS[key]]
            if observed != expected:
                raise SourceEvidenceError(
                    f"NSE figure mismatch: {row['name']} {key} "
                    f"expected {expected} observed {observed}"
                )
        findings.append({"name": row["name"], "checked_columns": list(fields),
                         "original_pdf_row": original_line})
    return {"url": DASHBOARD_URL, "report_date": "2026-10-31",
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "source_bytes": len(pdf_bytes),
            "checked_rows": len(findings),
            "matched_rows": findings,
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "EXACT_NAMED_ROW_AND_COLUMN_SOURCE_MATCH_NOT_EDITORIAL_APPROVAL"}


def archive_nse_dashboard(metrics_path: Path, output_dir: Path, *, session=None):
    metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    if metrics.get("issue") != "2026-11":
        raise SourceEvidenceError("Current NSE PDF archive is November 2026 issue-specific")
    body = _safe_download(session or requests.Session(), DASHBOARD_URL)
    result = verify_nse_dashboard(body, metrics)
    output = Path(output_dir)
    if output.exists():
        raise SourceEvidenceError("Source archive output already exists; will not overwrite")
    output.mkdir(parents=True, exist_ok=False)
    source_file = output / "Index_Dashboard_OCT2026.pdf"
    source_file.write_bytes(body)
    (output / "nse_dashboard_reconciliation.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-pack", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive_nse_dashboard(args.metrics_pack, args.output_dir),
                     indent=2))


if __name__ == "__main__":
    main()
