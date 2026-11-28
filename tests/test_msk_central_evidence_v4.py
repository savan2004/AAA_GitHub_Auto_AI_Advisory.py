import unittest
from datetime import date
from decimal import Decimal
from dataclasses import replace
from msk_central_data_v4 import Dataset
from msk_central_evidence_v4 import EvidenceKey, Receipt, resolve, parse_ai_leads

DAY = date(2026, 9, 30)
ASOF = date(2026, 11, 28)
URL = "https://nsearchives.nseindia.com/corporate/test.xml"
KEY = EvidenceKey("TEST", Dataset.RESULTS, "net_profit", DAY, "consolidated",
                  "INR_CRORE", date(2026, 7, 1), "INE000A01000")
REC = Receipt(KEY, Decimal("12.5"), URL, "a"*64, date(2026, 10, 25), "net_profit:row1")


class EvidenceTests(unittest.TestCase):
    def run_resolve(self, records=(), providers=(), **kwargs):
        return resolve(KEY, as_of=ASOF, retrieve=lambda _: records,
                       providers=providers, verify=kwargs.pop("verify", lambda *_: True), **kwargs)

    def test_exact_verified_retrieval(self):
        self.assertEqual(self.run_resolve([REC]).value, Decimal("12.5"))

    def test_wrong_identity_scope_period_or_unit(self):
        for key in (replace(KEY, isin="INE000B01000"), replace(KEY, scope="standalone"),
                    replace(KEY, period_start=date(2026, 4, 1)), replace(KEY, unit="INR")):
            self.assertIsNone(self.run_resolve([replace(REC, key=key)]).value)

    def test_wrong_future_or_nonfinite(self):
        for rec in (replace(REC, published=date(2026, 12, 1)),
                    replace(REC, value=Decimal("NaN")), replace(REC, document_sha256="bad")):
            self.assertEqual(self.run_resolve([rec]).status, "withheld_missing")

    def test_conflict_not_averaged(self):
        r = self.run_resolve([REC], [lambda _: [replace(REC, value=Decimal("13"))]])
        self.assertEqual(r.status, "withheld_conflict")
        self.assertIsNone(r.value)

    def test_provider_failure_recovery(self):
        def fail(_):
            raise TimeoutError()
        self.assertEqual(self.run_resolve([], [fail, lambda _: [REC]]).value, REC.value)

    def test_independent_verifier_required(self):
        self.assertIsNone(self.run_resolve([REC], verify=lambda *_: False).value)
        self.assertIsNone(self.run_resolve([REC], verify=lambda *_: 1).value)

    def test_ai_lead_cannot_supply_value(self):
        self.assertEqual(parse_ai_leads({"source_urls": [URL], "value": 42}), ())
        self.assertEqual(parse_ai_leads({"source_urls": ["https://nsearchives.nseindia.com.evil.test/x"]}), ())
        self.assertEqual(parse_ai_leads({"source_urls": ["https://user@nsearchives.nseindia.com/x"]}), ())
        self.assertEqual(parse_ai_leads({"source_urls": [URL]*4}), ())
        self.assertEqual(parse_ai_leads({"source_urls": [URL, URL]}), (URL,))

    def test_ai_lead_reverified(self):
        options = dict(discover=lambda _: {"source_urls": [URL]}, fetch_lead=lambda *_: [REC])
        self.assertEqual(self.run_resolve(**options).value, REC.value)
        self.assertIsNone(self.run_resolve(**options, verify=lambda *_: False).value)

    def test_no_ai_on_conflict_or_success(self):
        called = []
        options = dict(discover=lambda _: called.append(True), fetch_lead=lambda *_: [])
        self.run_resolve([REC], **options)
        self.run_resolve([REC, replace(REC, value=Decimal("20"))], **options)
        self.assertEqual(called, [])

    def test_candidate_limit_withholds(self):
        self.assertEqual(self.run_resolve([REC]*13).status, "withheld_candidate_limit")

    def test_provider_budget(self):
        calls = []
        def provider(_):
            calls.append(1)
            return []
        self.run_resolve([], [provider]*10)
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
