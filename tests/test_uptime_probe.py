"""No-network tests for standalone HTTP uptime probe."""
import importlib.util
from pathlib import Path
import unittest
from urllib.error import URLError


def load_probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "uptime_probe.py"
    spec = importlib.util.spec_from_file_location("msk_uptime_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHTTPResponse:
    def __init__(self, status=200, body=b'{"status":"ok"}'):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, n):
        return self.body[:n]


class UptimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.probe = load_probe()

    def test_success_is_not_a_telegram_delivery_claim(self):
        record = self.probe.check(opener=lambda *_a, **_k: FakeHTTPResponse(),
                                  clock=lambda: 1.0)
        self.assertTrue(record["http_ok"])
        self.assertEqual(record["http_status"], 200)
        self.assertEqual(record["telegram_delivery"], "not_verified")
        self.assertIn("Single HTTP probe", record["note"])

    def test_response_with_misleading_status_is_unhealthy(self):
        record = self.probe.check(opener=lambda *_a, **_k:
                                  FakeHTTPResponse(body=b'{"status":"degraded"}'),
                                  clock=lambda: 1.0)
        self.assertFalse(record["http_ok"])

    def test_network_failure_is_an_explicit_failed_sample(self):
        def fail(*_a, **_k):
            raise URLError("synthetic network failure")
        record = self.probe.check(opener=fail, clock=lambda: 1.0)
        self.assertFalse(record["http_ok"])
        self.assertEqual(record["error_type"], "URLError")
        self.assertNotIn("synthetic network failure", str(record))

    def test_server_500_is_unhealthy(self):
        record = self.probe.check(opener=lambda *_a, **_k:
                                  FakeHTTPResponse(status=500),
                                  clock=lambda: 1.0)
        self.assertFalse(record["http_ok"])

    def test_invalid_json_is_unhealthy(self):
        record = self.probe.check(opener=lambda *_a, **_k:
                                  FakeHTTPResponse(body=b'not-json'),
                                  clock=lambda: 1.0)
        self.assertFalse(record["http_ok"])


if __name__ == "__main__":
    unittest.main()
