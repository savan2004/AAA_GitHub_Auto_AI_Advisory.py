import unittest
from datetime import date,datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_legacy_xml import extract_quarter,verify_legacy_quarter

DAY=date(2024,12,31)
NOW=datetime(2026,11,28,tzinfo=timezone.utc)
FILED=datetime(2025,2,1,tzinfo=timezone.utc)
MEMBER=IndexMember("FIXTURE","INE000000001")
URL="https://nsearchives.nseindia.com/corporate/xbrl/INDAS_123_456_01022025121212.xml"

def fixture():
    return b"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
    <xbrli:context id="OneD"><xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
    <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
    <fin:Symbol contextRef="OneD">FIXTURE</fin:Symbol>
    <fin:ISIN contextRef="OneD">INE000000001</fin:ISIN>
    <fin:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</fin:NatureOfReportStandaloneConsolidated>
    <fin:DateOfStartOfReportingPeriod contextRef="OneD">2024-10-01</fin:DateOfStartOfReportingPeriod>
    <fin:DateOfEndOfReportingPeriod contextRef="OneD">2024-12-31</fin:DateOfEndOfReportingPeriod>
    <fin:DescriptionOfPresentationCurrency contextRef="OneD">INR</fin:DescriptionOfPresentationCurrency>
    <fin:WhetherResultsAreAuditedOrUnaudited contextRef="OneD">Unaudited</fin:WhetherResultsAreAuditedOrUnaudited>
    <fin:DateOfBoardMeetingWhenFinancialResultsWereApproved contextRef="OneD">2025-01-31</fin:DateOfBoardMeetingWhenFinancialResultsWereApproved>
    <fin:RevenueFromOperations contextRef="OneD" unitRef="INR">12350000000</fin:RevenueFromOperations>
    <fin:ProfitLossForPeriod contextRef="OneD" unitRef="INR">-230000000</fin:ProfitLossForPeriod>
    <fin:RevenueFromOperations contextRef="FourD" unitRef="INR">99999999999</fin:RevenueFromOperations>
    </xbrli:xbrl>"""

class LegacyXMLTests(unittest.TestCase):
    def extract(self,body):
        return extract_quarter(body,MEMBER,period_end=DAY,scope="consolidated",disseminated_at=FILED,now=NOW)
    def test_exact_quarter_not_ytd_and_actual_rupees(self):
        self.assertEqual(self.extract(fixture()),(Decimal(1235),Decimal(-23)))
    def test_fail_closed(self):
        for old,new in ((b"INE000000001",b"INE999999999"),
                        (b"Consolidated",b"Standalone"),
                        (b"2024-10-01",b"2024-04-01"),
                        (b"iso4217:INR",b"iso4217:USD"),
                        (b"12350000000",b"NaN"),
                        (b"2025-01-31",b"2025-02-02"),
                        (b'contextRef="OneD" unitRef="INR">1235',b'contextRef="FourD" unitRef="INR">1235')):
            with self.subTest(old=old),self.assertRaises(EvidenceError):
                self.extract(fixture().replace(old,new))
        with self.assertRaises(EvidenceError):
            self.extract(b"<!DOCTYPE xbrl>"+fixture())
    def test_numeric_reconciliation_and_no_publication(self):
        evidence=SimpleNamespace(symbol=MEMBER.symbol,scope="consolidated",retrieved_at=NOW,
                  quarterly=[SimpleNamespace(end=DAY,revenue_cr=1235,profit_cr=-23),
                             SimpleNamespace(end=date(2026,9,30),revenue_cr=1,profit_cr=1)])
        discovery={"status":"LEGACY_REFERENCES_DISCOVERED_ONLY","symbol":MEMBER.symbol,"isin":MEMBER.isin,
                   "period":DAY.isoformat(),"scope":"consolidated","publication_status":"WITHHELD",
                   "references":[{"url":URL,"disseminated_at":FILED.isoformat()}]}
        def check():
            return verify_legacy_quarter(MEMBER,evidence,period_end=DAY,discovery=discovery,
                                         download=lambda _:fixture(),clock=lambda:NOW)
        result=check()
        self.assertEqual(result["status"],"HISTORICAL_QUARTER_MATCHED_ONLY")
        self.assertEqual(result["publication_status"],"WITHHELD")
        self.assertEqual(len(result["document_sha256"]),64)
        evidence.quarterly[0].revenue_cr=999
        with self.assertRaisesRegex(EvidenceError,"VALUE_MISMATCH"):check()
        discovery["references"]*=2
        with self.assertRaisesRegex(EvidenceError,"REFERENCE_AMBIGUOUS"):check()
