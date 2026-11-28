from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import unittest
from unittest.mock import Mock

from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_filings import (
    DownloadedFiling, extract_quarter, download_filing, check_url, MAX_BYTES
)

URL="https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_123_12112026172147_iXBRL_WEB.html"
NOW=datetime(2026,11,21,7,tzinfo=timezone.utc)
MEMBER=IndexMember("FIXTURE","INE000000001")
END=date(2026,9,30)

def fixture():
    def table(rows):
        return "<table>"+"".join("<tr>"+"".join("<td>"+str(c)+"</td>" for c in r)+"</tr>" for r in rows)+"</table>"
    return (
        table([
            ("NSE Symbol","FIXTURE"),("ISIN",MEMBER.isin),
            ("Name of company","Fictional Test Entity"),
            ("Description of presentation currency","INR"),
            ("Nature of report standalone or consolidated","Standalone"),
            ("Reporting Type","Quarterly"),
            ("Level of rounding used in financial results","Lakhs"),
            ("Date of board meeting when results were approved","12-11-2026")
        ])+table([
            ("Particulars","3 months/ 6 months ended (dd-mm-yyyy)","Year to date figures for current period ended (dd-mm-yyyy)"),
            ("","Date of start of reporting period","01-07-2026","01-04-2026"),
            ("","Date of end of reporting period","30-09-2026","30-09-2026"),
            ("","Nature of report standalone or consolidated","Standalone","Standalone"),
            ("","Whether results are audited or unaudited","Unaudited","Unaudited"),
            ("","Revenue from operations","1,23,456.78","999999.99"),
            ("16","Total profit (loss) for period","(2,345.67)","8888")
        ])
    ).encode()

class FilingTests(unittest.TestCase):
    def extract(self, body=None):
        return extract_quarter(DownloadedFiling(URL,fixture() if body is None else body,NOW),
                               MEMBER,expected_end=END,now=NOW)

    def test_quarter_units_loss_and_digest(self):
        result=self.extract()
        self.assertEqual(result.revenue_cr,Decimal("1234.5678"))
        self.assertEqual(result.profit_cr,Decimal("-23.4567"))
        self.assertEqual(result.digest,sha256(fixture()).hexdigest())
        self.assertFalse(result.audited)
        self.assertEqual(result.status,"EXTRACTED_PUBLICATION_TIME_UNVERIFIED")

    def test_legacy_actuals_nse_template_uses_explicit_rupee_scale(self):
        # Synthetic regression of the observed official 2025 schema,
        # not real prices or a copied company's financial statements.
        body=fixture().replace(b">INR<",b">INR (in Actuals)<").replace(
            b"<tr><td>Level of rounding used in financial results</td><td>Lakhs</td></tr>",b"")
        body=body.replace(b"1,23,456.78",b"12345678000").replace(b"(2,345.67)",b"-234567000")
        result=self.extract(body)
        self.assertEqual(result.revenue_cr,Decimal("1234.5678"))
        self.assertEqual(result.profit_cr,Decimal("-23.4567"))
        with self.assertRaisesRegex(EvidenceError,"FILING_UNIT_CONFLICT"):
            self.extract(fixture().replace(b">INR<",b">INR (in Actuals)<"))
        with self.assertRaises(EvidenceError):
            self.extract(body.replace(b"INR (in Actuals)",b"USD (in Actuals)"))
        with self.assertRaises(EvidenceError):
            self.extract(body.replace(b"Standalone",b"Consolidated",1))
        with self.assertRaises(EvidenceError):
            self.extract(body.replace(MEMBER.isin.encode(),b"INE999999999"))

    def test_legacy_first_quarter_blank_ytd_dates_do_not_change_discrete_quarter(self):
        body=fixture().replace(b">INR<",b">INR (in Actuals)<").replace(
            b"<tr><td>Level of rounding used in financial results</td><td>Lakhs</td></tr>",b"")
        body=body.replace(b"1,23,456.78",b"12345678000").replace(b"(2,345.67)",b"-234567000")
        body=body.replace(b"<td>01-07-2026</td><td>01-04-2026</td>",b"<td>01-04-2026</td><td></td>").replace(
            b"<td>30-09-2026</td><td>30-09-2026</td>",b"<td>30-06-2026</td><td></td>")
        def extract(raw):
            return extract_quarter(DownloadedFiling(URL,raw,NOW),MEMBER,expected_end=date(2026,6,30),now=NOW)
        self.assertEqual(extract(body).revenue_cr,Decimal("1234.5678"))
        with self.assertRaisesRegex(EvidenceError,"FILING_PERIOD_MISMATCH"):
            extract(body.replace(b"<td>30-06-2026</td><td></td>",b"<td>30-06-2026</td><td>30-06-2026</td>"))

    def test_legacy_actuals_with_observed_nse_text_month_dates(self):
        # Reproduce the date/unit schema observed in the original NSE 2025
        # IndAS filings, while keeping entirely synthetic financial values.
        body=fixture().replace(b">INR<",b">INR (in Actuals)<").replace(
            b"<tr><td>Level of rounding used in financial results</td><td>Lakhs</td></tr>",b"")
        body=body.replace(b"1,23,456.78",b"12345678000").replace(b"(2,345.67)",b"-234567000")
        for numeric,named in ((b"12-11-2026",b"12-Nov-2026"),
                              (b"01-07-2026",b"01-Jul-2026"),
                              (b"01-04-2026",b"01-Apr-2026"),
                              (b"30-09-2026",b"30-Sep-2026")):
            body=body.replace(numeric,named)
        result=self.extract(body)
        self.assertEqual(result.revenue_cr,Decimal("1234.5678"))
        self.assertEqual(result.profit_cr,Decimal("-23.4567"))
        self.assertEqual(result.end,END)
        for bad in (b"31-Sep-2026",b"09/30/2026",b"30-Unknown-2026",b"30-Jun-2026"):
            with self.subTest(bad=bad),self.assertRaises(EvidenceError):
                self.extract(body.replace(b"30-Sep-2026",bad))
        with self.assertRaises(EvidenceError):
            self.extract(body.replace(b"12-Nov-2026",b"12-Nov-2027"))

    def test_reject_wrong_identity_scope_dates_unit_and_missing_fields(self):
        for old,new in (
            (b"FIXTURE",b"WRONG"),(MEMBER.isin.encode(),b"INE999999999"),
            (b"Standalone",b"Consolidated"),(b"INR",b"USD"),
            (b"Lakhs",b"Unknown"),(b"3 months/ 6 months ended",b"Year to date figures"),(b"01-07-2026",b"01-04-2026"),
            (b"30-09-2026",b"31-12-2026"),(b"12-11-2026",b"12-11-2027"),
            (b"1,23,456.78",b"--"),(b"Total profit (loss) for period",b"other income"),
            (b"Unaudited",b"Unknown")
        ):
            with self.subTest(old=old,new=new), self.assertRaises(EvidenceError):
                self.extract(fixture().replace(old,new))

    def test_consolidated_scope_explicit_and_consistent(self):
        body = fixture().replace(b"Standalone", b"Consolidated")
        doc = DownloadedFiling(URL, body, NOW)
        result = extract_quarter(doc, MEMBER, expected_end=END, now=NOW, scope="consolidated")
        self.assertEqual(result.revenue_cr, Decimal("1234.5678"))
        self.assertEqual(result.profit_cr, Decimal("-23.4567"))
        with self.assertRaisesRegex(EvidenceError, "SCOPE_OR_CURRENCY"):
            self.extract(body)
        mixed = DownloadedFiling(URL, fixture().replace(b"Standalone", b"Consolidated", 1), NOW)
        with self.assertRaisesRegex(EvidenceError, "SCOPE_OR_CURRENCY"):
            extract_quarter(mixed, MEMBER, expected_end=END, now=NOW, scope="consolidated")

    def test_segment_table_is_not_income_statement(self):
        # Actual NSE format has a separate segment table with the same
        # period and revenue labels, but no total net profit field.
        segment = (b"<table><tr><td>Date of start of reporting period</td>"
                   b"<td>01-07-2026</td><td>01-04-2026</td></tr>"
                   b"<tr><td>Revenue from operations</td><td></td><td></td></tr></table>")
        result=self.extract(fixture()+segment)
        self.assertEqual(result.revenue_cr,Decimal("1234.5678"))
        self.assertEqual(result.profit_cr,Decimal("-23.4567"))

    def test_nbfc_total_revenue_not_heading_or_total_income(self):
        url = URL.replace("INTEGRATED_FILING_INDAS_", "INTEGRATED_FILING_NBFC_INDAS_")
        body = fixture().replace(b"Revenue from operations", b"Total Revenue From Operations")
        body = body.replace(b"<tr><td></td><td>Total Revenue From Operations",
            b"<tr><td></td><td>Revenue from operations</td><td></td><td></td></tr>"
            b"<tr><td></td><td>Total income</td><td>99999999</td><td>99999999</td></tr>"
            b"<tr><td></td><td>Total Revenue From Operations")
        body = body.replace(b"Standalone", b"Consolidated")
        def extract(raw):
            return extract_quarter(DownloadedFiling(url, raw, NOW), MEMBER,
                    expected_end=END, now=NOW, scope="consolidated")
        self.assertEqual(check_url(url), url)
        self.assertEqual(extract(body).revenue_cr, Decimal("1234.5678"))
        self.assertEqual(extract(body).profit_cr, Decimal("-23.4567"))
        with self.assertRaises(EvidenceError):
            extract(body.replace(b"Total Revenue From Operations", b"Interest income"))
        with self.assertRaises(EvidenceError):
            check_url(url.replace("NBFC_INDAS", "UNKNOWN_INDAS"))

    def test_url_allowlist(self):
        self.assertEqual(check_url(URL),URL)
        for url in (URL.replace("https:","http:"), URL.replace("nsearchives.nseindia.com","evil.example"),
                    URL+"?a=1",URL+"#foo",URL.replace("nsearchives.nseindia.com","nsearchives.nseindia.com@evil.example"),
                    URL.replace("iXBRL_WEB.html","file.pdf")):
            with self.assertRaises(EvidenceError):
                check_url(url)

    def mocked_session(self):
        session=Mock()
        response=Mock()
        response.status_code=200
        response.url=URL
        response.headers={"Content-Type":"text/html; charset=UTF-8"}
        response.iter_content.return_value=[fixture()]
        session.get.return_value.__enter__=Mock(return_value=response)
        session.get.return_value.__exit__=Mock(return_value=False)
        return session,response

    def test_download_hash_and_no_redirect(self):
        session,response=self.mocked_session()
        doc=download_filing(URL,session=session,clock=lambda:0)
        self.assertEqual(doc.digest,sha256(fixture()).hexdigest())
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertTrue(session.get.call_args.kwargs["stream"])

    def test_download_rejections(self):
        for field,value in (("status_code",302),("url",URL+"?x=1"),
                            ("headers",{"Content-Type":"text/plain"}),
                            ("headers",{"Content-Type":"text/html","Content-Length":str(MAX_BYTES+1)})):
            session,response=self.mocked_session()
            setattr(response,field,value)
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                download_filing(URL,session=session)
        session,response=self.mocked_session()
        response.iter_content.return_value=[b"x"*(MAX_BYTES+1)]
        with self.assertRaisesRegex(EvidenceError,"FILING_SIZE"):
            download_filing(URL,session=session)
