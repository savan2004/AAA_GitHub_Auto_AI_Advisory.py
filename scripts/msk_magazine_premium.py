"""Premium A4 print layouts for MSK's source-verified editorial magazine.

An intentionally small ReportLab layout layer: no extra rendering engine,
no AGPL deck-template code, no guessed prices or synthetic chart data.
Each editorial family is independently laid out around the existing dated
vector chart and preserves source hyperlinks and publication watermarks.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit

PAPER = "#FAFBF8"
INK = "#102A3C"
TEAL = "#047A7D"
GOLD = "#C5A66A"
SLATE = "#516879"
LIGHT = "#E9F2F1"
RULE = "#D4E1E1"
RISK = "#A15348"
WHITE = "#FFFFFF"

FAMILIES = {
    3: "opening", 4: "column", 5: "analysis", 6: "split",
    7: "analysis", 8: "opening", 9: "split", 10: "column",
    11: "analysis", 12: "opening", 13: "split", 14: "column",
    15: "analysis", 16: "closing",
}


class PremiumLayoutError(ValueError):
    """A PDF element did not fit; never silently clip financial prose."""


def _box(c, x, y, w, h, color):
    c.setFillColor(HexColor(color))
    c.rect(x, y, w, h, stroke=0, fill=1)


def _line(c, x1, y1, x2, y2, color=RULE, width=0.65):
    c.setStrokeColor(HexColor(color))
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


def _text(c, x, y, value, size=10, color=INK, font="Helvetica"):
    c.setFillColor(HexColor(color))
    c.setFont(font, size)
    c.drawString(x, y, str(value))


def _fit(c, x, y, value, *, width, lines=4, size=10.0, min_size=8.65,
         leading=14.0, font="Helvetica", color=INK):
    value = str(value)
    at = size
    while at + 0.01 >= min_size:
        rows = simpleSplit(value, font, at, width)
        if len(rows) <= lines:
            for i, row in enumerate(rows):
                _text(c, x, y - i * leading, row, at, color, font)
            return len(rows)
        at = round(at - .25, 2)
    raise PremiumLayoutError("Magazine text cannot fit its print column: " + value[:80])


def draw_cover(c, issue, logo_file, logo_draw, cover_story=None):
    """Newsstand-style cover: editorial headline, restrained art, no fake chart."""
    w, h = A4
    _box(c, 0, 0, w, h, INK)
    # Deliberate geometric masthead art (not a data plot).
    c.setStrokeColor(HexColor("#305267"))
    for n in range(5):
        c.setLineWidth(.6)
        c.circle(542, 440, 135 + n * 42, fill=0, stroke=1)
    _box(c, 0, 0, w, 236, PAPER)
    _box(c, 45, 729, 506, 86, WHITE)
    logo_draw(c, 57, 747, logo_file)
    _box(c, 47, 698, 67, 4, GOLD)
    _text(c, 48, 678, "MSK  /  THE MARKET REVIEW", 10.1, "#E4C991",
          "Helvetica-Bold")
    year, month = (int(x) for x in issue.split("-"))
    month_label = date(year, month, 1).strftime("%B %Y").upper()
    _text(c, 48, 650, month_label + "  |  16-PAGE RESEARCH EDITION",
          9.4, "#DAE6E9", "Helvetica-Bold")
    if cover_story:
        _text(c, 48, 590, cover_story["kicker"], 12.5, "#E4C991",
              "Helvetica-Bold")
        headline = cover_story["headline"]
        # The page title must be legible on A4 and present verbatim in text.
        at = 45
        rows = simpleSplit(headline, "Times-Bold", at, 493)
        while len(rows) > 3 and at >= 35:
            at -= 1
            rows = simpleSplit(headline, "Times-Bold", at, 493)
        if len(rows) > 3:
            raise PremiumLayoutError("Feature headline too long for cover")
        for i, row in enumerate(rows):
            _text(c, 48, 520 - i * 57, row, at, WHITE, "Times-Bold")
        deck_y = 520 - 57 * len(rows) - 2
        _fit(c, 48, deck_y, cover_story["deck"], width=464,
             size=12.0, min_size=10.4, lines=3, leading=17,
             color="#E4F0F0")
        _text(c, 48, 259, "PRIMARY SOURCE  /  " +
              cover_story["source_date"], 8.2, "#DAE6E9",
              "Helvetica-Bold")
        c.linkURL(cover_story["source_url"], (46, 249, 545, 279), relative=0)
    else:
        _text(c, 48, 579, "MONTHLY MARKET", 43, WHITE, "Times-Bold")
        _text(c, 48, 520, "OUTLOOK", 52, WHITE, "Times-Bold")
        _fit(c, 48, 434, "Independent monthly research, pending dated "
             "primary-source evidence.", width=480, size=13,
             lines=3, color="#E4F0F0")
    _text(c, 48, 199, "INSIDE: NIFTY, SECTORS & MARKET RISKS", 11, TEAL, "Helvetica-Bold")
    _line(c, 48, 187, 547, 187, GOLD, 1.1)
    teasers = [
        ("01", "MARKETS", "Nifty, Bank Nifty and other indices"),
        ("02", "COMPANIES", "Profits, valuations and company filings"),
        ("03", "YOUR RISKS", "RBI, inflation and next-month watchpoints"),
    ]
    for i, (no, label, note) in enumerate(teasers):
        x = 48 + 171 * i
        if i:
            _line(c, x - 12, 97, x - 12, 174)
        _text(c, x, 163, no, 12.2, GOLD, "Times-Bold")
        _text(c, x, 142, label, 10.4, INK, "Helvetica-Bold")
        _fit(c, x, 123, note, width=144, lines=3,
             size=8.6, min_size=8.3, leading=11.0, color=SLATE)
    _text(c, 48, 61, "EVIDENCE-LED FINANCIAL RESEARCH  /  PRIVATE REVIEW COPY",
          8.3, TEAL, "Helvetica-Bold")


def draw_index(c, issue, index, cover_story=None):
    """Actual two-column editorial contents, instead of spreadsheet rows."""
    w, h = A4
    _box(c, 0, 0, w, h, PAPER)
    _box(c, 0, h - 26, w, 26, INK)
    _text(c, 48, 772, "THE CONTENTS", 11, TEAL, "Helvetica-Bold")
    _text(c, 48, 729, "Editor's Note & Index", 30, INK, "Times-Bold")
    _line(c, 48, 708, 545, 708, GOLD, 1.4)
    if cover_story:
        _text(c, 49, 685, "THIS MONTH'S COVER STORY", 8.7, TEAL,
              "Helvetica-Bold")
        _fit(c, 49, 664, cover_story["headline"], width=489,
             size=18, min_size=16, lines=2, leading=23, font="Times-Bold")
    else:
        _text(c, 49, 664, "A dated, source-led view of the Indian market.",
              13, INK, "Times-Roman")
    for item in index:
        i = int(item["page"]) - 1
        col = 0 if i < 8 else 1
        n = i if i < 8 else i - 8
        x = 48 if col == 0 else 308
        y = 587 - n * 48
        _line(c, x, y + 14, x + 237, y + 14, RULE)
        _text(c, x, y - 5, f'{item["page"]:02d}', 13.0, TEAL, "Times-Bold")
        _fit(c, x + 38, y - 4, item["title"], width=195,
             size=9.3, min_size=8.8, lines=2, leading=11.8,
             font="Helvetica-Bold")
    _box(c, 48, 94, 497, 92, LIGHT)
    _box(c, 48, 94, 4, 92, TEAL)
    _text(c, 64, 163, "YOUR QUICK GUIDE TO MARKET TERMS", 9.7, TEAL,
          "Helvetica-Bold")
    _fit(c, 64, 142, "P/E = price compared with earnings. "
         "CPI = consumer inflation. "
         "TRI = index return including reinvested dividends. "
         "25 bps = 0.25 percentage points. "
         "Returns shown are not buy or sell advice.",
         width=466, size=9.1, lines=4, leading=14)


def _article_head(c, number, section, article):
    w, h = A4
    _box(c, 0, 0, w, h, PAPER)
    _box(c, 0, h - 18, w, 18, INK)
    _text(c, 48, 797, "THE MSK REVIEW  /  RESEARCH", 8.6,
          TEAL, "Helvetica-Bold")
    _text(c, 492, 797, f"{number:02d} / 16", 8.5, SLATE,
          "Helvetica-Bold")
    _line(c, 48, 786, 546, 786, GOLD, 1)
    # Retain the locked index title literally for PDF structural audit.
    _text(c, 48, 770, section.upper(), 9.5, SLATE,
          "Helvetica-Bold")
    _fit(c, 48, 741, article["headline"], width=496, size=26,
         min_size=22, lines=2, leading=29, font="Times-Bold")
    _line(c, 48, 677, 546, 677, RULE)


def _lead(c, article, *, feature=False):
    if feature:
        _box(c, 48, 392, 498, 90, LIGHT)
        _box(c, 48, 392, 4, 90, TEAL)
        _text(c, 65, 461, "WHAT THE NUMBERS SHOW", 8.5, TEAL,
              "Helvetica-Bold")
        _fit(c, 65, 443, article["lead"], width=455, size=10.2,
             min_size=9.2, lines=4, leading=14.3)
    else:
        _text(c, 49, 469, "WHAT HAPPENED", 8.7, TEAL, "Helvetica-Bold")
        _fit(c, 49, 450, article["lead"], width=494, size=10.4,
             min_size=9.1, lines=4, leading=14.0)


def _analysis(c, article, family):
    parts = article["analysis"]
    if len(parts) != 2:
        raise PremiumLayoutError("Exactly two sourced editorial analysis points required")
    if family in ("column", "opening", "split"):
        if family == "split":
            _box(c, 48, 273, 246, 106, "#F1F5F2")
            _box(c, 301, 273, 244, 106, WHITE)
            positions = [(62, 354), (314, 354)]
            widths = [216, 214]
        else:
            positions = [(49, 351), (304, 351)]
            widths = [233, 231]
            _line(c, 292, 273, 292, 371, RULE)
            if family == "opening":
                _line(c, 48, 384, 546, 384, GOLD, 1)
        for i, ((x, y), width) in enumerate(zip(positions, widths), 1):
            _text(c, x, y, f"0{i}  /  WHY IT MATTERS", 8.3, TEAL,
                  "Helvetica-Bold")
            _fit(c, x, y - 20, parts[i-1], width=width, lines=6,
                 size=9.45, min_size=8.8, leading=13.4)
    else:
        _line(c, 48, 378, 546, 378, GOLD, 1)
        for i, row in enumerate(parts):
            y = 353 - i * 67
            _text(c, 49, y, f"0{i+1}", 18, TEAL, "Times-Bold")
            _fit(c, 94, y + 1, row, width=446, lines=3,
                 size=9.5, min_size=8.7, leading=13.1)


def _evidence(c, article):
    _box(c, 48, 171, 498, 76, "#F5F1EB")
    _box(c, 48, 171, 4, 76, RISK)
    _text(c, 63, 227, "RISK  /  WHAT WE DO NOT KNOW", 9.0, RISK, "Helvetica-Bold")
    _fit(c, 63, 208, article["risk_note"], width=459, lines=3,
         size=9.1, min_size=8.5, leading=12.4)
    _text(c, 49, 151, "PRIMARY SOURCES  /  VERIFIED LINKS", 8.3,
          TEAL, "Helvetica-Bold")
    sources = article["sources"]
    if len(sources) > 4:
        raise PremiumLayoutError("Too many sources for magazine print footer")
    for i, src in enumerate(sources):
        yy = 134 - i * 14
        domain = urlsplit(src["url"]).hostname
        line = f'[{i+1:02d}]  {domain}  /  {src["published_on"]}'
        _text(c, 51, yy, line, 8.0, SLATE)
        c.linkURL(src["url"], (48, yy - 3, 543, yy + 10), relative=0)
    _text(c, 49, 65, "Independent financial and publication approval is required.",
          8.0, SLATE)


def draw_article(c, number, section, article, chart_draw):
    family = FAMILIES.get(number)
    if not family:
        raise PremiumLayoutError("Unmapped magazine page")
    _article_head(c, number, section, article)
    # Chart drawing uses official numeric values and an invariant evidence
    # viewport. Distinct article composition is below the chart.
    _box(c, 48, 494, 498, 174, "#EBF3F3")
    _line(c, 48, 493, 546, 493, RULE)
    chart_draw(c)
    _lead(c, article, feature=(family in ("opening", "closing")))
    _analysis(c, article, family)
    _evidence(c, article)
