"""MSK monthly magazine: fail-closed draft generator and mechanical PDF QA.

Only reads owner-approved Design 3 index and optional source evidence.
NEVER calls Telegram, places trades, approves, or publishes an edition.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
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
from reportlab.lib.utils import simpleSplit

if __package__:
    from .msk_magazine_editorial import load_editorial_pack
    from .msk_magazine_charts import draw_research_chart, load_metrics
    from .msk_magazine_cover_story import select_cover_story
    from .msk_magazine_premium import draw_cover as premium_cover, draw_index as premium_index, draw_article as premium_article
    from .msk_magazine_source_archive import verify_nse_dashboard, SourceEvidenceError
    from .msk_magazine_primary_sources import recheck_primary_sources
else:
    from msk_magazine_editorial import load_editorial_pack
    from msk_magazine_charts import draw_research_chart, load_metrics
    from msk_magazine_cover_story import select_cover_story
    from msk_magazine_premium import draw_cover as premium_cover, draw_index as premium_index, draw_article as premium_article
    from msk_magazine_source_archive import verify_nse_dashboard, SourceEvidenceError
    from msk_magazine_primary_sources import recheck_primary_sources
from reportlab.pdfgen import canvas

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MASTER = Path(__file__).resolve().parents[1] / "docs/msk_magazine/LOCKED_DESIGN3_INDEX_MASTER.json"
RECREATED_LOGO = Path(__file__).resolve().parents[1] / "assets/msk_ai_bot_logo_recreated.png"
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


def _wrapped(c, x, y, value, *, size=9, max_width=465,
             max_lines=3, leading=13, color=NAVY, bold=False):
    """Draw all lines or fail instead of silently clipping editorial text."""
    font = "Helvetica-Bold" if bold else "Helvetica"
    lines = simpleSplit(str(value), font, size, max_width)
    if len(lines) > max_lines:
        raise MagazineError(
            f"Editorial text overflow: {len(lines)} lines exceed {max_lines}"
        )
    for i, line in enumerate(lines):
        _txt(c, x, y - i * leading, line, size, color, bold)


def _logo(c, x=45, y=777, logo_file: Path | None = None):
    if logo_file is not None and logo_file.is_file():
        c.drawImage(str(logo_file), x, y - 7, width=177, height=59,
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


def _cover(c, issue, logo_file, market_data=None, cover_story=None):
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
    if cover_story:
        # A monthly magazine leads with one verified newsworthy market event,
        # rather than using "Monthly Market Outlook" or a Diwali festival
        # title as the cover headline every month.
        _txt(c, 48, 497, "THE MSK MONTHLY MARKET STORY", 10, "#78CFD0", True)
        _txt(c, 48, 467, cover_story["kicker"], 14, "#78CFD0", True)
        headline = cover_story["headline"]
        font_size = 39
        lines = simpleSplit(headline, "Helvetica-Bold", font_size, 498)
        if not 1 <= len(lines) <= 3:
            raise MagazineError("Issue cover headline does not fit the approved editorial area")
        for i, line in enumerate(lines):
            _txt(c, 48, 405 - 51 * i, line, font_size, WHITE, True)
        deck_y = 405 - 51 * len(lines) - 9
        _wrapped(c, 48, deck_y, cover_story["deck"], size=9.6,
                 max_width=493, max_lines=2, leading=15,
                 color=WHITE)
        year, month = issue.split("-")
        _txt(c, 48, 222, date(int(year), int(month), 1).strftime("%B %Y").upper()
             + "  |  16-PAGE RESEARCH EDITION", 11, NAVY, True)
        _txt(c, 48, 186, "SOURCE: NSE INDICES | OBSERVED " +
             cover_story["source_date"], 9, TEAL, True)
        _txt(c, 48, 166, "READ THE LEAD STORY + MARKET, ECONOMY & STRATEGY", 8.4, NAVY)
        c.linkURL(cover_story["source_url"], (47, 177, 545, 203), relative=0)
    else:
        # Fail-soft for unverified issues: do not fabricate a gold plunge,
        # index lifetime high, festival story or any other market event.
        _txt(c, 48, 477, "MONTHLY", 33, WHITE, True)
        _txt(c, 48, 423, "MARKET", 43, WHITE, True)
        _txt(c, 48, 368, "OUTLOOK", 43, WHITE, True)
        _txt(c, 48, 333, "Indian Markets | Macro & Strategy Report", 12, WHITE)
        _txt(c, 48, 283, issue.upper() + "  |  DATA VERIFICATION PENDING", 11,
             "#78CFD0", True)
        _txt(c, 48, 185, "Markets | Economy | Sectors | Companies | Strategy", 11, NAVY)
    _txt(c, 48, 113, "MSK AI BOT | MONTHLY FINANCIAL RESEARCH MAGAZINE",
         9, TEAL, True)


def _index(c, issue, index, market_data=None, cover_story=None):
    _txt(c, 46, 741, "EDITOR'S NOTE & INDEX", 22, NAVY, True)
    _txt(c, 46, 714, issue + "  |  Approved page order, editorial draft only", 10, GRAY)
    _rect(c, 46, 683, 498, 3, TEAL)
    if cover_story:
        _wrapped(c, 46, 662, "THIS ISSUE | " + cover_story["headline"],
                 size=10, max_width=490, max_lines=1, color=NAVY, bold=True)
        _wrapped(c, 46, 646, cover_story["deck"], size=8.8,
                 max_width=490, max_lines=2, leading=13, color=GRAY)
    else:
        _txt(c, 46, 662, "Monthly research is rebuilt with current dated sources.", 10, NAVY)
        _txt(c, 46, 646, "The cover remains generic until the lead story is verified.", 9, GRAY)
    for i, row in enumerate(index):
        y = 615 - i * 34
        if i % 2 == 0:
            _rect(c, 46, y - 9, 498, 30, LIGHT)
        _txt(c, 57, y, f"{row['page']:02d}", 11, TEAL, True)
        _txt(c, 94, y, row["title"], 10, NAVY, True)
        _txt(c, 514, y, f"{row['page']:02d}", 9, GRAY)


def _page(c, number, title, issue, layout, observations, editorial=None, market_data=None):
    w, h = A4
    _rect(c, 46, h - 74, 44, 4, TEAL)
    _txt(c, 46, h - 103, title, 19 if len(title) < 31 else 15, NAVY, True)
    _txt(c, 46, h - 125, f"{issue}  |  Design 3 : {layout}", 9, GRAY)
    _rect(c, 46, 494, 496, 174, LIGHT)
    if market_data:
        draw_research_chart(c, number, market_data)
    elif observations:
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
    # The five master layout families remain visible around the article;
    # a continuous opaque reading surface prevents body text crossing
    # alternating colour blocks (caught during real PDF visual inspection).
    if editorial:
        _rect(c, 55, 273, 478, 185, WHITE)
        _rect(c, 55, 273, 4, 185, TEAL)
        _rect(c, 63, 275, 468, 2, "#D8E6EB")
    _rect(c, 46, 102, 496, 140, LIGHT)
    if editorial:
        _wrapped(c, 58, 445, editorial["headline"], size=11,
                 max_width=463, max_lines=2, bold=True)
        _wrapped(c, 58, 410, editorial["lead"], size=9,
                 max_width=463, max_lines=4, leading=13)
        _wrapped(c, 58, 352, "01  " + editorial["analysis"][0], size=8.4,
                 max_width=463, max_lines=3, leading=13)
        _wrapped(c, 58, 306, "02  " + editorial["analysis"][1], size=8.4,
                 max_width=463, max_lines=3, leading=13)
        _txt(c, 58, 215, "RISK / EVIDENCE LIMITATION", 10, NAVY, True)
        _wrapped(c, 58, 193, editorial["risk_note"], size=8.5,
                 max_width=460, max_lines=3, leading=13)
        for i, src in enumerate(editorial["sources"]):
            yy = 146 - i * 13
            caption = f'[{i+1}] {urlsplit(src["url"]).hostname} | {src["published_on"]}'
            _txt(c, 58, yy, caption, 7.2, TEAL)
            c.linkURL(src["url"], (57, yy-2, 504, yy+9), relative=0)
        _txt(c, 58, 109, "Click source links; original bytes still require acceptance.", 7.5, GRAY)
    else:
        _txt(c, 58, 435, "Analysis / chart workspace", 10, NAVY, True)
        _txt(c, 58, 416, "Populate only after source verification.", 8, GRAY)
        _txt(c, 58, 215, "MONTHLY INSIGHT / RISK NOTE", 10, NAVY, True)
        _txt(c, 58, 193, "Research and issuer data remain withheld until verified.", 9, GRAY)
        _txt(c, 58, 171, "Current status: DRAFT - human editorial approval required.", 9, TEAL)


def generate(issue: str, output_dir: Path, *, master_path: Path = DEFAULT_MASTER,
             source_pack: Path | None = None, source_root: Path | None = None,
             logo_file: Path | None = None, scheduled: bool = False,
             now: datetime | None = None,
             editorial_pack: Path | None = None,
             market_pack: Path | None = None,
             nse_proof_dir: Path | None = None,
             primary_proof_dir: Path | None = None) -> dict:
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
    editorial = (load_editorial_pack(editorial_pack, issue,
                                   as_of=now.astimezone(IST).date())
                 if editorial_pack is not None else {})
    # Use the user's clean recreated MSK logo in authored previews, without
    # falsely claiming that a recovered original asset was authenticated.
    rendered_logo = logo_file
    logo_source = "EXPLICIT_LOGO_FILE" if logo_file is not None else "NONE"
    if rendered_logo is None and editorial and RECREATED_LOGO.is_file():
        rendered_logo = RECREATED_LOGO
        logo_source = "RECREATED_FROM_USER_REFERENCE_NOT_FINAL_APPROVED"
    market_data = load_metrics(market_pack, issue) if market_pack is not None else None
    cover_story = select_cover_story(issue, market_data)
    if market_data and date.fromisoformat(market_data["as_of"]) > now.astimezone(IST).date():
        raise MagazineError("Future-dated market research is prohibited")
    # Reconcile the original downloaded PDF again at assembly time. Neither
    # a JSON label claiming 'verified' nor an attached SHA string is enough.
    nse_verified = None
    if nse_proof_dir is not None:
        if market_data is None:
            raise MagazineError("NSE proof requires the matching market pack")
        proof_dir = Path(nse_proof_dir)
        source_pdf = proof_dir / "Index_Dashboard_OCT2026.pdf"
        receipt_path = proof_dir / "nse_dashboard_reconciliation.json"
        if not source_pdf.is_file() or not receipt_path.is_file():
            raise MagazineError("Original NSE PDF or reconciliation receipt missing")
        if source_pdf.is_symlink() or receipt_path.is_symlink():
            raise MagazineError("Symlinked source proof rejected")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        nse_verified = verify_nse_dashboard(source_pdf.read_bytes(), market_data)
        if (receipt.get("sha256") != nse_verified["sha256"] or
                receipt.get("checked_rows") != nse_verified["checked_rows"]):
            raise MagazineError("NSE source receipt does not match independently checked PDF")
    primary_verified = (recheck_primary_sources(primary_proof_dir)
                        if primary_proof_dir is not None else None)
    if scheduled and market_data and (nse_verified is None or primary_verified is None):
        raise MagazineError("Scheduled market claims require NSE and primary-source receipts")
    if source_pack is not None:
        if source_root is None:
            raise MagazineError("--source-root required with --source-pack")
        source = json.loads(Path(source_pack).read_text(encoding="utf-8"))
        by_page, failures = validate_evidence(source, issue, source_root, now.astimezone(IST).date())
    else:
        by_page, failures = {}, ([] if nse_verified else
                                ["No independently retrieved financial source pack supplied"])
    dest.mkdir(parents=True, exist_ok=False)
    pdf_file = dest / f"MSK_{issue}_EDITORIAL_DRAFT.pdf"
    c = canvas.Canvas(str(pdf_file), pagesize=A4)
    c.setTitle(f"MSK Monthly Market Outlook {issue} - UNAPPROVED DRAFT")
    pages = []
    index = master["locked_index"]
    for entry in index:
        n, title = entry["page"], entry["title"]
        c.setFillColor(HexColor(WHITE))
        c.rect(0, 0, *A4, fill=1, stroke=0)
        if editorial and market_data:
            # One premium A4 print path for fully authored, source-backed
            # issues. Sparse drafts keep the conservative fallback below.
            if n == 1:
                premium_cover(c, issue, rendered_logo, _logo, cover_story)
            elif n == 2:
                premium_index(c, issue, index, cover_story)
            else:
                premium_article(c, n, title, editorial[n],
                                lambda ctx: draw_research_chart(ctx, n, market_data))
        elif n == 1:
            _cover(c, issue, rendered_logo, market_data, cover_story)
        elif n == 2:
            _index(c, issue, index, market_data, cover_story)
        elif n == 16:
            _page(c, n, title, issue, entry["layout"], [], editorial.get(n),
                  market_data)
        else:
            _page(c, n, title, issue, entry["layout"], by_page.get(n, []),
                  editorial.get(n), market_data)
        _footer(c, n, issue)
        c.showPage()
        page_obj = {"page": n, "locked_title": title, "layout": entry["layout"],
                    "status": "PENDING_EDITORIAL_REVIEW", "observations": by_page.get(n, []),
                    "visual_reviewed": False, "financial_reviewed": False,
                    "human_approved": False,
                    "editorial_content_present": n in editorial,
                    "editorial_sources": editorial.get(n, {}).get("sources", [])}
        pages.append(page_obj)
        (dest / f"page_{n:02d}.json").write_text(
            json.dumps(page_obj, indent=2) + "\n", encoding="utf-8")
    c.save()
    qa = audit_pdf(pdf_file, master_path=master_path, issue=issue)
    if cover_story:
        cover_page = PdfReader(str(pdf_file)).pages[0]
        cover_text = " ".join((cover_page.extract_text() or "").split())
        if cover_story["headline"] not in cover_text:
            qa["errors"].append("Cover: selected lead story headline missing")
        urls = set()
        for annot in cover_page.get("/Annots", []):
            action = annot.get_object().get("/A")
            if action and action.get("/URI"):
                urls.add(str(action["/URI"]))
        if cover_story["source_url"] not in urls:
            qa["errors"].append("Cover: source hyperlink missing")
        qa["passed"] = not qa["errors"]
    if editorial:
        reader = PdfReader(str(pdf_file))
        for page_num, content in sorted(editorial.items()):
            page_obj = reader.pages[page_num - 1]
            extracted = " ".join((page_obj.extract_text() or "").split())
            if content["headline"] not in extracted:
                qa["errors"].append(f"Page {page_num}: authored headline missing in PDF")
            linked = set()
            for annotation in page_obj.get("/Annots", []):
                action = annotation.get_object().get("/A")
                if action and action.get("/URI"):
                    linked.add(str(action["/URI"]))
            for source in content["sources"]:
                if source["url"] not in linked:
                    qa["errors"].append(f"Page {page_num}: missing source hyperlink")
        qa["passed"] = not qa["errors"]
    result = {"issue": issue, "draft_pdf": pdf_file.name,
              "pdf_sha256": hashlib.sha256(pdf_file.read_bytes()).hexdigest(),
              "page_count": 16, "source_consistency_rows": sum(map(len, by_page.values())),
              "editorial_pages_authored": len(editorial),
              "visual_engine": "premium_a4_editorial_v1" if editorial and market_data else "conservative_draft",
              "cover_story": cover_story,
              "cover_story_publication_approved": False,
              "chart_pages_with_dated_evidence": 14 if market_data else 0,
              "nse_original_pdf_reconciled": nse_verified is not None,
              "nse_reconciled_named_rows": nse_verified["checked_rows"] if nse_verified else 0,
              "nse_original_pdf_sha256": nse_verified["sha256"] if nse_verified else None,
              "macro_issuer_original_sources_verified": primary_verified is not None,
              "macro_issuer_source_files_rechecked": primary_verified["sources_checked"] if primary_verified else 0,
              "market_reference_url": market_data["market_source"]["url"] if market_data else None,
              "market_reference_date": market_data["market_source"]["report_date"] if market_data else None,
              "source_failures": failures, "logo_asset_provided": bool(rendered_logo and rendered_logo.is_file()),
              "logo_source": logo_source,
              "original_logo_authenticated": False,
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
    p.add_argument("--editorial-pack", help="Complete 03-16 dated editorial JSON; never publication approval")
    p.add_argument("--market-pack", help="Dated official-market research evidence for real charts")
    p.add_argument("--nse-proof-dir", help="Original downloaded NSE PDF and independently reconciled receipt")
    p.add_argument("--primary-proof-dir", help="Original RBI, MoSPI and issuer HTML files and receipts")
    p.add_argument("--scheduled", action="store_true",
                   help="Enforce first Monday and current IST issue")
    a = p.parse_args(argv)
    result = generate(
        a.issue, Path(a.output_dir), master_path=Path(a.master),
        source_pack=Path(a.source_pack) if a.source_pack else None,
        source_root=Path(a.source_root) if a.source_root else None,
        logo_file=Path(a.approved_logo) if a.approved_logo else None,
        editorial_pack=Path(a.editorial_pack) if a.editorial_pack else None,
        market_pack=Path(a.market_pack) if a.market_pack else None,
        nse_proof_dir=Path(a.nse_proof_dir) if a.nse_proof_dir else None,
        primary_proof_dir=Path(a.primary_proof_dir) if a.primary_proof_dir else None,
        scheduled=a.scheduled,
    )
    print(json.dumps({"issue": result["issue"], "pdf_sha256": result["pdf_sha256"],
                      "source_consistency_rows": result["source_consistency_rows"],
                      "mechanical_checks": result["mechanical_pdf_checks"],
                      "publication_allowed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
