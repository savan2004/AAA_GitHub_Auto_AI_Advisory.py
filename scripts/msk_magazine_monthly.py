"""MSK monthly magazine: fail-closed draft generator and mechanical PDF QA.

Only reads owner-approved Design 3 index and optional source evidence.
NEVER calls Telegram, places trades, approves, or publishes an edition.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MASTER = Path(__file__).resolve().parents[1] / "docs/msk_magazine/LOCKED_DESIGN3_INDEX_MASTER.json"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
OFFICIAL_HOSTS = {
    "nseindia.com", "bseindia.com", "rbi.org.in", "mospi.gov.in",
    "sebi.gov.in", "dipam.gov.in", "indiabudget.gov.in",
    "pib.gov.in", "mca.gov.in", "federalreserve.gov", "bea.gov", "bls.gov",
}
NAVY = "#132B46"
TEAL = "#007D84"
GRAY = "#536A78"
LIGHT = "#EEF5F6"
WHITE = "#FFFFFF"
UNAPPROVED = "UNAPPROVED EDITORIAL DRAFT - NOT FOR DISTRIBUTION"


class MagazineError(ValueError):
    """An input, evidence, or release gate was rejected."""


def first_monday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7)


def is_monthly_due(now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise MagazineError("Scheduling requires an aware time")
    local = now.astimezone(IST).date()
    return local == first_monday(local.year, local.month)


def issue_for(now: datetime) -> str:
    if now.tzinfo is None:
        raise MagazineError("Scheduling requires an aware time")
    return now.astimezone(IST).strftime("%Y-%m")


def load_master(path: Path = DEFAULT_MASTER) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    index = data.get("locked_index")
    if data.get("page_count") != 16 or data.get("page_size") != "A4 portrait":
        raise MagazineError("Design 3 A4/16-page master changed")
    if not isinstance(index, list) or len(index) != 16:
        raise MagazineError("The complete 16-page index is required")
    pages = [x.get("page") for x in index]
    titles = [x.get("title") for x in index]
    if pages != list(range(1, 17)) or len(set(titles)) != 16:
        raise MagazineError("Locked magazine page order or titles changed")
    if not all(isinstance(t, str) and t.strip() for t in titles):
        raise MagazineError("Invalid master page title")
    if data.get("colors") != {"white": WHITE, "navy": NAVY, "teal": TEAL}:
        raise MagazineError("Approved branding colours changed")
    return data


def _number(text: str) -> str:
    try:
        val = Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise MagazineError("Evidence value must be numeric")
    if not val.is_finite():
        raise MagazineError("Non-finite evidence value")
    return str(val)


def _official_host(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and
                not parsed.password and not parsed.port and
                any(host == suffix or host.endswith("." + suffix)
                    for suffix in OFFICIAL_HOSTS))
    except (TypeError, ValueError):
        return False


def _source_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if path.suffix.lower() not in {".txt", ".csv", ".json", ".html", ".htm"}:
        raise MagazineError("Unsupported source-file format")
    return path.read_text(encoding="utf-8")


def _receipt_timestamp(value: str) -> datetime | None:
    """Parse a receipt timestamp only when it carries an explicit UTC offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_source_receipts(pack: dict, accepted_rows: dict[int, list[dict]],
                             source_root: Path, issue: str, as_of: date,
                             validation_time: datetime | None = None) -> dict:
    """Check receipt structure and local byte consistency, not authenticity.

    Receipt claims are self-reported. A passing check never authenticates source
    retrieval or reviewer identity; that remains a separate release requirement.
    """
    validation_time = validation_time or datetime.now(timezone.utc)
    if validation_time.tzinfo is None or validation_time.utcoffset() is None:
        return {"passed": False, "receipts_checked": 0,
                "failures": ["Validation timestamp must include timezone"]}
    validation_time = validation_time.astimezone(timezone.utc)
    accepted = [row for rows in accepted_rows.values() for row in rows]
    failures = []
    if not accepted:
        return {"passed": False, "receipts_checked": 0,
                "failures": ["No accepted source observations to receipt-check"]}
    receipts = pack.get("source_receipts")
    if not isinstance(receipts, list) or not receipts:
        return {"passed": False, "receipts_checked": 0,
                "failures": ["No source_receipts handoff records supplied"]}
    root = source_root.resolve(strict=True)
    expected = {(row["source_url"], row["source_file"], row["source_sha256"])
                for row in accepted}
    checked = set()
    for i, receipt in enumerate(receipts, 1):
        try:
            if not isinstance(receipt, dict):
                raise MagazineError("Receipt must be an object")
            if receipt.get("issue") != issue:
                raise MagazineError("Receipt issue mismatch")
            key = (receipt.get("source_url"), receipt.get("source_file"),
                   receipt.get("source_sha256"))
            if key not in expected:
                raise MagazineError("Receipt does not match an accepted observation")
            if key in checked:
                raise MagazineError("Duplicate receipt for source bytes")
            fetched = _receipt_timestamp(receipt.get("fetched_at_utc"))
            reviewed = _receipt_timestamp(receipt.get("reviewed_at_utc"))
            if fetched is None or reviewed is None:
                raise MagazineError("Fetch/review timestamps must include timezone")
            if fetched > validation_time or reviewed > validation_time:
                raise MagazineError("Receipt timestamp is in the future relative to validation")
            if fetched.date() > as_of or reviewed.date() > as_of:
                raise MagazineError("Receipt timestamp is after issue evidence cutoff")
            if reviewed < fetched:
                raise MagazineError("Review timestamp precedes source retrieval")
            if type(receipt.get("http_status")) is not int or receipt["http_status"] != 200:
                raise MagazineError("Source retrieval must record HTTP 200")
            if receipt.get("reviewer_verified") is not True:
                raise MagazineError("Independent reviewer confirmation is missing")
            reviewer = receipt.get("reviewer")
            notes = receipt.get("review_notes")
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise MagazineError("Reviewer name/identity is missing")
            if not isinstance(notes, str) or len(notes.strip()) < 20:
                raise MagazineError("Reviewer cross-check notes are missing")
            name = receipt.get("source_file")
            if not isinstance(name, str) or not name or Path(name).is_absolute():
                raise MagazineError("Receipt source file must be relative")
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise MagazineError("Receipt source file outside trusted directory")
            if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get("source_sha256"):
                raise MagazineError("Receipt byte checksum mismatch")
            checked.add(key)
        except (MagazineError, OSError, ValueError, TypeError, KeyError) as exc:
            failures.append(f"Receipt {i}: {exc}")
    missing = expected - checked
    if missing:
        failures.append(f"Missing receipts for {len(missing)} accepted source observation(s)")
    return {"passed": not failures and checked == expected,
            "receipts_checked": len(checked), "failures": failures,
            "schema_only": True,
            "human_independent_authentication_required": True}



def validate_handoff_manifest(pack: dict, issue: str,
                              validation_time: datetime | None = None) -> dict:
    """Validate the edition-level AiAutoUpdate handoff envelope.

    This checks required fields and date/count consistency only. It cannot
    authenticate the source chat, publisher, approval, or document contents.
    """
    failures = []
    validation_time = validation_time or datetime.now(timezone.utc)
    if not isinstance(validation_time, datetime) or validation_time.tzinfo is None or validation_time.utcoffset() is None:
        return {"passed": False, "failures": ["Validation timestamp must include timezone"],
                "schema_only": True, "human_independent_authentication_required": True}
    validation_time = validation_time.astimezone(timezone.utc)
    receipt = pack.get("handoff_receipt") if isinstance(pack, dict) else None
    if not isinstance(receipt, dict):
        return {"passed": False, "failures": ["No edition-level handoff_receipt supplied"],
                "schema_only": True, "human_independent_authentication_required": True}

    if receipt.get("issue") != issue:
        failures.append("Handoff issue mismatch")
    if receipt.get("source_project") != "AiAutoUpdate":
        failures.append("Source project must be AiAutoUpdate")
    if not isinstance(receipt.get("source_chat"), str) or not receipt["source_chat"].strip():
        failures.append("Source chat/channel identity is missing")
    message_id = receipt.get("source_message_id")
    if type(message_id) is not int or message_id <= 0:
        failures.append("Positive source_message_id is required")

    published = _receipt_timestamp(receipt.get("published_at"))
    approved = _receipt_timestamp(receipt.get("approval_timestamp"))
    if published is None:
        failures.append("published_at must include an explicit UTC offset")
    elif published > validation_time:
        failures.append("Handoff publication timestamp is in the future")
    if approved is None:
        failures.append("approval_timestamp must include an explicit UTC offset")
    elif approved > validation_time:
        failures.append("Handoff approval timestamp is in the future")
    if not isinstance(receipt.get("approved_by"), str) or not receipt["approved_by"].strip():
        failures.append("approved_by is missing")
    if receipt.get("editorial_signoff") is not True:
        failures.append("Source handoff editorial sign-off is missing")

    as_of = None
    try:
        as_of = date.fromisoformat(receipt.get("as_of", ""))
    except (TypeError, ValueError):
        failures.append("as_of must be an ISO calendar date")
    if as_of is not None and published is not None:
        if as_of > published.date():
            failures.append("as_of is later than published_at")
        try:
            year, month = (int(part) for part in issue.split("-"))
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            issue_end = next_month - timedelta(days=1)
            if as_of > issue_end:
                failures.append("as_of is after the issue month")
        except (TypeError, ValueError):
            failures.append("Issue month is invalid for handoff validation")

    document_ids = receipt.get("source_document_ids", [])
    document_urls = receipt.get("source_document_urls", [])
    if not isinstance(document_ids, list) or not isinstance(document_urls, list):
        failures.append("Source document IDs and URLs must be lists")
        document_ids, document_urls = [], []
    else:
        if any(not isinstance(item, str) or not item.strip() for item in document_ids):
            failures.append("Source document IDs contain an empty or invalid value")
        if len(set(item for item in document_ids if isinstance(item, str))) != len(document_ids):
            failures.append("Source document IDs must be unique")
        for item in document_urls:
            try:
                parsed = urlsplit(item) if isinstance(item, str) else None
                if (parsed is None or parsed.scheme != "https" or not parsed.hostname or
                        parsed.username or parsed.password or parsed.query or parsed.fragment):
                    failures.append("Source document URLs must be safe HTTPS links without credentials, query strings or fragments")
                    break
            except ValueError:
                failures.append("Source document URL is invalid")
                break
        if not document_ids and not document_urls:
            failures.append("At least one source document ID or URL is required")
        if document_ids and document_urls and len(document_ids) != len(document_urls):
            failures.append("Source document ID/URL counts do not match")

    coverage = receipt.get("coverage")
    if not isinstance(coverage, dict):
        failures.append("Coverage counts are missing")
        coverage_summary = None
    else:
        keys = ("universe_size", "verified_count", "missing_count")
        values = [coverage.get(key) for key in keys]
        if any(type(value) is not int or value < 0 for value in values):
            failures.append("Coverage counts must be non-negative integers")
            coverage_summary = None
        else:
            universe, verified, missing = values
            if verified + missing != universe:
                failures.append("Coverage verified_count + missing_count must equal universe_size")
            coverage_summary = {"universe_size": universe,
                                "verified_count": verified,
                                "missing_count": missing}

    data_failures = receipt.get("data_failures")
    if not isinstance(data_failures, list) or any(
            not isinstance(item, str) or not item.strip() for item in data_failures):
        failures.append("data_failures must be a list of non-empty descriptions")
        data_failures = []

    return {
        "passed": not failures,
        "failures": failures,
        "schema_only": True,
        "human_independent_authentication_required": True,
        "partial_coverage": bool(coverage_summary and coverage_summary["missing_count"]) or bool(data_failures),
        "coverage": coverage_summary,
        "data_failures": data_failures,
        "source_document_count": max(len(document_ids), len(document_urls)),
        "source_message_id_present": type(message_id) is int and message_id > 0,
    }


def validate_evidence(pack: dict, issue: str, source_root: Path,
                      as_of: date) -> tuple[dict[int, list[dict]], list[str]]:
    """Require exact official-source bytes and matching excerpt/value.

    This checks consistency with supplied source bytes, NOT the authenticity
    of the source download or human confirmation of a market claim.
    """
    if pack.get("issue") != issue:
        raise MagazineError("Data pack issue differs from magazine issue")
    rows = pack.get("observations", [])
    if not isinstance(rows, list):
        raise MagazineError("Observations must be a list")
    if not rows:
        return {}, ["No financial observations supplied; source data withheld"]
    by_page: dict[int, list[dict]] = {}
    failures = []
    root = source_root.resolve(strict=True)
    for i, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise MagazineError("Invalid observation object")
            page = row.get("page")
            if type(page) is not int or page < 3 or page > 15:
                raise MagazineError("Observations belong on pages 3-15")
            label, unit = row.get("label"), row.get("unit")
            if not isinstance(label, str) or len(label.strip()) < 3 or len(label) > 80:
                raise MagazineError("Missing observation label")
            if not isinstance(unit, str) or not 1 <= len(unit.strip()) <= 35:
                raise MagazineError("Missing observation unit")
            value = _number(row.get("value"))
            on = date.fromisoformat(row.get("observed_on", ""))
            if on > as_of or on.year < as_of.year - 1:
                raise MagazineError("Source observation date outside accepted window")
            url = row.get("source_url")
            if not isinstance(url, str) or not _official_host(url):
                raise MagazineError("Evidence must cite an approved HTTPS official domain")
            name = row.get("source_file")
            if not isinstance(name, str) or not name or Path(name).is_absolute():
                raise MagazineError("Source file must be relative")
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise MagazineError("Source file outside trusted source directory")
            claimed_hash = row.get("source_sha256")
            if not isinstance(claimed_hash, str) or not SHA_RE.fullmatch(claimed_hash):
                raise MagazineError("Missing 64-character source checksum")
            if hashlib.sha256(path.read_bytes()).hexdigest() != claimed_hash:
                raise MagazineError("Source-file checksum mismatch")
            excerpt = row.get("source_excerpt")
            if not isinstance(excerpt, str) or not 8 <= len(excerpt) <= 600:
                raise MagazineError("Source excerpt missing")
            original = " ".join(_source_text(path).split()).casefold()
            excerpt_norm = " ".join(excerpt.split()).casefold()
            if excerpt_norm not in original:
                raise MagazineError("Quoted excerpt not found in source bytes")
            # CSV commas separate fields; they are NOT thousands separators.
            # Use parsed CSV cells for CSV sources, otherwise strictly grouped
            # numeral tokens such as 25,144.30 or 25144.30.
            numeric_pattern = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
            if path.suffix.lower() == ".csv":
                fields = next(csv.reader([excerpt]))
                numeric_tokens = [field.strip() for field in fields
                                  if re.fullmatch(numeric_pattern, field.strip())]
            else:
                numeric_tokens = re.findall(
                    rf"(?<!\w){numeric_pattern}(?!\w)", excerpt)
            if value not in {_number(token) for token in numeric_tokens}:
                raise MagazineError("Claimed value is not in the cited source excerpt")
            by_page.setdefault(page, []).append({
                "label": label.strip(), "value": value, "unit": unit.strip(),
                "observed_on": on.isoformat(), "source_url": url,
                "source_file": name, "source_sha256": claimed_hash,
            })
        except (MagazineError, OSError, ValueError, TypeError, KeyError) as exc:
            failures.append(f"Observation {i}: {exc}")
    # Independent official tables sometimes contradict their commentary.
    # Do not select one of two contradictory claims or silently rank it.
    for page, claims in list(by_page.items()):
        seen = {}
        for row in claims:
            key = (row["label"].casefold(), row["unit"].casefold(), row["observed_on"])
            seen.setdefault(key, set()).add(row["value"])
        conflicts = {key for key, values in seen.items() if len(values) > 1}
        if conflicts:
            failures.append(f"Page {page}: conflicting published numbers for " +
                            ", ".join(sorted(key[0] for key in conflicts)))
            by_page[page] = [row for row in claims if
                             (row["label"].casefold(), row["unit"].casefold(),
                              row["observed_on"]) not in conflicts]
    return by_page, failures


def _rect(c, x, y, w, h, color):
    c.setFillColor(HexColor(color))
    c.rect(x, y, w, h, stroke=0, fill=1)


def _txt(c, x, y, words, size=10, color=NAVY, bold=False):
    c.setFillColor(HexColor(color))
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, str(words))


def _logo(c, x=45, y=777, logo_file: Path | None = None):
    if logo_file is not None and logo_file.is_file():
        c.drawImage(str(logo_file), x, y - 7, width=95, height=40,
                    preserveAspectRatio=True, anchor="c", mask="auto")
        return True
    # Do not pretend a substitute logo is an approved original logo.
    _txt(c, x, y + 13, "MSK AI BOT", 19, NAVY, True)
    _txt(c, x, y - 5, "ORIGINAL LOGO ASSET PENDING", 7, GRAY)
    return False


def _footer(c, number, issue):
    _rect(c, 44, 33, 508, 0.8, TEAL)
    _txt(c, 45, 20, "MSK AI BOT  |  " + issue, 8, GRAY)
    _txt(c, 398, 20, f"{number:02d}/16", 8, GRAY)
    _txt(c, 45, 44, UNAPPROVED, 7.4, TEAL, True)


def _cover(c, issue, logo_file):
    w, h = A4
    _rect(c, 0, 0, w, h, WHITE)
    p = c.beginPath()
    p.moveTo(0, h - 190)
    p.lineTo(w, h - 310)
    p.lineTo(w, 155)
    p.lineTo(0, 260)
    p.close()
    c.setFillColor(HexColor(NAVY))
    c.drawPath(p, fill=1, stroke=0)
    _rect(c, 0, 132, w, 14, TEAL)
    _logo(c, 45, h - 92, logo_file)
    _txt(c, 48, 477, "MONTHLY", 33, WHITE, True)
    _txt(c, 48, 423, "MARKET", 43, WHITE, True)
    _txt(c, 48, 368, "OUTLOOK", 43, WHITE, True)
    _txt(c, 48, 333, "Indian Markets | Macro & Strategy Report", 12, WHITE)
    _txt(c, 48, 283, issue.upper() + "  |  16-PAGE RESEARCH EDITION", 11, "#78CFD0", True)
    # Growth artwork is decorative only; deliberately no invented market values.
    for i in range(8):
        _rect(c, 350 + 25 * i, 301, 13, 25 + 18 * i, "#15566A")
    p = c.beginPath()
    for i, coords in enumerate([(340, 330), (373, 353), (408, 368),
                                (444, 397), (480, 429), (535, 465)]):
        if i == 0:
            p.moveTo(*coords)
        else:
            p.lineTo(*coords)
    c.setStrokeColor(HexColor("#62D5C5"))
    c.setLineWidth(4)
    c.drawPath(p, stroke=1)
    _txt(c, 48, 185, "Markets  |  Economy  |  Sectors  |  Companies  |  Strategy", 11, NAVY)
    _txt(c, 48, 113, "DESIGN 3 / DESIGN C - FIXED MONTHLY MASTER", 9, TEAL, True)


def _index(c, issue, index):
    _txt(c, 46, 741, "EDITOR'S NOTE & INDEX", 22, NAVY, True)
    _txt(c, 46, 714, issue + "  |  Approved page order, editorial draft only", 10, GRAY)
    _rect(c, 46, 683, 498, 3, TEAL)
    _txt(c, 46, 662, "Monthly research is rebuilt with current dated sources.", 10, NAVY)
    _txt(c, 46, 646, "The Design 3 cover, page titles and index do not change.", 9, GRAY)
    for i, row in enumerate(index):
        y = 615 - i * 34
        if i % 2 == 0:
            _rect(c, 46, y - 9, 498, 30, LIGHT)
        _txt(c, 57, y, f"{row['page']:02d}", 11, TEAL, True)
        _txt(c, 94, y, row["title"], 10, NAVY, True)
        _txt(c, 514, y, f"{row['page']:02d}", 9, GRAY)


def _page(c, number, title, issue, layout, observations):
    w, h = A4
    _rect(c, 46, h - 74, 44, 4, TEAL)
    _txt(c, 46, h - 103, title, 19 if len(title) < 31 else 15, NAVY, True)
    _txt(c, 46, h - 125, f"{issue}  |  Design 3 : {layout}", 9, GRAY)
    _rect(c, 46, 494, 496, 174, LIGHT)
    if observations:
        _txt(c, 58, 642, "SOURCE-MATCHED DATA (EDITORIAL REVIEW REQUIRED)", 10, TEAL, True)
        for i, row in enumerate(observations[:5]):
            y = 615 - i * 28
            _txt(c, 58, y, row["label"][:38], 10, NAVY)
            _txt(c, 338, y, f'{row["value"]} {row["unit"]}'[:28], 10, TEAL, True)
    else:
        _txt(c, 58, 631, "VERIFIED CURRENT-MONTH DATA: NOT AVAILABLE", 11, TEAL, True)
        _txt(c, 58, 608, "No market figures, stock ranks or charts have been fabricated.", 9, NAVY)
        _txt(c, 58, 590, "Add original dated source documents for this page.", 9, GRAY)
    # Five varying layout families; visuals must remain neutral without data.
    variant = (number - 3) % 5
    if variant == 0:
        _rect(c, 46, 263, 210, 205, "#EAF1F7")
        _rect(c, 271, 263, 271, 205, "#ECF7F5")
    elif variant == 1:
        _rect(c, 46, 337, 496, 131, "#EAF1F7")
        _rect(c, 46, 263, 496, 59, "#ECF7F5")
    elif variant == 2:
        _rect(c, 46, 263, 496, 205, "#F0F6F5")
        for i in range(3):
            _rect(c, 58 + 161 * i, 285, 149, 154, WHITE)
    elif variant == 3:
        _rect(c, 46, 263, 238, 205, "#ECF7F5")
        _rect(c, 299, 263, 243, 205, "#EAF1F7")
    else:
        for i in range(4):
            _rect(c, 46 + 126 * i, 263, 118, 205, LIGHT if i % 2 else "#EAF1F7")
    _txt(c, 58, 435, "Analysis / chart workspace", 10, NAVY, True)
    _txt(c, 58, 416, "Populate only after source verification.", 8, GRAY)
    _rect(c, 46, 102, 496, 140, LIGHT)
    _txt(c, 58, 215, "MONTHLY INSIGHT / RISK NOTE", 10, NAVY, True)
    _txt(c, 58, 193, "Research and issuer data remain withheld until verified.", 9, GRAY)
    _txt(c, 58, 171, "Current status: DRAFT - human editorial approval required.", 9, TEAL)


def generate(issue: str, output_dir: Path, *, master_path: Path = DEFAULT_MASTER,
             source_pack: Path | None = None, source_root: Path | None = None,
             logo_file: Path | None = None, scheduled: bool = False,
             now: datetime | None = None) -> dict:
    now = now or datetime.now(IST)
    if not MONTH_RE.fullmatch(issue):
        raise MagazineError("Issue must be YYYY-MM")
    if scheduled and (not is_monthly_due(now) or issue != issue_for(now)):
        raise MagazineError("Scheduled creation restricted to first Monday in IST")
    master = load_master(master_path)
    dest = Path(output_dir) / issue
    # A rerun must never replace an earlier edition or its evidence ledger.
    # Check before creating any output; require explicit manual recovery
    # rather than silently overwriting even an incomplete prior attempt.
    if dest.exists():
        raise MagazineError(
            f"Issue directory already exists: {dest}; refusing to overwrite "
            "existing draft, page evidence, or release ledger"
        )
    dest.mkdir(parents=True, exist_ok=False)
    source_receipt_validation = {"passed": False, "receipts_checked": 0,
                                 "failures": ["Source receipt validation not run"]}
    source_handoff_validation = {
        "passed": False, "failures": ["No edition-level handoff manifest supplied"],
        "schema_only": True, "human_independent_authentication_required": True,
    }
    if source_pack is not None:
        if source_root is None:
            raise MagazineError("--source-root required with --source-pack")
        try:
            source = json.loads(Path(source_pack).read_text(encoding="utf-8"))
            source_handoff_validation = validate_handoff_manifest(
                source, issue, validation_time=now)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            by_page, failures = {}, [f"Unreadable or invalid source evidence pack: {type(exc).__name__}"]
        else:
            try:
                by_page, failures = validate_evidence(
                    source, issue, source_root, now.astimezone(IST).date())
                source_receipt_validation = validate_source_receipts(
                    source, by_page, source_root, issue, now.astimezone(IST).date(),
                    validation_time=now)
            except OSError as exc:
                # A missing/unreadable source directory must not suppress the
                # unapproved issue draft. Withhold all observations instead.
                by_page, failures = {}, [
                    f"Source evidence directory unavailable: {type(exc).__name__}"
                ]
                source_receipt_validation = {
                    "passed": False, "receipts_checked": 0,
                    "failures": failures[:],
                }
    else:
        by_page, failures = {}, ["No independently retrieved financial source pack supplied"]
    pdf_file = dest / f"MSK_{issue}_EDITORIAL_DRAFT.pdf"
    c = canvas.Canvas(str(pdf_file), pagesize=A4)
    c.setTitle(f"MSK Monthly Market Outlook {issue} - UNAPPROVED DRAFT")
    pages = []
    index = master["locked_index"]
    for entry in index:
        n, title = entry["page"], entry["title"]
        c.setFillColor(HexColor(WHITE))
        c.rect(0, 0, *A4, fill=1, stroke=0)
        if n == 1:
            _cover(c, issue, logo_file)
        elif n == 2:
            _index(c, issue, index)
        elif n == 16:
            _page(c, n, title, issue, entry["layout"], [])
        else:
            _page(c, n, title, issue, entry["layout"], by_page.get(n, []))
        _footer(c, n, issue)
        c.showPage()
        page_obj = {"page": n, "locked_title": title, "layout": entry["layout"],
                    "status": "PENDING_EDITORIAL_REVIEW", "observations": by_page.get(n, []),
                    "visual_reviewed": False, "financial_reviewed": False,
                    "human_approved": False}
        pages.append(page_obj)
        (dest / f"page_{n:02d}.json").write_text(
            json.dumps(page_obj, indent=2) + "\n", encoding="utf-8")
    c.save()
    qa = audit_pdf(pdf_file, master_path=master_path, issue=issue)
    result = {"issue": issue, "draft_pdf": pdf_file.name,
              "pdf_sha256": hashlib.sha256(pdf_file.read_bytes()).hexdigest(),
              "page_count": 16, "source_consistency_rows": sum(map(len, by_page.values())),
              "source_receipt_validation": source_receipt_validation,
              "source_receipt_structurally_valid": source_receipt_validation["passed"],
              "source_handoff_validation": source_handoff_validation,
              "source_handoff_structurally_valid": source_handoff_validation["passed"],
              "source_authenticity_verified": False,
              "source_provenance_blocker": (
                  "Local source hashes and self-reported receipts do not prove " 
                  "independent retrieval or reviewer identity; independent evidence is required"),
              "source_failures": failures, "logo_asset_provided": bool(logo_file and logo_file.is_file()),
              "mechanical_pdf_checks": qa, "editorial_approved": False,
              "publication_allowed": False, "pages": pages}
    (dest / "release_gate.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not qa["passed"]:
        raise MagazineError("Generated draft failed mechanical PDF QA")
    return result


def audit_pdf(path: Path, *, master_path: Path = DEFAULT_MASTER, issue: str) -> dict:
    master = load_master(master_path)
    pdf = PdfReader(str(path))
    errors = []
    if len(pdf.pages) != 16:
        errors.append("Expected 16 pages")
    for i, (page, entry) in enumerate(zip(pdf.pages, master["locked_index"]), 1):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if abs(w - A4[0]) > 1 or abs(h - A4[1]) > 1:
            errors.append(f"Page {i}: not A4 portrait")
        text = " ".join((page.extract_text() or "").split())
        if entry["title"].casefold() not in text.casefold() and i != 1:
            errors.append(f"Page {i}: changed/missing title")
        if UNAPPROVED not in text:
            errors.append(f"Page {i}: draft-only watermark missing")
        if len(text) < 75:
            errors.append(f"Page {i}: suspiciously blank")
    return {"passed": not errors, "errors": errors, "checks": "16 pages, A4, locked titles, unapproved watermark, text"}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--issue", required=True, help="YYYY-MM")
    p.add_argument("--output-dir", default="magazine_artifacts")
    p.add_argument("--master", default=str(DEFAULT_MASTER))
    p.add_argument("--source-pack")
    p.add_argument("--source-root")
    p.add_argument("--approved-logo")
    p.add_argument("--scheduled", action="store_true",
                   help="Enforce first Monday and current IST issue")
    a = p.parse_args(argv)
    result = generate(
        a.issue, Path(a.output_dir), master_path=Path(a.master),
        source_pack=Path(a.source_pack) if a.source_pack else None,
        source_root=Path(a.source_root) if a.source_root else None,
        logo_file=Path(a.approved_logo) if a.approved_logo else None,
        scheduled=a.scheduled,
    )
    print(json.dumps({"issue": result["issue"], "pdf_sha256": result["pdf_sha256"],
                      "source_consistency_rows": result["source_consistency_rows"],
                      "mechanical_checks": result["mechanical_pdf_checks"],
                      "publication_allowed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
