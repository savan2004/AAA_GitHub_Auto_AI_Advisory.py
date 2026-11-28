import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from threading import Event, Thread
from msk_central_data_v4 import Dataset, build_universe
from msk_central_evidence_v4 import EvidenceKey, Receipt, Resolution, resolve
from msk_central_reliability_v4 import EvidenceGateway, CircuitBreaker, ProviderUnavailable
from msk_central_nse_shadow_v4 import collect_candidates

DAY = date(2026, 11, 27)
KEY = EvidenceKey("TEST", Dataset.DAILY_OHLCV, "close", DAY, "raw", "INR", DAY, "INE000A01000")
URL = "https://nsearchives.nseindia.com/test.csv"
REC = Receipt(KEY, Decimal("100"), URL, "a"*64, DAY, "row 1")
OK = Resolution("verified", (REC,))


class ReliabilityTests(unittest.TestCase):
    def test_cache_hit_expiry_revision_and_invalidation(self):
        now = [0]
        gateway = EvidenceGateway(ttl=10, clock=lambda: now[0])
        calls = []
        def read(revision="v1"):
            return gateway.get(KEY, as_of=DAY, revision=revision, loader=lambda: (calls.append(1), OK)[1])
        read(); read()
        self.assertEqual(len(calls), 1)
        now[0] = 10
        read(); read("v2")
        gateway.invalidate(KEY)
        read("v2")
        self.assertEqual(len(calls), 4)

    def test_no_cache_of_failure_or_wrong_receipt(self):
        gateway = EvidenceGateway()
        for result in (Resolution("withheld_conflict"), Resolution("verified", (replace(REC, key=replace(KEY, field="open")),))):
            got = gateway.get(KEY, as_of=DAY, revision="r", loader=lambda: result)
            self.assertIsNone(got.value)
        self.assertEqual(gateway.get(KEY, as_of=DAY, revision="r", loader=lambda: OK).value, REC.value)

    def test_singleflight_and_invalidation(self):
        gateway = EvidenceGateway(wait_timeout=2)
        entered, release = Event(), Event()
        results = []
        def load():
            entered.set()
            release.wait(2)
            return OK
        worker = Thread(target=lambda: results.append(gateway.get(KEY, as_of=DAY, revision="r", loader=load)))
        worker.start()
        self.assertTrue(entered.wait(1))
        gateway.invalidate(KEY)
        release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status, "withheld_revision_changed")
        self.assertEqual(gateway.get(KEY, as_of=DAY, revision="r", loader=lambda: OK).value, REC.value)

    def test_coalesces_inflight(self):
        gateway = EvidenceGateway(wait_timeout=2)
        entered, release = Event(), Event()
        calls, results = [], []
        def load():
            calls.append(1); entered.set(); release.wait(2); return OK
        def read():
            results.append(gateway.get(KEY, as_of=DAY, revision="r", loader=load))
        a,b = Thread(target=read), Thread(target=read)
        a.start(); self.assertTrue(entered.wait(1)); b.start()
        release.set(); a.join(2); b.join(2)
        self.assertEqual(calls, [1])
        self.assertEqual([x.value for x in results], [REC.value, REC.value])

    def test_breaker_recovery(self):
        now = [0]
        cb = CircuitBreaker(threshold=2, cooldown=10, clock=lambda: now[0])
        def fail():
            raise TimeoutError()
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                cb.call(fail)
        with self.assertRaises(ProviderUnavailable):
            cb.call(lambda: 1)
        now[0] = 10
        self.assertEqual(cb.call(lambda: 2), 2)
        self.assertEqual(cb.call(lambda: 3), 3)

    def test_ai_cannot_substitute_other_document(self):
        result = resolve(KEY, as_of=DAY, retrieve=lambda _: [], providers=[],
            verify=lambda *_: True, discover=lambda _: {"source_urls": [URL]},
            fetch_lead=lambda *_: [replace(REC, source_url=URL.replace("test", "other"))])
        self.assertEqual(result.status, "withheld_missing")

    def test_independent_verifier_failure_does_not_hide_next_candidate(self):
        def verify(r, k):
            if r.locator == "bad":
                raise ValueError()
            return True
        result = resolve(KEY, as_of=DAY, retrieve=lambda _: [replace(REC, locator="bad"), REC],
                         providers=[], verify=verify)
        self.assertEqual(result.value, REC.value)

    def test_archive_one_call_missing_and_bad_volume(self):
        u = build_universe([f"CORE{i}" for i in range(500)], ["EXTRA"])
        calls = []
        def fetch(day, symbols):
            calls.append((day, symbols))
            good = dict(Open=100,High=110,Low=90,Close=105,Volume=100)
            return {"CORE0.NS": good, "CORE1.NS": dict(good, Volume=1.5),
                    "EXTRA.NS": good}
        candidates, missing = collect_candidates(u, DAY, as_of=DAY,
            is_completed_session=lambda _: True, fetch=fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][1]), 500)
        self.assertEqual(set(candidates), {"CORE0"})
        self.assertEqual(len(missing), 499)
        self.assertEqual(candidates["CORE0"].close, Decimal("105"))

    def test_no_fetch_when_session_not_completed(self):
        u = build_universe([f"CORE{i}" for i in range(500)])
        got, missing = collect_candidates(u, DAY, as_of=DAY,
            is_completed_session=lambda _: False, fetch=lambda *_: self.fail("unexpected fetch"))
        self.assertEqual(got, {})
        self.assertEqual(len(missing), 500)


if __name__ == "__main__":
    unittest.main()
