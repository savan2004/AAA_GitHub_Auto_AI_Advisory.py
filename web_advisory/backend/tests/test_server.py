"""Offline regression tests for the website-only read-only API.

Run from repository root:
    python -m unittest discover -s web_advisory/backend/tests -v
"""
import csv
import http.client
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import LocalCSVProvider, ProviderError, ThreadingHTTPServer, make_handler


def write_candles(directory, symbol="BEL", mutate=None):
    file = Path(directory) / (symbol + ".csv")
    start = date.today() - timedelta(days=75)
    with file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("timestamp", "open", "high", "low", "close", "volume"))
        writer.writeheader()
        for i in range(55):
            row = {"timestamp": (start + timedelta(days=i)).isoformat(),
                   "open": 100 + i, "high": 102 + i, "low": 99 + i,
                   "close": 101 + i, "volume": 10000 + i}
            if mutate:
                mutate(row, i)
            writer.writerow(row)
    return file


class ProviderTests(unittest.TestCase):
    def test_off_by_default(self):
        with self.assertRaises(ProviderError) as result:
            LocalCSVProvider("").history("BEL")
        self.assertEqual(result.exception.status, 503)

    def test_valid_eod_is_not_verified_or_live(self):
        with tempfile.TemporaryDirectory() as folder:
            write_candles(folder)
            data = LocalCSVProvider(folder).history("BEL")
            self.assertEqual(data["count"], 55)
            self.assertFalse(data["verified"])
            self.assertFalse(data["live"])
            self.assertEqual(data["source"], "operator_provisioned_csv")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            for symbol in ("../main.py", "../../secret", "DEMO01", "%2e%2e", "BEL/../TCS"):
                with self.subTest(symbol=symbol), self.assertRaises(ProviderError):
                    LocalCSVProvider(folder).history(symbol)

    def test_rejects_future_duplicate_and_nonfinite(self):
        cases = [
            lambda row, i: row.update(timestamp="2099-01-01") if i == 54 else None,
            lambda row, i: row.update(timestamp=(date.today()-timedelta(days=75+1)).isoformat()) if i == 54 else None,
            lambda row, i: row.update(close="NaN") if i == 54 else None,
            lambda row, i: row.update(high=1) if i == 54 else None,
        ]
        with tempfile.TemporaryDirectory() as folder:
            for mutate in cases:
                write_candles(folder, mutate=mutate)
                with self.assertRaises(ProviderError) as result:
                    LocalCSVProvider(folder).history("BEL")
                self.assertEqual(result.exception.status, 422)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        write_candles(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(LocalCSVProvider(self.temp.name)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Origin": origin} if origin else {}
        conn.request(method, path, headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read())
        outcome = (resp.status, dict(resp.getheaders()), body)
        conn.close()
        return outcome

    def test_health_and_history(self):
        status, headers, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(body["ai_enabled"])
        status, headers, body = self.request("GET", "/api/v1/equities/BEL/history",
                                             "https://msk-web-advisory-preview.onrender.com")
        self.assertEqual(status, 200)
        self.assertEqual(body["symbol"], "BEL")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),
                         "https://msk-web-advisory-preview.onrender.com")

    def test_no_credentials_cors_or_writes(self):
        status, headers, body = self.request("GET", "/api/health", "https://attacker.example")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, headers, body = self.request("POST", "/api/v1/equities/BEL/history")
        self.assertEqual(status, 405)

    def test_invalid_routes_and_symbols(self):
        self.assertEqual(self.request("GET", "/api/v1/equities/%2e%2e/history")[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/equities/TCS/history")[0], 404)
        self.assertEqual(self.request("GET", "/api/ai/ask")[0], 404)


if __name__ == "__main__":
    unittest.main()
