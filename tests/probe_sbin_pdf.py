"""Real-data SBIN research PDF diagnostic. Never sends a Telegram message."""
from pathlib import Path
from tempfile import TemporaryDirectory
import logging
import os
import sys

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MSK_RESEARCH_ONLY", "1")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from pypdf import PdfReader
from pdf_release_gate import validate_research_pdf, ResearchPDFRefused
import report_generator as rg

def run():
    symbol = "SBIN"
    print("SBIN_REPORT_PROBE_START", flush=True)
    inputs = rg.collect_inputs(symbol)
    print("SBIN_INPUT_CHECK candle_rows={} fund_fields={} annuals={} quarters={}".format(
        len(inputs["df"]),
        sorted(k for k,v in (inputs.get("fund") or {}).items() if v is not None and not k.startswith("_")),
        len((inputs.get("stm") or {}).get("years") or []),
        len((inputs.get("stm") or {}).get("quarters") or [])),flush=True)
    assembled = rg.assemble(inputs,use_ai=False)
    with TemporaryDirectory() as d:
        path = Path(d)/"SBIN_report_probe.pdf"
        rg.build_pdf(assembled,str(path))
        print("SBIN_PDF_BUILT bytes={}".format(path.stat().st_size),flush=True)
        reader=PdfReader(str(path),strict=True)
        extracted=[(p.extract_text() or "").strip() for p in reader.pages]
        print("SBIN_PDF_PAGES count={} lengths={}".format(len(extracted),[len(s) for s in extracted]),flush=True)
        for term in ("Technical Analysis","ONE-PAGE DECISION SNAPSHOT","Not SEBI-registered advice","Financial figures and ratios were not independently reconciled","SBIN"):
            print("SBIN_PDF_REQUIRED {} {}".format(term,int(term.lower() in " ".join(extracted).lower())),flush=True)
        try:
            quality=validate_research_pdf(path,expected_symbol=symbol)
        except ResearchPDFRefused as e:
            print("SBIN_PDF_GATE_REJECTED reason={}".format(str(e)),flush=True)
            return 1
        print("SBIN_PDF_GATE_PASSED pages={} bytes={}".format(quality["pages"],quality["bytes"]),flush=True)
    return 0

if __name__=="__main__":
    try:
        sys.exit(run())
    except Exception:
        logging.exception("SBIN_REPORT_PROBE_CRASH")
        raise
