"""Portfolio Doctor privacy, input and report delivery regression tests (no API calls)."""
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import portfolio_doctor_live as doctor


class InputTests(unittest.TestCase):
    def test_three_customer_positions(self):
        raw = b"SAMPLEA 7 120\nSAMPLEB 8 230\nSAMPLEC 9 340"
        out = doctor.parse_holdings("typed_holdings.csv", raw)
        self.assertEqual([x["symbol"] for x in out], ["SAMPLEA", "SAMPLEB", "SAMPLEC"])
        self.assertEqual(out[1]["qty"], 8)
        self.assertEqual(out[2]["buy"], 340)

    def test_exact_sbi_alias_normalizes_to_exchange_verified_sbin(self):
        for symbol in ("SBI", "sbi", "NSE:SBI", "SBI.NS"):
            with self.subTest(symbol=symbol):
                raw = f"{symbol} 1 100".encode("ascii")
                parsed = doctor.parse_holdings("typed_holdings.csv", raw)
                self.assertEqual(parsed[0]["symbol"], "SBIN")
                self.assertNotEqual(parsed[0].get("exchange"), "BSE")
        # Already correct NSE symbols stay unchanged.
        self.assertEqual(doctor.parse_holdings(
            "typed_holdings.csv", b"SBIN 1 100")[0]["symbol"], "SBIN")
        # Preserve explicit exchange identity, not a fuzzy rename.
        bse = doctor.parse_holdings("typed_holdings.csv", b"BSE:SBI 1 100")
        self.assertEqual(bse[0]["symbol"], "SBI")
        self.assertEqual(bse[0]["exchange"], "BSE")
        # Same company appearing under both labels must not be double-counted.
        with self.assertRaises(doctor.PortfolioDoctorError) as err:
            doctor.parse_holdings("typed_holdings.csv",
                                  b"SBI 1 100\\nSBIN 2 200".replace(b"\\n", b"\n"))
        self.assertEqual(err.exception.code, "BAD_INPUT")

    def test_all_other_symbols_remain_exact_and_unmodified(self):
        raw = b"EXIDEIND 1 100\\nAXISBANK 2 200".replace(b"\\n", b"\n")
        actual = doctor.parse_holdings("typed_holdings.csv", raw)
        self.assertEqual([h["symbol"] for h in actual], ["EXIDEIND", "AXISBANK"])

    def test_csv_headers_and_x_ticker(self):
        raw = b"Symbol,Qty,Buy Price\nAXISBANK,10,900\nEXIDEIND,3,200"
        out = doctor.parse_holdings("account.csv", raw)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["symbol"], "AXISBANK")

    def test_invalid_holdings_rejected(self):
        cases = [
            b"SAMPLEA",
            b"SAMPLEA 7 120\nSAMPLEA 51 481",
            b"SAMPLEA -2 120",
            b"SAMPLEA 7 nan",
            b"SAMPLEA 7 0",
            b"SAMPLEA 7 120\nSAMPLEB 8 230\nSAMPLEC 9 340\nA 1 2\nB 2 3\nC 3 4",
            b"Symbol,Qty,Buy Price\nINVALID? 2 3",
        ]
        for raw in cases:
            with self.subTest(raw=raw[:30]), self.assertRaises(doctor.PortfolioDoctorError):
                doctor.parse_holdings("typed_holdings.csv", raw)
        for name in ("unknown.txt", "holdings.xls"):
            with self.assertRaises(doctor.PortfolioDoctorError):
                doctor.parse_holdings(name, b"SAMPLEA 5 480")


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        bse = patch.object(doctor, "bse_daily_for_doctor", return_value=None)
        bse.start()
        self.addCleanup(bse.stop)

    def test_missing_prices_are_not_fabricated(self):
        fake = SimpleNamespace(bulk_history=lambda *_args, **_kwargs: {})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy", return_value={}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.price_holdings([{"symbol": "SAMPLEA", "qty": 7, "buy": 120}])
        self.assertEqual(err.exception.code, "NO_PRICES")

    def test_stale_prices_refused(self):
        import pandas as pd
        from datetime import datetime, timedelta
        old = datetime.now(doctor.IST).date() - timedelta(days=9)
        idx = pd.date_range(end=old, periods=30)
        close = pd.Series([float(100 + i) for i in range(30)], index=idx)
        fake = SimpleNamespace(bulk_history=lambda *_args, **_kwargs: {
            "SAMPLEA": pd.DataFrame({"Close": close})})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy", return_value={}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.price_holdings([{"symbol": "SAMPLEA", "qty": 50, "buy": 480}])
        self.assertEqual(err.exception.code, "STALE_PRICES")


class MemoryOnlyTests(unittest.TestCase):
    def test_pdf_generated_in_ram_without_files(self):
        h = {"symbol": "SAMPLEA", "qty": 7, "buy": 120, "close": 130,
             "date": "2026-11-17", "invested": 840, "value": 910,
             "pnl": 70, "pnl_pct": 8.3333,
             "ema20": 125, "ema50": 120, "rsi14": 55,
             "high20": 135, "low20": 110, "weight": 100}
        data = doctor.build_pdf_bytes([h])
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreater(len(data), 1000)
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        self.assertIn("SAMPLEA", text)
        self.assertIn("840", text)
        self.assertIn("No BUY/SELL signal", text)

    def test_parent_uses_stdin_stdout_no_client_files(self):
        with patch.object(doctor.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout=b"", stderr=b"MSK_DOCTOR_CODE=NO_PRICES\n")) as run:
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory("typed_holdings.csv", b"SAMPLEA 7 120")
        self.assertEqual(err.exception.code, "NO_PRICES")
        args, kwargs = run.call_args
        self.assertEqual(kwargs["input"], b"SAMPLEA 7 120")
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("cwd", kwargs)

    def test_hard_deadline(self):
        with patch.object(doctor.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory("test.csv", b"SAMPLEA 7 120", timeout=1)
        self.assertEqual(err.exception.code, "TIMEOUT")

    def test_customer_path_has_no_ledger_or_tempfile(self):
        main = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        segment = main.split("def portfolio_doctor_document(m, _typed_payload=None):", 1)[1].split("# ── Button Handlers", 1)[0]
        self.assertNotIn("PortfolioDoctorLedger", segment)
        self.assertNotIn("TemporaryDirectory", segment)
        self.assertNotIn("upload_hash", segment)
        self.assertNotIn("portfolio_data.json", segment)
        self.assertIn("run_isolated_memory(name, payload, timeout=150)", segment)
        self.assertIn("_portfolio_doctor_slots.release()", segment)
        live = Path(doctor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("NamedTemporaryFile", live)
        self.assertNotIn("CREATE TABLE", live)
        self.assertNotIn("psycopg", live)


if __name__ == "__main__":
    unittest.main()
