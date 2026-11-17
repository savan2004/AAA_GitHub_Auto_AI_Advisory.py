"""Offline NSE bhavcopy authenticity, overlap and non-fabrication tests."""
import io
import csv
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import portfolio_doctor_bhavcopy as nse
import portfolio_doctor_live as doctor


TUESDAY = date(2026, 11, 17)
MONDAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 18, 1, 35, tzinfo=doctor.IST)
HEAD = ["SYMBOL", "SERIES", "DATE1", "OPEN_PRICE", "HIGH_PRICE",
        "LOW_PRICE", "CLOSE_PRICE", "PREV_CLOSE", "TTL_TRD_QNTY",
        "TURNOVER_LACS", "DELIV_PER"]


def csv_bytes(day=TUESDAY, *, include=True, bad_day=False, mismatch=False):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(HEAD)
    daystr = ("15-Nov-2026" if bad_day else day.strftime("%d-%b-%Y"))
    if include:
        writer.writerow(["SAMPLEA", "EQ", daystr,
                         "100" if day == MONDAY else "102",
                         "101" if day == MONDAY else "104",
                         "99" if day == MONDAY else "101",
                         "160" if mismatch else ("100" if day == MONDAY else "103"),
                         "99" if day == MONDAY else "100",
                         "10000", "100", "50"])
    for i in range(1250):
        writer.writerow([f"DUMMY{i:05d}", "EQ", daystr,
                         "101", "103", "99", "100", "100", "5000", "100", "50"])
    return stream.getvalue().encode("utf-8")


def price_frame(last="2026-11-16"):
    dates = pd.bdate_range(end=last, periods=80)
    return pd.DataFrame({
        "Open": [100.0] * 80, "High": [101.0] * 80,
        "Low": [99.0] * 80, "Close": [100.0] * 80,
        "Volume": [10000.0] * 80,
    }, index=dates)


def records(day):
    return {
        "Open": 100.0 if day == MONDAY else 102.0,
        "High": 101.0 if day == MONDAY else 104.0,
        "Low": 99.0 if day == MONDAY else 101.0,
        "Close": 100.0 if day == MONDAY else 103.0,
        "Volume": 10000.0,
        "PreviousClose": 99.0 if day == MONDAY else 100.0,
    }


class BhavcopySafeguards(unittest.TestCase):
    def test_public_csv_must_contain_exchange_headers_eq_rows_and_date(self):
        urls = []
        def get(url, **kw):
            urls.append(url)
            self.assertEqual(kw["timeout"], 12)
            return SimpleNamespace(ok=True, content=csv_bytes())
        rows = nse.fetch_nse_bhavcopy_rows(TUESDAY, ["SAMPLEA"], get=get)
        self.assertEqual(list(rows), ["SAMPLEA"])
        self.assertEqual(rows["SAMPLEA"]["Close"], 103)
        self.assertIn("17112026", urls[0])
        self.assertEqual(nse.fetch_nse_bhavcopy_rows(
            TUESDAY, ["SAMPLEA"], get=lambda *_a, **_kw:
                SimpleNamespace(ok=True, content=csv_bytes(bad_day=True))), {})
        self.assertEqual(nse.fetch_nse_bhavcopy_rows(
            TUESDAY, ["SAMPLEA"], get=lambda *_a, **_kw:
                SimpleNamespace(ok=True, content=b"not a CSV")), {})

    def test_file_not_published_or_equity_absent_does_not_invent(self):
        none = nse.fetch_nse_bhavcopy_rows(
            TUESDAY, ["SAMPLEA"], get=lambda *_a, **_kw:
                SimpleNamespace(ok=False, content=b""))
        self.assertEqual(none, {})
        missing = nse.fetch_nse_bhavcopy_rows(
            TUESDAY, ["SAMPLEA"], get=lambda *_a, **_kw:
                SimpleNamespace(ok=True, content=csv_bytes(include=False)))
        self.assertEqual(missing, {})

    def test_one_day_only_if_prior_nse_ohlc_agrees_with_history(self):
        merged = nse.stitch_one_verified_day(
            price_frame(), prior=records(MONDAY), latest=records(TUESDAY),
            previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        self.assertIsNotNone(merged)
        self.assertEqual(len(merged), 81)
        self.assertEqual(merged.index[-1].date(), TUESDAY)
        self.assertEqual(float(merged.iloc[-1]["Close"]), 103)
        self.assertEqual(float(merged.iloc[-1]["Volume"]), 10000)

    def test_reject_provider_disagreement_and_wrong_trading_session(self):
        prior = records(MONDAY)
        prior["Close"] = 150
        bad = nse.stitch_one_verified_day(
            price_frame(), prior=prior, latest=records(TUESDAY),
            previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        self.assertIsNone(bad)
        bad_date = nse.stitch_one_verified_day(
            price_frame("2026-11-13"), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY,
            expected_day=TUESDAY, now=NOW)
        self.assertIsNone(bad_date)

    def test_split_or_bonus_suspected_must_block_autostitch(self):
        latest = records(TUESDAY)
        latest["PreviousClose"] = 50
        self.assertIsNone(nse.stitch_one_verified_day(
            price_frame(), prior=records(MONDAY), latest=latest,
            previous_day=MONDAY, expected_day=TUESDAY, now=NOW))
        latest = records(TUESDAY)
        latest["Open"] = 170
        self.assertIsNone(nse.stitch_one_verified_day(
            price_frame(), prior=records(MONDAY), latest=latest,
            previous_day=MONDAY, expected_day=TUESDAY, now=NOW))

    def test_request_data_kept_in_memory_and_no_sensitive_logging(self):
        path = Path(nse.__file__).read_text(encoding="utf-8")
        for banned in ("logging.", "logger.", "psycopg", "sqlite3", "shelve",
                       "NamedTemporaryFile", "mkstemp", "open(path", "CREATE TABLE"):
            self.assertNotIn(banned, path)
        self.assertIn("io.StringIO(", path)
        self.assertIn("fetch_nse_bhavcopy_rows", path)


if __name__ == "__main__":
    unittest.main()
