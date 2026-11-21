"""Synthetic fixtures only; no live data, real issuers, or advice."""
import unittest
from datetime import datetime, timezone
from msk_ai_builder_evidence import EvidenceError, collect_secondary, normalize_secondary

NOW = datetime(2026, 11, 21, 8, tzinfo=timezone.utc)

def fixture():
    def row(label, n):
        return {"label": label, "revenue": 100 + 10*n, "net_profit": 10+n}
    return {
        "_source": "Screener.in",
        "_statement_scope": "consolidated",
        "_source_url": "https://www.screener.in/company/FIXTURE/consolidated/",
        "_retrieved_utc": "2026-11-21T07:00:00Z",
        "years": [row(f"Mar {y}", i) for i,y in enumerate(range(2022,2027))],
        "quarters": [row(label,i) for i,label in enumerate(
            ["Dec 2024","Mar 2025","Jun 2025","Sep 2025",
             "Dec 2025","Mar 2026","Jun 2026","Sep 2026"])],
    }

class SecondaryTests(unittest.TestCase):
    def validate(self, data):
        return normalize_secondary("FIXTURE", data, now=NOW)

    def test_complete_but_never_selectable(self):
        snapshot = self.validate(fixture())
        self.assertEqual(len(snapshot.annual),5)
        self.assertEqual(len(snapshot.quarterly),8)
        self.assertEqual(snapshot.revenue_cagr_pct,8.78)
        self.assertEqual(snapshot.profit_cagr_pct,8.78)
        self.assertEqual(snapshot.publication_status,"SECONDARY_ONLY")
        self.assertFalse(snapshot.eligible_for_selection)

    def test_history_order_normalized(self):
        data = fixture()
        data["years"].reverse()
        self.assertEqual(self.validate(data).annual[0].end.year,2022)

    def test_missing_duplicates_and_gaps(self):
        for transform in (
            lambda d: d["years"].pop(),
            lambda d: d["quarters"].append(d["quarters"][0]),
            lambda d: d["years"][0].update(label="Mar 2021"),
            lambda d: d["quarters"][-1].update(label="Dec 2026"),
            lambda d: d["quarters"][0].update(label="TTM"),
        ):
            d = fixture()
            transform(d)
            with self.assertRaises(EvidenceError):
                self.validate(d)

    def test_bad_numeric_fields(self):
        for value in (None, True, "100", float("nan"), float("inf"), -1, 0):
            d = fixture()
            d["years"][-1]["revenue"] = value
            with self.subTest(value=value), self.assertRaises(EvidenceError):
                self.validate(d)

    def test_losses_are_not_fabricated_into_cagr(self):
        d = fixture()
        d["years"][-1]["net_profit"] = -2
        self.assertIsNone(self.validate(d).profit_cagr_pct)

    def test_provenance_and_retrieval(self):
        for key,value in (
            ("_source","unknown"), ("_statement_scope","unknown"),
            ("_source_url","https://example.org"),
            ("_retrieved_utc","2026-11-19T07:00:00Z"),
            ("_retrieved_utc","2026-11-21T09:00:00Z"),
            ("_retrieved_utc","2026-11-21T07:00:00"),
        ):
            d = fixture()
            d[key] = value
            with self.subTest(key=key,value=value), self.assertRaises(EvidenceError):
                self.validate(d)

    def test_stale_annual_and_quarterly(self):
        d = fixture()
        for i,row in enumerate(d["years"]):
            row["label"] = f"Mar {2021+i}"
        with self.assertRaisesRegex(EvidenceError,"STALE_ANNUAL_HISTORY"):
            self.validate(d)
        d = fixture()
        d["quarters"] = [{"label":"Sep 2024","revenue":90,"net_profit":9}] + d["quarters"][:-1]
        with self.assertRaisesRegex(EvidenceError,"STALE_QUARTERLY_HISTORY"):
            normalize_secondary("FIXTURE", {**d, "_retrieved_utc": "2026-11-30T07:00:00Z"}, now=NOW.replace(day=30))

    def test_adapter_invokes_existing_contract_and_fails_closed(self):
        calls = []
        def provider(symbol, request_timeout):
            calls.append((symbol,request_timeout))
            return fixture(), {"roe":99}
        self.assertFalse(collect_secondary("FIXTURE",collector=provider,now=NOW).eligible_for_selection)
        self.assertEqual(calls,[("FIXTURE",9)])
        def broken(*args,**kwargs):
            raise TimeoutError()
        with self.assertRaisesRegex(EvidenceError,"PROVIDER_UNAVAILABLE"):
            collect_secondary("FIXTURE",collector=broken,now=NOW)
