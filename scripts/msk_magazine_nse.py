"""Download official NSE month-end index CSV, preserving exact source bytes.

This collects source-bound index CLOSES only, not stocks, FII, breadth,
fundamental recommendations, live prices or research conclusions.
Network failures and missing index names are explicit, never zero-filled.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
ARCHIVES = ("https://archives.nseindia.com/content/indices/",
            "https://nsearchives.nseindia.com/content/indices/")
INDICES = {
    "NIFTY 50": (3, 5),
    "NIFTY BANK": (3, 6),
    "NIFTY MIDCAP 150": (7, 10),
    "NIFTY SMALLCAP 250": (7, 11),
}
USER_AGENT = "Mozilla/5.0 (compatible; MSK-Magazine-Research/1.0)"
MAX_BYTES = 2_000_000


def previous_month_end(year: int, month: int) -> date:
    return date(year, month, 1) - timedelta(days=1)


def _decode_source(body: bytes) -> tuple[list[dict], dict]:
    if not body or len(body) > MAX_BYTES:
        raise ValueError("Empty or oversized NSE index CSV")
    content = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    fields = reader.fieldnames or []
    lookup = {field.strip().casefold(): field for field in fields if field}
    if "closing index value" in lookup:
        if "closing" in lookup:
            raise ValueError("Ambiguous NSE closing columns")
        lookup["closing"] = lookup["closing index value"]
    required = ("index name", "index date", "closing")
    if any(name not in lookup for name in required):
        raise ValueError("NSE CSV required columns not found")
    result = []
    raw_lines = content.splitlines()
    for row in reader:
        if not row:
            continue
        name = (row.get(lookup["index name"]) or "").strip().upper()
        if name not in INDICES:
            continue
        day = (row.get(lookup["index date"]) or "").strip()
        try:
            observed = datetime.strptime(day, "%d-%m-%Y").date()
        except ValueError:
            raise ValueError("NSE source has unparseable index date")
        close = (row.get(lookup["closing"]) or "").strip()
        try:
            value = Decimal(close.replace(",", ""))
            if not value.is_finite() or value <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError("NSE index closing value is invalid")
        # The exact original line is used as the source quotation.
        # CSV records with embedded newlines are rejected for evidence safety.
        matches = [line for line in raw_lines[1:]
                   if name in line.upper() and day in line and close in line]
        if len(matches) != 1:
            raise ValueError(f"Cannot prove unique CSV row for {name}")
        result.append({"name": name, "observed": observed,
                       "value": str(value), "excerpt": matches[0]})
    return result, {"fields": fields, "total_rows": len(raw_lines) - 1}


def _download(url: str, *, session=requests, timeout: int = 15) -> tuple[bytes, str]:
    r = session.get(url, headers={"User-Agent": USER_AGENT,
                                 "Accept": "text/csv,text/plain,*/*"},
                    timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    filename = url.rsplit("/", 1)[1]
    if r.url not in {base + filename for base in ARCHIVES}:
        raise ValueError("Response URL differs from the requested dated NSE archive")
    content = r.content
    if not isinstance(content, bytes) or len(content) > MAX_BYTES:
        raise ValueError("NSE archive response is too large")
    return content, r.url


def collect(issue: str, output_dir: Path, *, now: date | None = None,
            session=requests, lookback_days: int = 8) -> dict:
    if not isinstance(issue, str) or len(issue) != 7 or issue[4] != "-":
        raise ValueError("Issue must use YYYY-MM")
    year, month = map(int, issue.split("-"))
    month_start = date(year, month, 1)
    now = now or datetime.now(IST).date()
    if month_start > now:
        raise ValueError("Future issue cannot collect sources")
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = previous_month_end(year, month)
    failures = []
    for d in range(lookback_days + 1):
        day = target - timedelta(days=d)
        if day.weekday() > 4:
            continue
        for base in ARCHIVES:
            url = base + "ind_close_all_" + day.strftime("%d%m%Y") + ".csv"
            try:
                raw, source_url = _download(url, session=session)
                rows, meta = _decode_source(raw)
                if not rows or any(r["observed"] != day for r in rows):
                    raise ValueError("No matching NSE index date in CSV")
                expected = set(INDICES)
                seen = {r["name"] for r in rows}
                if len(rows) != len(seen):
                    raise ValueError("Duplicate NSE index name in CSV")
                if not expected.issubset(seen):
                    raise ValueError("Missing required broad-market index in NSE CSV")
                filename = "nse_indices_" + day.strftime("%Y%m%d") + ".csv"
                (dest / filename).write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                observations = []
                for row in rows:
                    for page in INDICES[row["name"]]:
                        observations.append({
                            "page": page, "label": row["name"] + " closing level",
                            "value": row["value"], "unit": "index points",
                            "observed_on": day.isoformat(), "source_url": source_url,
                            "source_file": filename, "source_sha256": digest,
                            "source_excerpt": row["excerpt"],
                        })
                pack = {"issue": issue, "source": "NSE daily official indices CSV",
                        "source_date": day.isoformat(),
                        "retrieved_at": datetime.now(IST).isoformat(),
                        "evidence_status": "SOURCE_BYTES_CAPTURED; HUMAN REVIEW REQUIRED",
                        "observations": observations, "meta": meta}
                (dest / "source_pack.json").write_text(
                    json.dumps(pack, indent=2) + "\n", encoding="utf-8")
                return {"success": True, "source_date": day.isoformat(),
                        "observations": len(observations), "sha256": digest,
                        "failures": failures}
            except (requests.RequestException, UnicodeError, ValueError, OSError) as e:
                failures.append(f"{day.isoformat()} {urlsplit(url).hostname}: {type(e).__name__} {e}")
    result = {"success": False, "observations": 0,
              "failures": failures, "reason": "No validated official NSE archive obtained"}
    (dest / "source_fetch_failures.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--issue", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    result = collect(a.issue, Path(a.output_dir))
    print(json.dumps(result))
    # A failed official download must NOT terminate the entire monthly draft.
    # The downstream renderer will withhold financial charts and values.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
