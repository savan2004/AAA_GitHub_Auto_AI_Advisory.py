"""Offline SBIN-like layout diagnostics. Data are synthetic and not recommendations."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
os.environ.setdefault("TELEGRAM_TOKEN", "123:synthetic-test")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import strong
import report_generator as rg
from pdf_release_gate import validate_research_pdf, ResearchPDFRefused
from pypdf import PdfReader

def run():
    bank = {"kind":"bank", "is_lender":True, "is_financial":True, "reasons":["synthetic"]}
    variants = [
      ("bank-complete", strong(sym="SBIN",name="State Bank of India",industry=bank)),
      ("bank-without-statements", strong(sym="SBIN",name="State Bank of India",industry=bank,stm=None,extras={},peers=[])),
    ]
    with TemporaryDirectory() as d:
        for label, data in variants:
            try:
                result=rg.assemble(data,use_ai=False)
                out=Path(d)/(label+".pdf")
                rg.build_pdf(result,str(out))
                reader=PdfReader(str(out),strict=True)
                chars=[len((p.extract_text() or "").strip()) for p in reader.pages]
                print("SBIN_LAYOUT layout={} bytes={} pages={} chars={}".format(label,out.stat().st_size,len(chars),chars),flush=True)
                try:
                    validate_research_pdf(out,expected_symbol="SBIN")
                    print("SBIN_LAYOUT_RESULT layout={} result=accepted".format(label),flush=True)
                except ResearchPDFRefused as exc:
                    print("SBIN_LAYOUT_RESULT layout={} result=refused reason={}".format(label,exc),flush=True)
            except Exception as exc:
                print("SBIN_LAYOUT_RESULT layout={} result=error type={} reason={}".format(label,type(exc).__name__,exc),flush=True)
                raise
if __name__=="__main__":
    run()
