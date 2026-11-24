"""Opt-in A4 editorial print-engine pilot for MSK Magazine PR #265.

Builds four page-design proofs, not a customer magazine. No Telegram,
no API calls, no production changes. Only uses the current dated evidence
and source-cited editorial; full 16 pages remain gated in existing engine.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date
import html
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pypdf import PdfReader

from scripts.msk_magazine_charts import load_metrics
from scripts.msk_magazine_cover_story import select_cover_story
from scripts.msk_magazine_editorial import load_editorial_pack

ROOT = Path(__file__).resolve().parents[1]
ISSUE = "2026-11"
PACK = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"
EDIT = ROOT / "docs/msk_magazine/ISSUE_2026-11_SOURCE_CITED_WORKING_DRAFT.json"
LOGO = ROOT / "assets/msk_ai_bot_logo_recreated.png"
TEMPLATE = ROOT / "templates/msk_magazine_pilot_a4.html"


def input_data():
    """Source/date checks precede all printable material."""
    pack = load_metrics(PACK, ISSUE)
    stories = load_editorial_pack(EDIT, ISSUE, as_of=date(2026, 11, 24))
    cover = select_cover_story(ISSUE, pack)
    if not cover or 3 not in stories or 8 not in stories:
        raise ValueError("Verified cover, index and sector stories required")
    if len(stories) != 14:
        raise ValueError("Full cited research pack required even for pilot")
    indices = {item["name"]: item for item in pack["market_indices"]}
    rows = [
        {"name": name, "value": indices[name]["m1"], "sign": "up" if indices[name]["m1"] >= 0 else "down",
         "bar": min(42, abs(indices[name]["m1"])*4.5)}
        for name in ("Nifty 50", "Nifty Bank", "Nifty 500", "Nifty Midcap 150", "Nifty Smallcap 250")
    ]
    sectors = sorted(
        ({"name": x["name"].replace("Nifty ", ""), "value": x["m1"],
          "status": "up" if x["m1"] >= 0 else "down"} for x in pack["sectors"]),
        key=lambda row: row["value"], reverse=True)
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    return {"cover": cover, "pack": pack, "market": stories[3], "sector": stories[8],
            "rows": rows, "sectors": sectors, "logo": logo,
            "index": [{"no": "01", "label": "Cover feature · The market divide"},
                      {"no": "02", "label": "Editor's note & contents"},
                      {"no": "03", "label": "Executive market outlook"},
                      {"no": "08", "label": "Sector performance & rotation"}]}


def build(output_dir: Path, *, renderer="plutoprint"):
    if renderer != "plutoprint":
        raise ValueError("Only the explicit PlutoPrint print-engine pilot is supported")
    from plutoprint import Book, PAGE_SIZE_A4  # import only for opt-in pilot
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATE.parent)),
                      autoescape=select_autoescape(["html"]), trim_blocks=True,
                      lstrip_blocks=True)
    html_doc = env.get_template(TEMPLATE.name).render(**input_data())
    # HTML is the inspectable source: kept beside PDF for design review.
    html_file = dest / "MSK_NOV2026_PRINT_PILOT.html"
    html_file.write_text(html_doc, encoding="utf-8")
    book = Book(PAGE_SIZE_A4)
    book.load_html(html_doc)
    pdf_file = dest / "MSK_NOV2026_PRINT_PILOT_UNAPPROVED.pdf"
    book.write_to_pdf(str(pdf_file))
    reader = PdfReader(str(pdf_file))
    if len(reader.pages) != 4:
        raise ValueError(f"Expected four A4 layout proofs, got {len(reader.pages)}")
    for n, page in enumerate(reader.pages, 1):
        txt = " ".join((page.extract_text() or "").split())
        if "UNAPPROVED" not in txt:
            raise ValueError(f"Page {n}: private proof watermark missing")
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if abs(w - 595.276) > 1 or abs(h - 841.89) > 1:
            raise ValueError(f"Page {n}: wrong A4 dimensions")
    first = " ".join(reader.pages[0].extract_text().split())
    if "NIFTY RISES. SMALL CAPS FALL." not in first:
        raise ValueError("Evidence-bound cover story missing from PDF")
    report = {"status":"A4_PRIVATE_PROOF_NOT_FOR_DISTRIBUTION", "page_count":4,
              "page_types":["cover","contents","index_comparison","sector_heatmap"],
              "publication_allowed":False,
              "source":input_data()["cover"]["source_url"],
              "renderer":"plutoprint","issue":ISSUE}
    (dest / "pilot_check.json").write_text(json.dumps(report, indent=2)+"\n",encoding="utf-8")
    return report


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",default="magazine_print_pilot")
    p.add_argument("--renderer",default="plutoprint")
    args=p.parse_args()
    print(json.dumps(build(Path(args.output_dir),renderer=args.renderer)))
