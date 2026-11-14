"""Safety tests for bounded, real-session Stocks Buzz Neon snapshot."""
import copy
import json
import sys
import types
import unittest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch

import stocks_buzz_snapshot as snap

IST = timezone(timedelta(hours=5, minutes=30))


def scan():
    return {
        "data_session": date.today().isoformat(),
        "ts": datetime.now(IST).isoformat(),
        "universe_size": 1000, "downloaded": 950, "coverage": .95,
        "source": "Synthetic unit-test fixture — not market data",
        "regime": {"text": "Nifty sample", "supportive": None},
        "breakouts": [], "watch": [], "liquid": 900, "extended": 0,
    }


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("SELECT data"):
            return FakeResult(self.row)
        return FakeResult(None)


class BuzzSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.day = date.today()
        self.botmod = types.SimpleNamespace(IST=IST,
                            _last_completed_session=lambda: self.day)
        self.botpatch = patch.dict(sys.modules, {"stocks_buzz": self.botmod})
        self.botpatch.start()
        self.addCleanup(self.botpatch.stop)

    def test_valid_completed_session_and_coverage_encode(self):
        value = scan()
        value["data_session"] = self.day.isoformat()
        cleaned, raw = snap._encode(value)
        self.assertEqual(cleaned["downloaded"], 950)
        self.assertLess(len(raw), snap.MAX_BYTES)
        self.assertIn(b"data_session", raw)

    def test_refuse_stale_or_partial(self):
        value = scan()
        value["data_session"] = self.day.isoformat()
        value["downloaded"] = 500
        with self.assertRaisesRegex(ValueError, "coverage"):
            snap._encode(value)
        value["downloaded"] = 950
        value["data_session"] = "2020-01-01"
        with self.assertRaisesRegex(ValueError, "completed NSE"):
            snap._encode(value)

    def test_refuse_invalid_trading_levels(self):
        value = scan()
        value["data_session"] = self.day.isoformat()
        value["breakouts"] = [{
            "sym": "TEST", "kind": "BREAKOUT", "ltp": 100.,
            "entry": 100., "sl": 101., "t1": 120., "t2": 130.,
            "score": 80.,
        }]
        with self.assertRaisesRegex(ValueError, "ordering"):
            snap._encode(value)

    def test_durable_read_rejects_tampering(self):
        value = scan()
        value["data_session"] = self.day.isoformat()
        _, raw = snap._encode(value)
        fake = types.SimpleNamespace(connect=lambda *_a, **_kw:
              FakeConnection((raw, "0" * 64, 1000, 950)))
        with patch.object(snap, "_dsn", return_value="postgresql://test?sslmode=require"), \
             patch.dict(sys.modules, {"psycopg": fake}):
            self.assertIsNone(snap.load_verified_scan())

    def test_durable_read_restores_valid_types(self):
        import hashlib
        value = scan()
        value["data_session"] = self.day.isoformat()
        _, raw = snap._encode(value)
        fake = types.SimpleNamespace(connect=lambda *_a, **_kw:
              FakeConnection((raw, hashlib.sha256(raw).hexdigest(), 1000, 950)))
        with patch.object(snap, "_dsn", return_value="postgresql://test?sslmode=require"), \
             patch.dict(sys.modules, {"psycopg": fake}):
            got = snap.load_verified_scan()
        self.assertEqual(got["data_session"], self.day)
        self.assertEqual(got["downloaded"], 950)
        self.assertIsInstance(got["ts"], datetime)


if __name__ == "__main__":
    unittest.main()
