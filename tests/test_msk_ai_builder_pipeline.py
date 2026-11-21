import unittest
from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from pypdf import PdfReader
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_evidence import EvidenceError, normalize_secondary
from msk_ai_builder_primary import IndexMember, FilingPeriod, parse_index, reconcile_statements
from msk_ai_builder_demo import synthetic_companies, build_demo_basket, EQUITY_CAP, score
from msk_ai_builder_demo_pdf import demo_pdf
from test_msk_ai_builder_evidence import fixture, NOW

class PrimaryTests(unittest.TestCase):
    def test_exact_index_only(self):
        rows=[{"symbol":f"TEST{i}","isin":f"INE{i:09d}"} for i in range(500)]
        self.assertEqual(len(parse_index({"data":rows})),500)
        for bad in (rows[:-1],rows[:-1]+[rows[0]],rows[:-1]+[{"symbol":"TEST499","isin":"bad"}]):
            with self.assertRaises(EvidenceError):
                parse_index({"data":bad})
        with self.assertRaises(EvidenceError):
            parse_index({})

    def test_all_periods_reconciled_and_not_authenticated(self):
        s=normalize_secondary("FIXTURE",fixture(),now=NOW)
        m=IndexMember("FIXTURE","INE000000001")
        rows=[FilingPeriod(m.symbol,m.isin,s.scope,basis,p.end,NOW,
                 "https://nsearchives.nseindia.com/fictional-test-only", "a"*64,
                 Decimal(str(p.revenue_cr)),Decimal(str(p.profit_cr)))
              for basis,periods in (("FY",s.annual),("Q",s.quarterly)) for p in periods]
        self.assertEqual(reconcile_statements(m,s,rows,now=NOW),"MATCHED_EXTRACTS_NOT_AUTHENTICATED")
        self.assertFalse(s.eligible_for_selection)
        for bad in (rows[:-1],rows+[rows[0]], [replace(rows[0],scope="standalone")]+rows[1:],
                    [replace(rows[0],revenue_cr=Decimal("1"))]+rows[1:],
                    [replace(rows[0],filing_url="https://evil.example/a")]+rows[1:],
                    [replace(rows[0],document_sha256="")]+rows[1:]):
            with self.assertRaises(EvidenceError):
                reconcile_statements(m,s,bad,now=NOW)

class DemoTests(unittest.TestCase):
    def test_36_profiles_and_caps(self):
        for capital in (1000000,5000000,10000000):
            for risk in ("low","medium","high"):
                for months in (6,18,36,60):
                    p=ResearchProfile(capital,risk,months)
                    with self.subTest(profile=p):
                        b=build_demo_basket(p,synthetic_companies())
                        self.assertLessEqual(len(b.positions),p.max_stocks)
                        self.assertEqual(sum(x.rupees for x in b.positions)+b.reserve,capital)
                        self.assertGreaterEqual(b.reserve,capital*(100-EQUITY_CAP[risk][months])//100)
                        self.assertTrue(all(x.score>=75 and 0<x.rupees<=capital*(10 if risk=="low" else 15)//100 for x in b.positions))
                        self.assertEqual(len({x.company.sector for x in b.positions}),len(b.positions))

    def test_approved_example(self):
        b=build_demo_basket(ResearchProfile(1000000,"medium",18),synthetic_companies())
        self.assertEqual(len(b.positions),5)
        self.assertEqual(b.reserve,400000)
        self.assertTrue(all(x.rupees==120000 for x in b.positions))

    def test_no_forced_candidates(self):
        p=ResearchProfile(1000000,"medium",18)
        self.assertEqual(build_demo_basket(p,()).reserve,p.capital_rupees)
        c=replace(synthetic_companies()[0],drawdown=99)
        self.assertEqual(score(c),0)
        self.assertEqual(build_demo_basket(p,[c]).positions,())
        with self.assertRaises(ValueError):
            build_demo_basket(p,[c,c])

    def test_pdf_page_structure_and_label(self):
        for capital,risk,months in ((1000000,"medium",18),(5000000,"medium",36),
                                    (10000000,"high",60),(1000000,"low",6)):
            p=ResearchProfile(capital,risk,months)
            b=build_demo_basket(p,synthetic_companies())
            reader=PdfReader(BytesIO(demo_pdf(p)))
            self.assertEqual(len(reader.pages),6+2*len(b.positions))
            for page in reader.pages:
                text=page.extract_text()
                self.assertIn("FICTIONAL DEMONSTRATION",text)
                self.assertGreater(len(text),200)
