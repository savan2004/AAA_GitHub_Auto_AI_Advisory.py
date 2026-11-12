"""Render and assess a completely SYNTHETIC research PDF for visual CI.

Never use synthetic data as actual recommendations, market prices or issuer
filing evidence. Artifact names include SYNTHETIC and NOT_FOR_DISTRIBUTION.
"""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "123:offline-only")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from fixtures import strong
import report_generator as rg
from pdf_release_gate import validate_research_pdf


def main():
    outdir = Path(os.environ.get("MSK_SYNTHETIC_QA_DIR", "/tmp/msk-pdf-visual-qa"))
    outdir.mkdir(parents=True, exist_ok=True)
    pdfpath = outdir / "SYNTHETIC_NOT_FOR_DISTRIBUTION_Research_Report.pdf"
    fixture = strong()
    assembled = rg.assemble(fixture, use_ai=False)
    try:
        rg.build_pdf(assembled, str(pdfpath))
        summary = validate_research_pdf(pdfpath, expected_symbol="STRONGCO")
        pdf = pdfium.PdfDocument(str(pdfpath))
        renders, coverage = [], []
        for i in range(len(pdf)):
            page = pdf[i]
            image = page.render(scale=0.55).to_pil().convert("RGB")
            gray = image.convert("L")
            hist = gray.histogram()
            nonblank = sum(hist[:238]) / (image.width * image.height)
            if nonblank < 0.006:
                raise RuntimeError(f"PDF page {i+1} appears nearly blank")
            coverage.append(round(nonblank, 4))
            thumb = image.copy()
            thumb.thumbnail((440, 615))
            renders.append(thumb)
            page.close()
        cols = 3
        rows = (len(renders) + cols - 1) // cols
        contact = Image.new("RGB", (cols * 460, rows * 660), "white")
        draw = ImageDraw.Draw(contact)
        for i, thumb in enumerate(renders):
            x, y = (i % cols) * 460 + 10, (i // cols) * 660 + 30
            contact.paste(thumb, (x, y))
            draw.text((x, y - 20), f"PAGE {i+1} - SYNTHETIC QA", fill="black")
        contact.save(outdir / "SYNTHETIC_PAGE_CONTACT_SHEET.png")
        summary["page_visible_content_ratio"] = coverage
        summary["synthetic_only"] = True
        summary["issuer_filing_verified"] = False
        summary["real_data_reconciled"] = False
        (outdir / "qa.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        for key in ("tech_price", "tech_momentum", "tech", "quarterly"):
            artifact = (assembled.get("ch") or {}).get(key)
            if isinstance(artifact, str):
                Path(artifact).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
