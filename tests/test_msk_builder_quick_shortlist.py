"""Offline proof of simple five-per-category, no invented stock selection."""
from datetime import date
import unittest

from msk_builder_quick_shortlist import (
    BUCKETS, ShortlistUnavailable, parse_membership,
    fetch_memberships, shortlist_index_ideas,
)

SESSION = date(2026, 11, 23)


def raw_index(tier):
    tag = {"large": "L", "mid": "M", "small": "S"}[tier]
    offset = {"large": 0, "mid": 50, "small": 200}[tier]
    rows = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for n in range(BUCKETS[tier][1]):
        s = f"{tag}{n:03d}"
        rows.append(f"Test Company {s},Fictional Sector,{s},EQ,INE{offset+n:09d}")
    return ("\n".join(rows)+"\n").encode()


def record(sym, isin):
    return {
        "symbol": sym, "isin": isin, "close_date": SESSION.isoformat(),
        "close_rupees": "125.50",
        "evidence_status": "ISSUER_AUTHENTICATED",
        "price_status": "EXCHANGE_VERIFIED",
        "valuation_status": "VERIFIED", "risk_status": "VERIFIED",
        "corporate_actions_status": "VERIFIED",
        "issuer_identity_verified": True, "verified_annual_periods": 5,
        "verified_quarter_periods": 8, "filing_sha256": "a"*64,
    }


class QuickShortlistTests(unittest.TestCase):
    def inputs(self):
        snapshots = fetch_memberships(
            session_date=SESSION,
            fetch=lambda url: next(
                raw_index(tier) for tier in BUCKETS
                if url.endswith(BUCKETS[tier][0])))
        reviewed, ideas = {}, {}
        for tier, snap in snapshots.items():
            symbols = list(snap.members)[:8]
            ideas[tier] = symbols
            for s in symbols:
                reviewed[s] = record(s, snap.members[s])
        return snapshots, reviewed, ideas

    def test_official_memberships_have_exact_nse_counts_and_source_hash(self):
        snapshots, _, _ = self.inputs()
        self.assertEqual([len(s.members) for s in snapshots.values()], [50,150,250])
        self.assertTrue(all(len(s.sha256) == 64 for s in snapshots.values()))
        self.assertTrue(all(s.source_url.startswith(
            "https://nsearchives.nseindia.com/content/indices/") for s in snapshots.values()))

    def test_fast_five_each_from_three_groups_not_customer_publication(self):
        snapshots, reviewed, ideas = self.inputs()
        result = shortlist_index_ideas(
            snapshots, ideas, reviewed, close_date=SESSION)
        self.assertEqual(result["status"], "RESEARCH_SHORTLIST_READY_FOR_RELEASE_REVIEW")
        self.assertEqual({k:len(v) for k,v in result["shortlists"].items()},
                         {"large":5,"mid":5,"small":5})
        self.assertEqual(result["publication_status"],"WITHHELD")
        self.assertEqual(result["close_date"], SESSION.isoformat())

    def test_failed_company_skipped_and_next_verified_candidate_used(self):
        snapshots, reviewed, ideas = self.inputs()
        missing = ideas["small"][1]
        reviewed[missing]["price_status"] = "UNVERIFIED"
        result = shortlist_index_ideas(
            snapshots, ideas, reviewed, close_date=SESSION)
        self.assertEqual(len(result["shortlists"]["small"]),5)
        self.assertNotIn(missing,result["shortlists"]["small"])
        self.assertEqual(result["skipped"]["small"][missing],
                         "FINANCIAL_EVIDENCE_NOT_APPROVED")
        self.assertEqual(result["shortlists"]["small"][-1],ideas["small"][5])

    def test_malformed_or_missing_evidence_fails_closed(self):
        for field, bad in (
            ("filing_sha256","tampered"),("close_rupees","NaN"),
            ("verified_annual_periods",4),("verified_quarter_periods",7),
            ("issuer_identity_verified",False),
            ("valuation_status","PENDING"),("risk_status","PENDING"),
            ("close_date","2026-11-20")):
            snapshots, reviewed, ideas=self.inputs()
            symbol=ideas["large"][0]
            reviewed[symbol][field]=bad
            result=shortlist_index_ideas(
                snapshots,ideas,reviewed,close_date=SESSION)
            with self.subTest(field=field):
                self.assertNotIn(symbol,result["shortlists"]["large"])
                self.assertEqual(result["publication_status"],"WITHHELD")

    def test_unsupported_index_and_cross_bucket_ticker_are_rejected(self):
        snapshots, reviewed, ideas = self.inputs()
        ideas["small"].insert(0, ideas["large"][0])
        result=shortlist_index_ideas(snapshots,ideas,reviewed,close_date=SESSION)
        self.assertEqual(
            result["skipped"]["small"][ideas["large"][0]],"NOT_IN_OFFICIAL_INDEX")
        self.assertNotIn(ideas["large"][0],result["shortlists"]["small"])

    def test_partial_is_research_incomplete_not_fake_five_stocks(self):
        snapshots, reviewed, ideas = self.inputs()
        ideas["mid"] = ideas["mid"][:2]
        result = shortlist_index_ideas(
            snapshots, ideas, reviewed, close_date=SESSION)
        self.assertEqual(result["status"],"RESEARCH_SHORTLIST_INCOMPLETE")
        self.assertEqual(len(result["shortlists"]["mid"]),2)
        self.assertEqual(result["publication_status"],"WITHHELD")

    def test_index_csv_rejects_truncation_duplicates_and_wrong_size(self):
        for tier in BUCKETS:
            raw=raw_index(tier)
            self.assertEqual(len(parse_membership(raw,tier,SESSION).members),
                             BUCKETS[tier][1])
            with self.subTest(tier=tier),self.assertRaises(ShortlistUnavailable):
                parse_membership(b"Symbol,ISIN Code\nX,INE000000000\n",
                                 tier,SESSION)
            with self.assertRaises(ShortlistUnavailable):
                parse_membership(raw.replace(b",EQ,",b",BE,"),tier,SESSION)
        with self.assertRaises(ShortlistUnavailable):
            parse_membership(raw_index("large")*3,"large",SESSION)

    def test_stale_membership_and_unverified_replacement_never_succeed(self):
        snaps, reviewed, ideas=self.inputs()
        with self.assertRaisesRegex(ShortlistUnavailable,"INDEX_SESSION_INVALID"):
            shortlist_index_ideas(snaps,ideas,reviewed,close_date=date(2026,11,24))
        for s in ideas["small"]:
            reviewed.pop(s)
        result=shortlist_index_ideas(snaps,ideas,reviewed,close_date=SESSION)
        self.assertEqual(result["shortlists"]["small"],[])
        self.assertEqual(result["status"],"RESEARCH_SHORTLIST_INCOMPLETE")
        self.assertEqual(result["publication_status"],"WITHHELD")


if __name__=="__main__":
    unittest.main()
