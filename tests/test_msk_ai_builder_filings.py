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

    def test_reject_wrong_identity_scope_dates_unit_and_missing_fields(self):
        for old,new in (
            (b"FIXTURE",b"WRONG"),(MEMBER.isin.encode(),b"INE999999999"),
            (b"Standalone",b"Consolidated"),(b"INR",b"USD"),
            (b"Lakhs",b"Unknown"),(b"01-07-2026",b"01-04-2026"),
            (b"30-09-2026",b"31-12-2026"),(b"12-11-2026",b"12-11-2027"),
            (b"1,23,456.78",b"--"),(b"Total profit (loss) for period",b"other income"),
            (b"Unaudited",b"Unknown")
        ):
            with self.subTest(old=old,new=new), self.assertRaises(EvidenceError):
                self.extract(fixture().replace(old,new))

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
