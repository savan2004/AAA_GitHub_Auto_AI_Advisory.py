"""Deep Doctor regression: stale/partial prices, PDF integrity and retired paths."""
import ast
import re
from datetime import datetime
from pathlib import Path
import unittest

import pandas as pd

import portfolio_doctor_live as doctor


def record(symbol="SAMPLEA", day="2026-11-17"):
    return {"symbol": symbol, "qty": 2, "buy": 100, "close": 110,
            "date": day, "ema20": 106, "ema50": 101, "rsi14": 58,
            "high20": 118, "low20": 95}


class DeepPriceAndPDFTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({"Close": [100.] * 30},
                            index=pd.bdate_range(end="2026-11-17", periods=30))

    def test_missing_or_nonfinite_latest_close_is_not_relabelled_current(self):
        now = datetime(2026, 11, 18, 10, tzinfo=doctor.IST)
        for value in (float("nan"), float("inf"), 0, -1):
            frame = self.frame()
            frame.iloc[-1, 0] = value
            with self.subTest(value=value), self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.verified_completed_daily_frame(frame, now=now)
            self.assertEqual(error.exception.code, "NO_PRICES")

    def test_duplicate_daily_observations_and_out_of_order_history_are_rejected(self):
        frame = self.frame()
        duplicate = pd.concat([frame, frame.iloc[-1:]])
        intraday = pd.concat([frame, frame.iloc[-1:].rename(
            index=lambda stamp: stamp + pd.Timedelta(hours=3))])
        for bad in (duplicate, intraday, frame.iloc[::-1]):
            with self.assertRaises(doctor.PortfolioDoctorError):
                doctor.verified_completed_daily_frame(
                    bad, now=datetime(2026, 11, 18, 10, tzinfo=doctor.IST))

    def test_pdf_rejects_mixed_dates_invalid_dates_and_nonfinite_indicators(self):
        for rows in ([record(), record("SAMPLEB", "2026-11-16")],
                     [record(day="not-a-date")],
                     [record(day="2099-01-01")]):
            with self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.build_pdf_bytes(rows)
            self.assertEqual(error.exception.code, "PDF_FAILED")
        for field, value in (("ema20", float("nan")), ("ema50", float("inf")),
                             ("rsi14", -1), ("rsi14", 101), ("high20", float("inf"))):
            row = record()
            row[field] = value
            with self.subTest(field=field), self.assertRaises(doctor.PortfolioDoctorError):
                doctor.build_pdf_bytes([row])

    def test_input_does_not_silently_round_unsupported_precision(self):
        for raw in (b"SAMPLEA 1000000000 999999999.123456789",
                    b"SAMPLEA 0.123456789012345678901 100"):
            with self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.parse_holdings("input.csv", raw)
            self.assertEqual(error.exception.code, "BAD_INPUT")
        normal = doctor.parse_holdings("input.csv", b"SAMPLEA 2.5 123.4567")
        self.assertEqual(normal[0]["qty"], 2.5)
        self.assertEqual(normal[0]["buy"], 123.4567)

    def test_incomplete_private_rows_are_not_provider_queries(self):
        for text in ("SAMPLEA 7", '"SAMPLEA",7,"120', "SAMPLEA;7",
                     "NSE:SAMPLEA\t7"):
            self.assertTrue(doctor.looks_like_holdings_text(text))
        for text in ("NIFTY 50", "NIFTY 100", "SENSEX 30", "3M India Limited"):
            self.assertFalse(doctor.looks_like_holdings_text(text))


class RetiredDoctorPathTests(unittest.TestCase):
    def test_removed_persistent_doctor_paths_have_no_remaining_imports(self):
        root = Path(__file__).resolve().parents[1]
        retired = {"portfolio_doctor_runner", "portfolio_doctor_ledger", "portfolio_report"}
        for name in retired:
            self.assertFalse((root / (name + ".py")).exists())
        references = []
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8-sig")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                # Unrelated historical scripts are not part of Doctor startup.
                # Still check their import lines without certifying their syntax.
                for name in retired:
                    if re.search(r"(?m)^\s*(?:from|import)\s+" + re.escape(name) + r"\b", source):
                        references.append(str(path.relative_to(root)))
                continue
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [(node.module or "").split(".")[0]]
                elif isinstance(node, ast.Call) and node.args:
                    fn = node.func
                    dynamic = ((isinstance(fn, ast.Name) and fn.id == "__import__") or
                               (isinstance(fn, ast.Attribute) and fn.attr == "import_module"))
                    if dynamic and isinstance(node.args[0], ast.Constant):
                        modules = [str(node.args[0].value).split(".")[0]]
                if retired.intersection(modules):
                    references.append(str(path.relative_to(root)))
        self.assertEqual(references, [])
        # Charts are shared by active research/sector reports and must remain.
        self.assertTrue((root / "report_charts.py").exists())
