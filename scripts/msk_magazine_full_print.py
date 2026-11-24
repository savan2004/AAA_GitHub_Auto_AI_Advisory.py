"""Private source-backed 16-page MSK Magazine using original editorial HTML.

The print layout is influenced by general editorial practices found in the
owner's Diwali design sample and the NSE Market Pulse reference. No assets,
templates, or copyrighted prose are copied from those publications.
No Telegram action, approval change, deployment, or public publication.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from plutoprint import Book, PAGE_SIZE_A4
from pypdf import PdfReader

from scripts.msk_magazine_cover_story import select_cover_story
from scripts.msk_magazine_charts import load_metrics
from scripts.msk_magazine_editorial import load_editorial_pack
from scripts.msk_magazine_monthly import load_master, RECREATED_LOGO
from scripts.msk_magazine_source_archive import verify_nse_dashboard
from scripts.msk_magazine_primary_sources import recheck_primary_sources

ROOT = Path(__file__).resolve().parents[1]
ISSUE = "2026-11"
MARKET = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"
EDITORIAL = ROOT / "docs/msk_magazine/ISSUE_2026-11_SOURCE_CITED_WORKING_DRAFT.json"
TEMPLATE = ROOT / "templates/msk_magazine_full_a4.html"
EXTRA_CSS = ROOT / "templates/msk_magazine_full_extra.css"
PILOT = ROOT / "templates/msk_magazine_pilot_a4.html"
ZERO_SEND = True


def source_proof(market, nse_dir: Path, primary_dir: Path):
    """Require identical checks as original draft before binding financial facts."""
    source = Path(nse_dir) / "Index_Dashboard_OCT2026.pdf"
    receipt = Path(nse_dir) / "nse_dashboard_reconciliation.json"
    if not source.is_file() or not receipt.is_file() or source.is_symlink() or receipt.is_symlink():
        raise ValueError("Original NSE dashboard + reconciliation receipt required")
    data = verify_nse_dashboard(source.read_bytes(), market)
    old = json.loads(receipt.read_text(encoding="utf-8"))
    if data["sha256"] != old.get("sha256") or data["checked_rows"] != old.get("checked_rows"):
        raise ValueError("NSE receipt mismatch; do not render")
    primary = recheck_primary_sources(Path(primary_dir))
    if not primary:
        raise ValueError("Primary macro/issuer source receipts absent")
    return data, primary


def evidence_rows(indices, names, field="m1"):
    rows = [{"name": name, "value": float(indices[name][field])} for name in names]
    scale = max(1, *(abs(x["value"]) for x in rows))
    for row in rows:
        row["up"] = row["value"] >= 0
        row["bar"] = round((abs(row["value"]) / scale) * 45, 2)
    return rows


def context():
    market = load_metrics(MARKET, ISSUE)
    stories = load_editorial_pack(EDITORIAL, ISSUE, as_of=date(2026, 11, 24))
    master = load_master()
    cover = select_cover_story(ISSUE, market)
    if not cover or set(stories) != set(range(3, 17)):
        raise ValueError("Missing complete verified content/cover")
    idx = {x["name"]: x for x in market["market_indices"]}
    sectors = sorted(
        [{"name": x["name"].replace("Nifty ",""), "value": x["m1"], "up": x["m1"] >= 0}
         for x in market["sectors"]], key=lambda x: x["value"], reverse=True)
    macro = {x["name"]: x for x in market["macro"]}
    issuers = {x["name"]: x for x in market["issuer_results"]}
    png = RECREATED_LOGO.read_bytes()
    logo = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return dict(issue=ISSUE, cover=cover, market=market, stories=stories,
                sections={item["page"]: item["title"] for item in master["locked_index"]},
                index=idx, macro=macro, issuers=issuers, sectors=sectors,
                logo=logo,
                index_rows=evidence_rows(idx, ["Nifty 50","Nifty Bank","Nifty 500",
                                               "Nifty Midcap 150","Nifty Smallcap 250"]),
                nifty_periods=[{"name": x,"value": idx["Nifty 50"][f]}
                               for x,f in [("1 month","m1"),("3 months","m3"),("1 year","y1")]],
                breadth_rows=evidence_rows(idx,["Nifty 500","Nifty 500 Equal Weight"]),
                calendar=market["calendar"])


def make(output_dir, nse_dir, primary_dir):
    base = Path(output_dir)
    # Verify before writing any PDF bytes.
    ctx = context()
    nse_receipt, primary_receipts = source_proof(ctx["market"], Path(nse_dir), Path(primary_dir))
    pilot_html = PILOT.read_text(encoding="utf-8")
    css = pilot_html.split("<style>",1)[1].split("</style>",1)[0] + "\n" + EXTRA_CSS.read_text(encoding="utf-8")
    env = Environment(loader=FileSystemLoader(str(TEMPLATE.parent)),
                      autoescape=select_autoescape(["html"]), trim_blocks=True,
                      lstrip_blocks=True)
    html_content = env.get_template(TEMPLATE.name).render(**ctx, css=css)
    base.mkdir(parents=True, exist_ok=True)
    html_out = base / "MSK_2026-11_MAGAZINE_PREVIEW.html"
    html_out.write_text(html_content, encoding="utf-8")
    pdf = base / "MSK_2026-11_16PAGE_MAGAZINE_PRIVATE_DRAFT.pdf"
    book = Book(PAGE_SIZE_A4)
    book.load_html(html_content)
    book.write_to_pdf(str(pdf))
    reader = PdfReader(str(pdf))
    if len(reader.pages) != 16:
        raise ValueError(f"Expected exactly 16 pages; rendered {len(reader.pages)}")
    all_text = []
    for n,page in enumerate(reader.pages,1):
        w,h = float(page.mediabox.width), float(page.mediabox.height)
        if abs(w-595.28)>1 or abs(h-841.89)>1:
            raise ValueError(f"Page {n}: not A4")
        t = " ".join((page.extract_text() or "").split())
        if "UNAPPROVED" not in t:
            raise ValueError(f"Page {n}: private draft watermark missing")
        if n >= 3:
            if ctx["stories"][n]["headline"] not in t:
                raise ValueError(f"Page {n}: source-backed headline missing")
            if "RISKS / IMPORTANT LIMITS" not in t or ctx["stories"][n]["risk_note"] not in t:
                raise ValueError(f"Page {n}: risk disclosure missing or clipped")
            if "ORIGINAL DATA SOURCES & DATES" not in t:
                raise ValueError(f"Page {n}: source caption missing or clipped")
            links = {str(a.get_object().get("/A",{}).get("/URI",""))
                     for a in page.get("/Annots",[])}
            for src in ctx["stories"][n]["sources"]:
                if src["url"] not in links:
                    raise ValueError(f"Page {n}: source URL missing: {src['url']}")
        all_text.append(t)
    if ctx["cover"]["headline"] not in all_text[0]:
        raise ValueError("Cover story missing")
    for req in ("+1.95%", "-5.78%"):
        if req not in all_text[2]:
            raise ValueError("Index chart figure missing: "+req)
    if not all(term in all_text[1] for term in ("P/E","CPI","TRI")):
        raise ValueError("Indian-retail investor glossary missing")
    if "18.06%" not in all_text[7] or "-7.51%" not in all_text[7]:
        raise ValueError("Sector heatmap lacks dated NSE returns")
    gates = dict(issue=ISSUE, pages=16, document_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                 renderer="PlutoPrint+Jinja2", primary_source_count=primary_receipts["sources_checked"],
                 nse_source_sha256=nse_receipt["sha256"],
                 source_verified=True, pdf_structure_passed=True, owner_approved=False,
                 publication_allowed=False, telegram_sent=False,
                 visual_reviewed_by_owner=False,
                 status="UNAPPROVED_16_PAGE_MAGAZINE_FOR_OWNER_REVIEW_ONLY")
    (base/"print_gate.json").write_text(json.dumps(gates,indent=2)+"\n",encoding="utf-8")
    return gates


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--nse-proof-dir", required=True)
    parser.add_argument("--primary-proof-dir", required=True)
    args=parser.parse_args()
    print(json.dumps(make(args.output_dir,args.nse_proof_dir,args.primary_proof_dir),indent=2))
