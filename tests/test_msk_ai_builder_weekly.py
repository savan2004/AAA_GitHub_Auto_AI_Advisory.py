import unittest
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock,patch

from msk_ai_builder_bank import BankError
from msk_ai_builder_evidence import EvidenceError,normalize_secondary
from msk_ai_builder_weekly_sources import parse_roster,parse_closes,parse_final_closes,fetch_closes,CLOSE_CSV,FINAL_ZIP
from msk_ai_builder_weekly import plan_review,stage_week,preliminary_score
from test_msk_ai_builder_evidence import fixture,NOW

CLOSE=date(2026,11,20)

def roster_csv():
    return "Company Name,Industry,Symbol,Series,ISIN Code\n"+"\n".join(
        f"Fictional Company {i},Sector {i%10},TEST{i:03},EQ,INE{i:09}" for i in range(500))

def close_csv():
    return "SYMBOL,SERIES,DATE1,CLOSE_PRICE,TURNOVER_LACS\n"+"\n".join(
        f"TEST{i:03},EQ,20-Nov-2026,123.45,{1000-i}" for i in range(500))

def inputs():
    roster=parse_roster(roster_csv())
    return roster,parse_closes(close_csv(),CLOSE,roster)

def provider(symbol,timeout):
    d=fixture()
    d["_source_url"]=f"https://www.screener.in/company/{symbol}/consolidated/"
    return normalize_secondary(symbol,d,now=NOW)

class SourceTests(unittest.TestCase):
    def test_official_501_members_retained_and_accounted_for(self):
        raw = roster_csv() + "\nAdditional Company,Other,ADDITIONAL,EQ,INE999999999"
        roster = parse_roster(raw)
        self.assertEqual(len(roster), 501)
        closes = parse_closes(close_csv(), CLOSE, roster)
        result = plan_review(roster, closes, close_date=CLOSE, now=NOW,
                             evidence_provider=provider)
        self.assertEqual(result.universe_size, 501)
        for invalid in (raw + "\n" + raw.splitlines()[-1],
                        raw.replace("INE999999999", "INE000000000"),
                        raw + "\nAnother Company,Other,ANOTHER,EQ,INE999999998"):
            with self.assertRaises(EvidenceError):
                parse_roster(invalid)

    def test_official_final_archive_identity_and_turnover(self):
        import io
        import zipfile
        roster=parse_roster(roster_csv())
        raw="TckrSymb,SctySrs,TradDt,ISIN,Sgmt,ClsPric,TtlTrfVal\n"+"\n".join(
            f"TEST{i:03},EQ,2026-11-20,INE{i:09},CM,123.45,{(1000-i)*100000}" for i in range(500))
        def pack(body):
            out=io.BytesIO()
            with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
                z.writestr("final.csv",body)
            return out.getvalue()
        payload=pack(raw)
        rows=parse_final_closes(payload,CLOSE,roster)
        self.assertEqual(len(rows),500)
        self.assertEqual(rows["TEST001"].turnover_lakh,Decimal(999))
        self.assertEqual(rows["TEST001"].price,Decimal("123.45"))
        self.assertEqual(rows["TEST001"].source,FINAL_ZIP.format(d="20261120"))
        with patch("msk_ai_builder_weekly_sources._download",
                   side_effect=[EvidenceError("OFFICIAL_SOURCE_HTTP"),payload]) as fetch:
            self.assertEqual(fetch_closes(CLOSE,roster),rows)
            self.assertTrue(fetch.call_args.kwargs["binary"])
        for bad in (raw.replace("INE000000001","INE000000000"),
                    raw.replace("2026-11-20","2026-11-19"),
                    raw+"\n"+raw.splitlines()[1],
                    "\n".join(raw.splitlines()[:400])):
            with self.assertRaises(EvidenceError):
                parse_final_closes(pack(bad),CLOSE,roster)
        with self.assertRaises(EvidenceError):
            parse_final_closes(b"bad zip",CLOSE,roster)
        with patch("msk_ai_builder_weekly_sources._download",return_value="bad CSV") as fetch:
            with self.assertRaises(EvidenceError):
                fetch_closes(CLOSE,roster)
            self.assertEqual(fetch.call_count,1)

    def test_roster_and_close_schema(self):
        roster,prices=inputs()
        self.assertEqual(len(roster),500)
        self.assertEqual(len(prices),500)
        self.assertEqual(prices["TEST001"].price,Decimal("123.45"))
        self.assertEqual(prices["TEST001"].source,CLOSE_CSV.format(d="20112026"))
        for bad in (roster_csv().replace("ISIN Code","ISIN"),
                    "\n".join(roster_csv().splitlines()[:-1]),
                    roster_csv().replace("TEST499","TEST000")):
            with self.assertRaises(EvidenceError):
                parse_roster(bad)
        for bad in (close_csv().replace("20-Nov-2026","19-Nov-2026"),
                    close_csv().replace("123.45","NaN"),close_csv().replace("TEST499","TEST000"),
                    "\n".join(close_csv().splitlines()[:400])):
            with self.assertRaises(EvidenceError):
                parse_closes(bad,CLOSE,roster)

    def test_live_roster_variations(self):
        raw=roster_csv().replace("TEST001,EQ,","TEST001,RR,").replace("TEST002,EQ,","TEST002,BE,")
        raw += "\nDummy HEG Ltd.,Capital Goods,DUMMYHEG,EQ,DUM545A01024"
        roster=parse_roster(raw)
        self.assertEqual(len(roster),500)
        self.assertEqual(sum(x.series=="RR" for x in roster),1)
        self.assertEqual(sum(x.series=="BE" for x in roster),1)
        closes=parse_closes(close_csv(),CLOSE,roster)
        self.assertEqual(len(closes),498)
        self.assertNotIn("TEST001",closes)
        self.assertNotIn("TEST002",closes)
        with self.assertRaises(EvidenceError):
            parse_roster(raw.replace("DUM545A01024","DUM545A01025"))

class WeeklyTests(unittest.TestCase):
    def plan(self,previous=(),collector=provider,**kw):
        roster,closes=inputs()
        return plan_review(roster,closes,previous=previous,close_date=CLOSE,now=NOW,
                           evidence_provider=collector,max_attempts=40,clock=lambda:0,**kw)
    def test_initial_bounded_and_diversified(self):
        r=self.plan()
        self.assertEqual(r.universe_size,500)
        self.assertEqual(r.close_coverage,500)
        self.assertEqual(len(r.attempted),40)
        self.assertEqual(len(r.scored),40)
        self.assertEqual(len(r.companies),20)
        self.assertEqual(r.status,"INTERNAL_SECONDARY_STAGING")
        self.assertTrue(all(not c.evidence.eligible_for_selection for c in r.companies))
        self.assertTrue(all(sum(x.sector==c.sector for x in r.companies)<=5 for c in r.companies))
    @staticmethod
    def quarter_observation(member,evidence):
        return {"symbol":member.symbol,"isin":member.isin,
                "period":evidence.quarterly[-1].end.isoformat(),
                "scope":evidence.scope,"status":"LATEST_QUARTER_MATCHED_ONLY",
                "publication_status":"WITHHELD"}

    def test_primary_failure_replaced_by_next_ranked_reserve_without_rechecking(self):
        baseline=self.plan()
        original=[c.member.symbol for c in baseline.companies]
        failed=original[4]
        calls=[]
        def verify(member,evidence):
            calls.append(member.symbol)
            if member.symbol==failed:
                return {"symbol":failed,"status":"FILING_METADATA_COVERAGE",
                        "publication_status":"WITHHELD"}
            return self.quarter_observation(member,evidence)
        r=self.plan(quarter_verifier=verify)
        chosen=[c.member.symbol for c in r.companies]
        self.assertEqual(calls[:20],original)
        self.assertEqual(len(calls),21)
        self.assertEqual(len(set(calls)),21)
        self.assertEqual(set(chosen)-set(original),{baseline.reserves[0]})
        self.assertEqual(set(original)-set(chosen),{failed})
        self.assertEqual(len(r.primary_checks),20)
        self.assertEqual(r.primary_rejections[0]["symbol"],failed)
        self.assertEqual(r.status,"INTERNAL_SECONDARY_STAGING")
        self.assertTrue(all(sum(x.sector==c.sector for x in r.companies)<=5 for c in r.companies))

    def test_primary_reserve_exhaustion_does_not_store_partial_snapshot(self):
        roster,closes=inputs()
        bank=Mock()
        bank.read_for_review.side_effect=BankError("BANK_EMPTY")
        verifier=Mock(return_value={"symbol":"wrong","status":"LATEST_QUARTER_MATCHED_ONLY"})
        with patch.dict("sys.modules",{"portfolio_doctor_live":SimpleNamespace(expected_doctor_session=lambda now:CLOSE)}):
            r,version=stage_week(bank,now=NOW,roster_provider=lambda:roster,
                close_provider=lambda day,rows:closes,evidence_provider=provider,
                quarter_verifier=verifier)
        self.assertEqual(verifier.call_count,30)
        self.assertEqual(r.status,"WITHHELD_PRIMARY_COVERAGE")
        self.assertEqual(len(r.primary_checks),0)
        self.assertIsNone(version)
        bank.replace.assert_not_called()

    def test_incumbents_first_and_no_churn(self):
        previous=tuple(f"TEST{i:03}" for i in range(480,500))
        r=self.plan(previous)
        self.assertEqual(r.attempted[:20],previous)
        self.assertEqual(set(r.reviews),set(previous))
        self.assertEqual(set(r.reviews.values()),{"retained"})
        self.assertEqual({c.member.symbol for c in r.companies},set(previous))
    def test_transient_failure_withholds_entire_refresh(self):
        def broken(symbol,timeout):
            if symbol=="TEST499":
                raise TimeoutError()
            return provider(symbol,timeout)
        r=self.plan(("TEST499",),broken)
        self.assertEqual(r.status,"WITHHELD_INCOMPLETE_INCUMBENT_REVIEW")
        self.assertEqual(r.companies,())
        self.assertIn("TEST499",r.failures)
    def test_weak_company_removed_with_reason(self):
        def weak(symbol,timeout):
            e=provider(symbol,timeout)
            if symbol=="TEST499":
                e=replace(e,annual=e.annual[:-1]+(replace(e.annual[-1],profit_cr=-10),))
            return e
        self.assertEqual(preliminary_score(weak("TEST499",9)),0)
        r=self.plan(("TEST499",),weak)
        self.assertEqual(r.reviews["TEST499"],"removed: below preliminary financial screen")
        self.assertNotIn("TEST499",{c.member.symbol for c in r.companies})
    def test_all_fail_never_force_twenty(self):
        def bad(symbol,timeout):
            raise EvidenceError("MISSING_DATA")
        r=self.plan((),bad)
        self.assertEqual(r.status,"WITHHELD_NO_QUALIFIERS")
        self.assertEqual(r.companies,())
        self.assertEqual(len(r.failures),40)

    def test_storage_orchestration_contract(self):
        roster,closes=inputs()
        bank=Mock()
        bank.read_for_review.side_effect=BankError("BANK_EMPTY")
        bank.replace.return_value="new-version"
        with patch.dict("sys.modules",{"portfolio_doctor_live":SimpleNamespace(expected_doctor_session=lambda now:CLOSE)}):
            result,version=stage_week(bank,now=NOW,roster_provider=lambda:roster,
                close_provider=lambda day,rows:closes,evidence_provider=provider)
        self.assertEqual(version,"new-version")
        self.assertEqual(len(result.companies),20)
        self.assertIsNone(bank.replace.call_args.kwargs["expected_version"])
        self.assertEqual(bank.replace.call_args.kwargs["reviews"],{})
        bank.reset_mock()
        bank.read_for_review.side_effect=None
        bank.read_for_review.return_value=("old",{"companies":[{"symbol":"TEST499","isin":"INE000000499"}]})
        with patch.dict("sys.modules",{"portfolio_doctor_live":SimpleNamespace(expected_doctor_session=lambda now:CLOSE)}):
            result,version=stage_week(bank,now=NOW,roster_provider=lambda:roster,
                close_provider=lambda day,rows:closes,evidence_provider=lambda *a,**k:None)
        self.assertIsNone(version)
        bank.replace.assert_not_called()
