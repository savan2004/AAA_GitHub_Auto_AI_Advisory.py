import unittest
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_pdf_evidence import extract_pdf_period

MEMBER=IndexMember("FIXTURE","INE000000001")
URL="https://nsearchives.nseindia.com/corporate/fixture.pdf"
HEADERS=("Particulars","Quarter ended 2026-09-30","YTD ended 2026-09-30")

def fixture(*,symbol="FIXTURE",unit="Lakhs",duplicate=False):
    b=BytesIO()
    c=canvas.Canvas(b)
    for i,line in enumerate(("NSE Symbol: "+symbol,"ISIN: "+MEMBER.isin,
                             "Reporting scope: Standalone","Currency: INR","Units: "+unit)):
        c.drawString(40,780-i*18,line)
    rows=[HEADERS,("Revenue from operations","12,345.67","999999"),
          ("Total profit (loss) for period","(234.50)","88888")]
    if duplicate:
        rows.append(rows[-1])
    t=Table(rows,colWidths=[180,155,155])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.black)]))
    t.wrapOn(c,500,400)
    t.drawOn(c,40,590)
    c.save()
    return b.getvalue()

def extract(body,**overrides):
    args=dict(source_url=URL,expected_sha256=sha256(body).hexdigest(),page_number=1,
              end=date(2026,9,30),basis="Q",scope="Standalone",units="Lakhs",
              column_headers=HEADERS,revenue_label="Revenue from operations",
              profit_label="Total profit (loss) for period")
    args.update(overrides)
    return extract_pdf_period(body,MEMBER,**args)

class PDFEvidenceTests(unittest.TestCase):
    def test_exact_quarter_not_ytd(self):
        body=fixture()
        evidence=extract(body)
        self.assertEqual(evidence.revenue_cr,Decimal("123.4567"))
        self.assertEqual(evidence.profit_cr,Decimal("-2.345"))
        self.assertEqual(evidence.document_sha256,sha256(body).hexdigest())
        self.assertEqual(evidence.status,"EXTRACTED_PDF_REVIEW_REQUIRED")

    def test_identity_units_duplicate_rows_wrong_period_and_bad_source(self):
        for body,kw in [(fixture(symbol="WRONG"),{}),(fixture(unit="Crores"),{}),
                        (fixture(duplicate=True),{}),(fixture(),{"basis":"FY"}),
                        (fixture(),{"page_number":2}),(fixture(),{"expected_sha256":"0"*64}),
                        (fixture(),{"source_url":"https://evil.example/file.pdf"}),
                        (fixture(),{"scope":"Consolidated"})]:
            with self.subTest(kw=kw),self.assertRaises(EvidenceError):
                extract(body,**kw)

    def test_non_pdf_and_missing_row(self):
        with self.assertRaises(EvidenceError):
            extract(b"fake")
        with self.assertRaises(EvidenceError):
            extract(fixture(),profit_label="Previous profit")
