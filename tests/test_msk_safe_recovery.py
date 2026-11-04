import unittest
from tools.msk_safe_recovery import CircuitBreaker, RecoveryExhausted, retry_read_only, validate_market_snapshot

class SafeRecoveryTests(unittest.TestCase):
    def test_transient_read_recovery(self):
        calls = []
        def read():
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError()
            return {"ok": True}
        self.assertEqual(retry_read_only(read, sleep=lambda _: None), {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_permanent_error_not_retried(self):
        calls = []
        def read():
            calls.append(1)
            raise PermissionError("403")
        with self.assertRaises(PermissionError):
            retry_read_only(read, sleep=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_exhaustion_and_circuit(self):
        breaker = CircuitBreaker(threshold=2)
        def failing():
            raise TimeoutError()
        with self.assertRaises(RecoveryExhausted):
            retry_read_only(failing, breaker=breaker, sleep=lambda _: None)
        self.assertFalse(breaker.allowed())

    def test_price_gate(self):
        self.assertTrue(validate_market_snapshot({"symbol": "SBIN", "asof": "2026-11-04", "price": 100.0}))
        for value in (0, -1, float("nan"), float("inf"), True, None):
            self.assertFalse(validate_market_snapshot({"symbol": "SBIN", "asof": "2026-11-04", "price": value}))

    def test_invalid_retry_configuration(self):
        with self.assertRaises(ValueError):
            retry_read_only(lambda: 1, attempts=100)

if __name__ == "__main__":
    unittest.main()
