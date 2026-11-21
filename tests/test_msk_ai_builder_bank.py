import json
import os
import unittest
from dataclasses import replace
from datetime import timedelta,date
from decimal import Decimal
from msk_ai_builder_primary import IndexMember
from msk_ai_builder_evidence import normalize_secondary
from msk_ai_builder_bank import (
    BankError, StagedCompany, WeeklyResearchBank, encode_snapshot, build_weekly_bank
)
from test_msk_ai_builder_evidence import fixture,NOW

CLOSE=date(2026,11,20)
def company(symbol="FIXTURE",index=1,now=NOW,close=CLOSE):
    data=fixture()
    data["_source_url"]=f"https://www.screener.in/company/{symbol}/consolidated/"
    data["_retrieved_utc"]=(now-timedelta(hours=1)).isoformat()
    return StagedCompany(IndexMember(symbol,f"INE{index:09}"),"Fictional sector",
        "Fictional company "+symbol,normalize_secondary(symbol,data,now=now),
        close,Decimal("123.45"),"https://www.nseindia.com/fictional-test-reference")

def encode(rows,reviews=None,now=NOW,close=CLOSE):
    return encode_snapshot(rows,reviewed_at=now,close_date=close,
                           reviews={} if reviews is None else reviews,now=now)

class BankContractTests(unittest.TestCase):
    def test_canonical_order_and_publication_block(self):
        a,b=company(),company("TESTB",2)
        version,raw=encode([a,b])
        self.assertEqual((version,raw),encode([b,a]))
        data=json.loads(raw)
        self.assertEqual(len(version),64)
        self.assertEqual(data["publication_status"],"WITHHELD")
        self.assertEqual(data["companies"][0]["evidence_status"],"SECONDARY_ONLY")
        self.assertEqual(data["companies"][0]["price_status"],"SOURCE_REFERENCE_NOT_RECONCILED")
        self.assertEqual(len(data["companies"][0]["annual"]),5)
        self.assertEqual(len(data["companies"][0]["quarterly"]),8)
    def test_limit_and_duplicate_identity(self):
        rows=[company(f"TEST{i:02}",i+1) for i in range(21)]
        self.assertEqual(len(json.loads(encode(rows[:20])[1])["companies"]),20)
        for bad in (rows,[],[rows[0],rows[0]], [rows[0],replace(rows[1],member=IndexMember("OTHER",rows[0].member.isin))]):
            with self.assertRaises(BankError):
                encode(bad)
    def test_bad_price_and_scope_and_snapshot_dates(self):
        a=company()
        for b in (replace(a,close_rupees=Decimal("NaN")),
                  replace(a,close_rupees=0),
                  replace(a,price_source_url="https://example.com/fake"),
                  replace(a,close_date=CLOSE-timedelta(days=1)),
                  replace(a,evidence=replace(a.evidence,retrieved_at=NOW-timedelta(days=2))),
                  replace(a,evidence=replace(a.evidence,annual=a.evidence.annual[:-1]))):
            with self.assertRaises(BankError):
                encode([b])
        with self.assertRaises(BankError):
            encode([a],close=CLOSE-timedelta(days=10))
    def test_review_reasons(self):
        a=company()
        for bad in ({"FIXTURE":"removed: weak"}, {"OTHER":"retained"}, {"FIXTURE":"unknown"}, {"FIXTURE":"removed: "}):
            with self.assertRaises(BankError):
                encode([a],bad)
        self.assertEqual(json.loads(encode([a],{"FIXTURE":"retained","OLD":"removed: evidence missing"})[1])["reviews"]["FIXTURE"],"retained")
    def test_disabled_default_and_separate_dsn(self):
        self.assertIsNone(build_weekly_bank(environ={"DATABASE_URL":"not-used"}))
        with self.assertRaises(BankError):
            build_weekly_bank(environ={"MSK_BUILDER_BANK_ENABLED":"true"})
        with self.assertRaises(BankError):
            build_weekly_bank(environ={"MSK_BUILDER_BANK_ENABLED":"maybe"})

@unittest.skipUnless(os.getenv("MSK_BUILDER_TEST_POSTGRES"),"Requires disposable CI PostgreSQL service")
class PostgresBankTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.connect=lambda _:psycopg.connect(os.environ["MSK_BUILDER_TEST_POSTGRES"])
        with self.connect(None) as conn:
            conn.execute("DROP SCHEMA IF EXISTS msk_builder_research CASCADE")
        self.bank=WeeklyResearchBank("postgresql://test@localhost/test?sslmode=require",connector=self.connect)
    def test_atomic_history_stale_and_all_previous_reviews(self):
        b=self.bank
        with self.assertRaisesRegex(BankError,"BANK_EMPTY"):
            b.read_staging(now=NOW)
        kw=dict(reviewed_at=NOW,close_date=CLOSE,reviews={},expected_version=None,now=NOW)
        v=b.replace([company(),company("OLD",2)],**kw)
        self.assertEqual(b.replace([company(),company("OLD",2)],**kw),v)
        self.assertEqual(b.read_staging(now=NOW)[0],v)
        later=NOW+timedelta(days=7)
        close=CLOSE+timedelta(days=7)
        rows=[company(now=later,close=close),company("NEW",3,now=later,close=close)]
        kw2=dict(reviewed_at=later,close_date=close,now=later,reviews={"FIXTURE":"retained","OLD":"removed: missing evidence"},expected_version=v)
        with self.assertRaisesRegex(BankError,"PREVIOUS_COMPANY_REVIEW_INCOMPLETE"):
            b.replace(rows,**{**kw2,"reviews":{"FIXTURE":"retained"}})
        with self.assertRaisesRegex(BankError,"VERSION_CONFLICT"):
            b.replace(rows,**{**kw2,"expected_version":None})
        self.assertEqual(b.read_staging(now=later)[0],v)
        v2=b.replace(rows,**kw2)
        self.assertNotEqual(v,v2)
        self.assertEqual(b.read_staging(now=later)[0],v2)
        with self.connect(None) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM msk_builder_research.snapshots").fetchone()[0],2)
        with self.assertRaisesRegex(BankError,"BANK_STALE"):
            b.read_staging(now=later+timedelta(days=9))
        with self.assertRaisesRegex(BankError,"PRODUCTION_PUBLICATION_DISABLED"):
            b.read_for_customer()
    def test_corrupt_snapshot_rejected(self):
        v=self.bank.replace([company()],reviewed_at=NOW,close_date=CLOSE,
                            reviews={},expected_version=None,now=NOW)
        with self.connect(None) as conn:
            conn.execute("UPDATE msk_builder_research.snapshots SET payload='{}' WHERE version=%s",(v,))
        with self.assertRaisesRegex(BankError,"SNAPSHOT_CORRUPT"):
            self.bank.read_staging(now=NOW)
