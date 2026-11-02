"""MSK Monthly Outlook - 16-page corporate magazine PDF layout (DRAFT ONLY).

No data collection, external requests, Telegram, approval, or publishing.
Every factual page must come from independently reviewed, dated source records.
A missing source fails closed. Preview uses unmistakable placeholder material.
"""
import argparse
from datetime import date
from pathlib import Path
import json
import re
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

ISSUE_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
PAGE_TITLES = (
    "Monthly Market Outlook",
    "Editor's Note & Index",
    "Executive Market Outlook",
    "Global & Indian Economy",
    "Nifty 50 Technical Outlook",
    "Bank Nifty & Major Indices",
    "Market Breadth & Institutional Flows",
    "Sector Performance & Rotation",
    "Large-Cap Research",
    "Mid-Cap Research",
    "Small-Cap Research",
    "Fundamental Research Spotlight",
    "Advanced Strategy Lab",
    "Events & Policy Calendar",
    "Next-Month Strategy & Risk",
    "Research Methodology & Disclosures",
)
RESEARCH_PAGES = range(2, 15)  # zero-based pages 3 through 15
INK = "#132B46"
TEAL = "#007D84"
MUTED = "#526579"
GRAY = "#F0F4F7"
NOTICE = (
    "EDUCATIONAL RESEARCH ONLY. Not SEBI-registered investment advice. "
    "This draft is not approved for distribution."
)


class MagazineValidationError(ValueError):
    pass


def _checked_date(value, label):
    try:
        if type(value) is not str:
            raise ValueError()
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError()
        return result
    except (TypeError, ValueError) as exc:
        raise MagazineValidationError("Invalid " + label + " date") from exc


def _validate(data, preview):
    if preview:
        return {"issue": "PREVIEW", "as_of": "NOT VERIFIED", "pages": [{} for _ in PAGE_TITLES]}
    if not isinstance(data, dict):
        raise MagazineValidationError("Source-dated magazine input pack required")
    issue = data.get("issue")
    if not isinstance(issue, str) or not ISSUE_RE.fullmatch(issue):
        raise MagazineValidationError("Magazine issue must be YYYY-MM")
    observed = _checked_date(data.get("as_of"), "magazine as_of")
    if observed > date.today():
        raise MagazineValidationError("Source as_of is future-dated")
    if data.get("approved") is True:
        raise MagazineValidationError("This builder makes drafts only; approval is a separate gate")
    pages = data.get("pages")
    if not isinstance(pages, list) or len(pages) != len(PAGE_TITLES):
        raise MagazineValidationError("Exactly 16 structured page records required")
    for idx in RESEARCH_PAGES:
        page = pages[idx]
        if not isinstance(page, dict):
            raise MagazineValidationError("Invalid structured page")
        summary = page.get("summary")
        if not isinstance(summary, str) or not 30 <= len(summary.strip()) <= 520:
            raise MagazineValidationError(f"Page {idx + 1}: missing bounded summary")
        evidence = page.get("evidence")
        if not isinstance(evidence, list) or not 3 <= len(evidence) <= 4:
            raise MagazineValidationError(f"Page {idx + 1}: require 3-4 referenced facts")
        source_hosts = set()
        for fact in evidence:
            if not isinstance(fact, dict):
                raise MagazineValidationError("Invalid fact")
            statement = fact.get("statement")
            if not isinstance(statement, str) or not 25 <= len(statement.strip()) <= 370:
                raise MagazineValidationError(f"Page {idx + 1}: unsupported or excessive claim")
            link = fact.get("source_url")
            parsed = urlsplit(link) if isinstance(link, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise MagazineValidationError(f"Page {idx + 1}: missing public HTTPS citation")
            host = parsed.hostname.lower().rstrip(".")
            # Editorial citations must resolve to named public publishers, not
            # loopback, private network or reserved documentation endpoints.
            # This is a structural check; it does NOT prove a page's claims.
            from ipaddress import ip_address
            try:
                ip_address(host)
            except ValueError:
                pass
            else:
                raise MagazineValidationError(f"Page {idx + 1}: IP-literal citation forbidden")
            if (host == "localhost" or "." not in host or
                    host.endswith((".localhost", ".invalid", ".test", ".example"))):
                raise MagazineValidationError(f"Page {idx + 1}: non-public citation host")
            source_hosts.add(host.removeprefix("www."))
            claim_date = _checked_date(fact.get("observed_on"), "source observation")
            if claim_date > observed:
                raise MagazineValidationError(f"Page {idx + 1}: source newer than as_of")
        if len(source_hosts) < 2:
            raise MagazineValidationError(f"Page {idx + 1}: two distinct source hosts required")
    return data


def _p(canvas, text, x, top_y, *, width=490, size=12, leading=17, color=INK, max_height=100):
    """Draw bounded and XML-safe paragraphs; reject clipped content."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    style = ParagraphStyle("MSK", fontName="Helvetica", fontSize=size,
                           leading=leading, textColor=HexColor(color),
                           splitLongWords=True)
    item = Paragraph(escape(str(text)).replace("\n", "<br/>"), style)
    _, height = item.wrap(width, max_height)
    if height > max_height:
        raise MagazineValidationError("Paragraph exceeds print-safe region")
    item.drawOn(canvas, x, top_y - height)
    return top_y - height


def _footer(c, page, issue, preview):
    from reportlab.lib.colors import HexColor
    c.setStrokeColor(HexColor(TEAL))
    c.setLineWidth(.8)
    c.line(47, 56, 548, 56)
    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor(MUTED))
    c.drawString(47, 41, "MSK AI BOT | Analysis | AI Intelligence | Market Insights")
    c.drawRightString(548, 41, f"{page:02d}/16 | {issue}")
    c.setFillColor(HexColor(TEAL))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(47, 28, "LAYOUT PREVIEW - NO VERIFIED DATA" if preview else "RESEARCH DRAFT - NOT APPROVED")


def _cover(c, issue, preview):
    from reportlab.lib.colors import HexColor
    w, h = c._pagesize
    c.setFillColor(HexColor("#FFFFFF"))
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(HexColor(INK))
    c.setFont("Helvetica-Bold", 35)
    c.drawString(49, h - 90, "MSK")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(49, h - 111, "AI BOT")
    c.setFont("Helvetica", 9)
    c.setFillColor(HexColor(MUTED))
    c.drawString(49, h - 134, "Analysis | AI Intelligence | Market Insights")
    c.setFillColor(HexColor(INK))
    c.rect(0, 140, w, h - 308, fill=1, stroke=0)
    c.setFillColor(HexColor(TEAL))
    c.rect(49, h - 254, 63, 5, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica", 13)
    c.drawString(49, h - 291, "CORPORATE RESEARCH EDITION")
    c.setFont("Helvetica-Bold", 33)
    c.drawString(49, h - 354, "MONTHLY")
    c.drawString(49, h - 399, "MARKET OUTLOOK")
    c.setFont("Helvetica", 15)
    c.drawString(49, h - 447, f"Issue: {issue}")
    c.setStrokeColor(HexColor("#5FABB1"))
    c.line(49, h - 468, w - 49, h - 468)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(49, h - 503, "ECONOMY   |   EQUITY RESEARCH   |   STRATEGY LAB")
    c.setFillColor(HexColor(TEAL))
    c.rect(49, 176, w - 98, 3, fill=1, stroke=0)
    _p(c, "Institutional-style research for Indian market participants.", 49, 127, size=10, color=MUTED)
    if preview:
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(HexColor("#B42318"))
        c.drawString(49, 93, "DESIGN PREVIEW ONLY - NO INVESTMENT CONTENT VERIFIED")


def _contents(c, issue, preview):
    from reportlab.lib.colors import HexColor
    w, h = c._pagesize
    c.setFillColor(HexColor(INK))
    c.setFont("Helvetica-Bold", 25)
    c.drawString(49, h - 103, "Editor's Note & Index")
    c.setFont("Helvetica", 11)
    c.setFillColor(HexColor(MUTED))
    c.drawString(49, h - 133, f"MSK Monthly Research | {issue}")
    y = h - 181
    for page, title in enumerate(PAGE_TITLES, 1):
        if page == 2:
            continue
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(HexColor(TEAL))
        c.drawString(49, y, f"{page:02d}")
        c.setFillColor(HexColor(INK))
        c.setFont("Helvetica", 10)
        c.drawString(85, y, title)
        y -= 33
    _p(c, "Editorial note: Every statement and market observation requires source review. "
          "This document is a layout draft until the data and publication checks are complete.",
       49, 137, size=9, leading=13, color=MUTED)


def _research(c, title, number, issue, as_of, page, preview):
    from reportlab.lib.colors import HexColor
    w, h = c._pagesize
    c.setFillColor(HexColor(TEAL))
    c.rect(49, h - 71, 45, 4, stroke=0, fill=1)
    c.setFillColor(HexColor(MUTED))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(49, h - 93, f"MSK MONTHLY OUTLOOK  /  {issue}")
    c.setFillColor(HexColor(INK))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(49, h - 124, title)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(HexColor(MUTED))
    c.drawString(49, h - 146, f"Source observation cutoff: {as_of}")
    summary = ("This is an unpopulated design preview. No real-world prices, "
               "company ratings, sector rankings or investment recommendations are shown."
               if preview else page["summary"])
    _p(c, summary, 49, h - 182, size=11, leading=16, max_height=75)

    c.setFillColor(HexColor(GRAY))
    c.roundRect(49, h - 359, w - 98, 95, radius=6, fill=1, stroke=0)
    c.setFillColor(HexColor(TEAL))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(64, h - 286, "EVIDENCE-LED RESEARCH")
    _p(c, "Chart/table slot: reserved for an independently verified, "
          "source-dated visualization. A placeholder is not investment evidence.",
       64, h - 308, size=9, leading=14, max_height=45, width=w - 128)
    c.setFillColor(HexColor(INK))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(49, h - 392, "SOURCE NOTES / RESEARCH ASSERTIONS")
    evidence = ([
        {"statement":"Verified data has not yet been supplied. Content is intentionally withheld.",
         "source_url":"https://example.invalid/preview", "observed_on":"UNVERIFIED"}
    ] if preview else page["evidence"])
    y = h - 420
    for index, fact in enumerate(evidence, 1):
        height = 70 if len(evidence) == 4 else 90
        c.setFillColor(HexColor("#FAFCFD"))
        c.roundRect(49, y - height + 13, w - 98, height - 5, radius=4, fill=1, stroke=0)
        c.setFillColor(HexColor(TEAL))
        c.setFont("Helvetica-Bold", 9)
        c.drawString(60, y - 3, f"{index:02d}")
        _p(c, fact["statement"], 86, y + 1, width=w - 158, size=9.5,
           leading=13, max_height=39 if len(evidence) == 4 else 54)
        source = urlsplit(fact["source_url"]).hostname or "unverified"
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor(MUTED))
        c.drawString(86, y - height + 24, f"Source: {source}  |  Observed: {fact['observed_on']}")
        y -= height + 3
    if y < 95:
        raise MagazineValidationError("Page source notes exceed available space")


def _methodology(c, issue, preview):
    from reportlab.lib.colors import HexColor
    w, h = c._pagesize
    c.setFillColor(HexColor(INK))
    c.setFont("Helvetica-Bold", 22)
    c.drawString(49, h - 103, "Methodology & Disclosures")
    sections = (
        ("Data provenance", "Every numeric market statement must carry a traceable source, "
         "observation session and methodological definition. Missing financial coverage "
         "is disclosed, not filled with fabricated estimates."),
        ("Technical analysis", "Indicators describe historical observations, not guaranteed "
         "future direction. No strategy win-rate, volume delta or options Greek is published "
         "without a reproducible data method and realistic transaction-cost assumptions."),
        ("Editorial and release approval", "An independent human reviewer must inspect all "
         "16 pages, check chart legends and citations, and approve the final real PDF artifact. "
         "A green CI run alone is not publication approval."),
        ("Risk disclosure", NOTICE),
    )
    y = h - 148
    for title, body in sections:
        c.setFillColor(HexColor(TEAL))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(49, y, title)
        y = _p(c, body, 49, y - 16, width=490, size=10, leading=16, max_height=120) - 29
    c.setFillColor(HexColor("#F0F4F7"))
    c.roundRect(49, 144, w - 98, 63, 4, fill=1, stroke=0)
    _p(c, "Publication status: NOT APPROVED. This file does not represent a "
          "financial advisory, source verification or completed magazine release.",
       60, 194, width=w - 120, size=10, leading=15, max_height=45)


def render_magazine(data, output_file, *, preview=False):
    """Create a 16-page PDF draft and return path/status; never send or approve it."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    pack = _validate(data, preview)
    path = Path(output_file)
    if path.suffix.lower() != ".pdf":
        raise MagazineValidationError("Output must be a PDF file")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reportlab writes straight to disk. Use a temporary file and replace only on success.
    tmp = path.with_name(path.name + ".tmp")
    try:
        c = canvas.Canvas(str(tmp), pagesize=A4, pageCompression=0)
        c.setTitle("MSK Monthly Market Outlook - UNAPPROVED " + pack["issue"])
        for idx, title in enumerate(PAGE_TITLES):
            c.setFillColor(HexColor("#FFFFFF"))
            c.rect(0, 0, *A4, fill=1, stroke=0)
            if idx == 0:
                _cover(c, pack["issue"], preview)
            elif idx == 1:
                _contents(c, pack["issue"], preview)
            elif idx == 15:
                _methodology(c, pack["issue"], preview)
            else:
                _research(c, title, idx + 1, pack["issue"], pack["as_of"],
                          pack["pages"][idx], preview)
            _footer(c, idx + 1, pack["issue"], preview)
            c.showPage()
        c.save()
        raw = tmp.read_bytes()
        if not raw.startswith(b"%PDF-") or len(re.findall(rb"/Type\s*/Page\b", raw)) != 16:
            raise MagazineValidationError("Generated file failed exact 16-page integrity check")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"path": str(path), "pages": 16,
            "status": "LAYOUT_PREVIEW" if preview else "UNAPPROVED_RESEARCH_DRAFT"}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--input-json", help="Reviewed source data pack in private workspace")
    p.add_argument("--preview", action="store_true",
                   help="Explicit unpopulated design preview without market data")
    args = p.parse_args(argv)
    if args.preview == bool(args.input_json):
        p.error("Specify either --preview or --input-json, not both")
    data = None if args.preview else json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    print(json.dumps(render_magazine(data, args.output, preview=args.preview)))


if __name__ == "__main__":
    main()
