import unittest
from datetime import date,datetime,timezone
from msk_ai_builder_discovery import parse_listing
from msk_ai_builder_evidence import EvidenceError

END=date(2026,9,30)
NOW=datetime(2026,11,21,9,tzinfo=timezone.utc)
URL="https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_212819_12112026172147_iXBRL_WEB.html"
def row(**kw):
    return dict(symbol="NIRAJISPAT",qe_Date="30-SEP-2026",consolidated="Standalone",
        broadcast_Date="12-Nov-2026 17:22:00",revised_Date=None,ixbrl=URL)|kw
def parse(rows,**kw):
    return parse_listing({"totalCount":len(rows),"data":rows}|kw,symbol="NIRAJISPAT",
                         period_end=END,scope="standalone",now=NOW)
class DiscoveryTests(unittest.TestCase):
    def test_original_and_revision(self):
        first=row()
        revised=row(revised_Date="13-Nov-2026 12:00:00",ixbrl=URL.replace("212819","212820"))
        self.assertEqual(parse([first]).url,URL)
        self.assertEqual(parse([first,revised]).url,revised["ixbrl"])
        self.assertIsNotNone(parse([first,revised]).revised_at)
    def test_nse_revision_without_broadcast_requires_exact_original(self):
        original = row()
        revised = row(broadcast_Date=None,
                      revised_Date="13-Nov-2026 12:00:00",
                      ixbrl=URL.replace("212819", "212821"))
        checked = parse([original, revised])
        self.assertEqual(checked.url, revised["ixbrl"])
        self.assertEqual(checked.broadcast_at,
                         datetime(2026, 11, 12, 17, 22, tzinfo=checked.broadcast_at.tzinfo))
        self.assertIsNotNone(checked.revised_at)
        with self.assertRaisesRegex(EvidenceError, "REVISION_UNLINKED"):
            parse([revised])
        with self.assertRaisesRegex(EvidenceError, "REVISION_UNLINKED"):
            parse([original, row(ixbrl=URL.replace("212819", "212822")), revised])
        with self.assertRaisesRegex(EvidenceError, "METADATA_TIME"):
            parse([original, row(broadcast_Date=None,
                                 revised_Date="11-Nov-2026 12:00:00",
                                 ixbrl=revised["ixbrl"])])

    def test_incomplete_or_bad_identity(self):
        for rows,kw in [([row()],{"totalCount":21}),([row(symbol="OTHER")],{}),
           ([row(qe_Date="30-JUN-2026")],{}),([row(consolidated="Consolidated")],{}),
           ([row(broadcast_Date="22-Nov-2026 12:00:00")],{}),
           ([row(revised_Date="11-Nov-2026 12:00:00")],{}),
           ([row(ixbrl="https://example.org/a")],{})]:
            with self.assertRaises(EvidenceError):
                parse(rows,**kw)
    def test_ambiguity_rejected(self):
        with self.assertRaisesRegex(EvidenceError,"AMBIGUOUS_REVISION"):
            parse([row(),row(ixbrl=URL.replace("212819","212820"))])

class LegacyDiscoveryTests(unittest.TestCase):
    def test_legacy_reference_only_and_identity_guards(self):
        from msk_ai_builder_discovery import parse_legacy_listing
        from msk_ai_builder_primary import IndexMember
        member=IndexMember("FIXTURE","INE000000001")
        period=date(2024,12,31)
        base=dict(symbol=member.symbol,isin=member.isin,fromDate="01-Oct-2024",
                  toDate="31-Dec-2024",consolidated="Consolidated",period="Quarterly",
                  cumulative="Non-cumulative",broadCastDate="16-Jan-2025 20:15:20",
                  exchdisstime="16-Jan-2025 20:15:46",
                  xbrl="https://nsearchives.nseindia.com/corporate/xbrl/INDAS_123_456_16012025081520.xml")
        def parse(rows):
            return parse_legacy_listing(rows,member=member,period_end=period,scope="consolidated",now=NOW)
        result=parse([base,dict(base,consolidated="Non-Consolidated")])
        self.assertEqual(result["status"],"LEGACY_REFERENCES_DISCOVERED_ONLY")
        self.assertEqual(result["publication_status"],"WITHHELD")
        self.assertEqual(len(result["references"]),1)
        for change in (dict(isin="INE999999999"),dict(cumulative="Cumulative"),
                       dict(fromDate="01-Apr-2024"),dict(xbrl="https://example.org/x.xml"),
                       dict(exchdisstime="15-Jan-2025 20:15:46"),dict(toDate="30-Sep-2024")):
            with self.subTest(change=change),self.assertRaises(EvidenceError):
                parse([dict(base,**change)])
        other=dict(base,xbrl=base["xbrl"].replace("123","124"))
        self.assertEqual(len(parse([base,other])["references"]),2)
