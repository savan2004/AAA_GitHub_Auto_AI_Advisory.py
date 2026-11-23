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
