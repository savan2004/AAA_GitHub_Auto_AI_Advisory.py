"""Doctor-only stale-provider recovery, price integrity, and privacy gates.

All fixtures are synthetic. No customer symbols, accounts or live requests.
"""
import logging
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import portfolio_doctor_live as doctor


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 18, 1, 35, tzinfo=tz or doctor.IST)


def frame(last="2026-11-17", *, base=100):
    dates = pd.bdate_range(end=last, periods=80)
    vals = [float(base + i / 10) for i in range(80)]
    return pd.DataFrame({
        "Open": vals, "High": [v + 1 for v in vals],
        "Low": [v - 1 for v in vals], "Close": vals,
        "Volume": [1200] * len(vals),
    }, index=dates)


class DoctorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = Clock.now(doctor.IST)
        self.old = frame("2026-11-16")
        self.new = frame("2026-11-17")

    def test_recover_only_completed_daily_from_alternate_provider(self):
        calls = []
        def older():
            calls.append("older")
            return self.old
        def fresh():
            calls.append("fresh")
            return self.new
        result = doctor.recover_fresh_daily_for_doctor(
            "SAMPLEA", now=self.now, fetchers=(older, fresh))
        self.assertEqual(calls, ["older", "fresh"])
        self.assertEqual(result.index[-1].date().isoformat(), "2026-11-17")
        self.assertEqual(len(result), 80)

    def test_reject_both_stale_alternates(self):
        out = doctor.recover_fresh_daily_for_doctor(
            "SAMPLEA", now=self.now,
            fetchers=(lambda: self.old, lambda: self.old))
        self.assertIsNone(out)

    def test_reject_partial_close_only_alternate(self):
        incomplete = self.new[["Close"]].copy()
        out = doctor.recover_fresh_daily_for_doctor(
            "SAMPLEA", now=self.now,
            fetchers=(lambda: incomplete,))
        self.assertIsNone(out)

    def test_reject_fewer_than_20_completed_closes(self):
        too_short = self.new.iloc[-10:]
        out = doctor.recover_fresh_daily_for_doctor(
            "SAMPLEA", now=self.now, fetchers=(lambda: too_short,))
        self.assertIsNone(out)

    def test_primary_stale_but_alternate_fresh_produces_full_correct_totals(self):
        fake = SimpleNamespace(bulk_history=lambda *_a, **_kw: {"SAMPLEA": self.old})
        def recover(symbol, *, now):
            self.assertEqual(symbol, "SAMPLEA")
            return self.new
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", side_effect=recover) as rec, \
             patch.object(doctor, "datetime", Clock):
            result = doctor.price_holdings(
                [{"symbol": "SAMPLEA", "qty": 7, "buy": 120}])
        rec.assert_called_once()
        self.assertEqual(result[0]["date"], "2026-11-17")
        self.assertAlmostEqual(result[0]["value"], result[0]["close"] * 7)
        self.assertAlmostEqual(result[0]["pnl"], result[0]["value"] - 7 * 120)
        self.assertEqual(result[0]["weight"], 100.0)

    def test_fresh_primary_does_not_call_alternates(self):
        fake = SimpleNamespace(bulk_history=lambda *_a, **_kw: {"SAMPLEA": self.new})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor",
                          side_effect=AssertionError("must not retry fresh source")), \
             patch.object(doctor, "datetime", Clock):
            out = doctor.price_holdings(
                [{"symbol": "SAMPLEA", "qty": 1, "buy": 100}])
        self.assertEqual(out[0]["date"], "2026-11-17")

    def test_one_holding_unrecoverable_blocks_whole_report(self):
        fake = SimpleNamespace(
            bulk_history=lambda *_a, **_kw: {
                "SAMPLEA": self.new, "SAMPLEB": self.old})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None), \
             patch.object(doctor, "datetime", Clock):
            with self.assertRaises(doctor.PortfolioDoctorError) as e:
                doctor.price_holdings([
                    {"symbol": "SAMPLEA", "qty": 2, "buy": 100},
                    {"symbol": "SAMPLEB", "qty": 3, "buy": 200}])
        self.assertEqual(e.exception.code, "STALE_PRICES")
        self.assertNotIn("SAMPLEB", str(e.exception))

    def test_missing_all_sources_never_returns_fabricated_price(self):
        fake = SimpleNamespace(bulk_history=lambda *_a, **_kw: {})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None), \
             patch.object(doctor, "datetime", Clock):
            with self.assertRaises(doctor.PortfolioDoctorError) as e:
                doctor.price_holdings([{"symbol": "SAMPLEA", "qty": 2, "buy": 100}])
        self.assertEqual(e.exception.code, "NO_PRICES")

    def test_privacy_safe_worker_does_not_enable_logging(self):
        from pathlib import Path
        source = Path(doctor.__file__).read_text(encoding="utf-8")
        segment = source.split("def worker_main(filename):", 1)[1]
        self.assertIn("logging.disable(logging.CRITICAL)", segment)
        self.assertNotIn("logger.info(", segment)
        self.assertNotIn("logger.warning(", segment)
        self.assertNotIn("logger.error(", segment)
        self.assertIn("stdin.buffer.read(", segment)
        self.assertIn("stdout.buffer.write(", segment)


if __name__ == "__main__":
    unittest.main()
