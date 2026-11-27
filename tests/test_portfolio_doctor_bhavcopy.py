"""Offline NSE bhavcopy authenticity, overlap and non-fabrication tests."""
import io
import csv
import zipfile
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

    def test_official_final_udiff_fallback_preserves_price_date_and_ohlcv(self):
        def archive(day=TUESDAY, duplicate=False):
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["TckrSymb", "SctySrs", "TradDt", "OpnPric",
                             "HghPric", "LwPric", "ClsPric", "PrvsClsgPric",
                             "TtlTradgVol"])
            row = ["SAMPLEA", "EQ", day.isoformat(), "102", "104",
                   "101", "103", "100", "10000"]
            writer.writerow(row)
            if duplicate:
                writer.writerow(row)
            for i in range(1500):
                writer.writerow([f"DUMMY{i:05d}", "EQ", day.isoformat(),
                                 "101", "103", "99", "100", "100", "5000"])
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("official.csv", stream.getvalue())
            return data.getvalue()

        def check(payload):
            calls = []
            def get(url, **kw):
                calls.append(url)
                if len(calls) == 1:
                    return SimpleNamespace(ok=False, content=b"")
                return SimpleNamespace(ok=True, content=payload)
            result = nse.fetch_nse_bhavcopy_rows(TUESDAY, ["SAMPLEA"], get=get)
            self.assertEqual(len(calls), 2)
            self.assertIn("20261117_F_0000.csv.zip", calls[1])
            return result

        self.assertEqual(check(archive()), {"SAMPLEA": records(TUESDAY)})
        self.assertEqual(check(archive(MONDAY)), {})
        self.assertEqual(check(archive(duplicate=True)), {})
        self.assertEqual(check(b"not a zip"), {})

        # A blocked official host can use the same-date official mirror.
        import requests
        for failure in ("403", "timeout"):
            calls = []
            def mirror_get(url, **kw):
                calls.append(url)
                if len(calls) == 1:
                    return SimpleNamespace(ok=False)
                if len(calls) == 2:
                    if failure == "timeout":
                        raise requests.Timeout("archive timeout")
                    return SimpleNamespace(ok=False, status_code=403)
                return SimpleNamespace(ok=True, content=archive())
            self.assertEqual(nse.fetch_nse_bhavcopy_rows(
                TUESDAY, ["SAMPLEA"], get=mirror_get), {"SAMPLEA": records(TUESDAY)})
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1], nse.FINAL_ARCHIVE_HOSTS[0] +
                             "/content/cm/BhavCopy_NSE_CM_0_0_0_20261117_F_0000.csv.zip")
            self.assertEqual(calls[2], nse.FINAL_ARCHIVE_HOSTS[1] +
                             "/content/cm/BhavCopy_NSE_CM_0_0_0_20261117_F_0000.csv.zip")

        calls = []
        def unavailable(url, **kw):
            calls.append(url)
            return SimpleNamespace(ok=False, status_code=403)
        self.assertEqual(nse.fetch_nse_bhavcopy_rows(
            TUESDAY, ["SAMPLEA"], get=unavailable), {})
        self.assertEqual(len(calls), 3)

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

    def test_unfinished_current_day_candle_does_not_block_official_prior_overlap(self):
        # Mid-session Yahoo chart can include Wednesday even while Tuesday is
        # the required completed session; it must never be used as report data.
        partial_today = pd.DataFrame({
            "Open": [105.0], "High": [106.0], "Low": [104.0],
            "Close": [105.0], "Volume": [1000.0],
        }, index=[pd.Timestamp("2026-11-18")])
        primary = pd.concat([price_frame(), partial_today])
        stitched = nse.stitch_one_verified_day(
            primary, prior=records(MONDAY), latest=records(TUESDAY),
            previous_day=MONDAY, expected_day=TUESDAY,
            now=datetime(2026, 11, 18, 14, 7, tzinfo=doctor.IST))
        self.assertIsNotNone(stitched)
        self.assertEqual(stitched.index[-1].date(), TUESDAY)
        self.assertEqual(len(stitched), 81)
        self.assertNotIn(date(2026, 11, 18), set(stitched.index.date))
        self.assertEqual(float(stitched.iloc[-1]["Close"]), 103)

    def test_recovery_wont_overwrite_existing_session_or_reorder_bad_history(self):
        partial_today = pd.DataFrame({
            "Open": [105.0], "High": [106.0], "Low": [104.0],
            "Close": [105.0], "Volume": [1000.0],
        }, index=[pd.Timestamp("2026-11-18")])
        completed = pd.DataFrame({
            "Open": [102.0], "High": [104.0], "Low": [101.0],
            "Close": [103.0], "Volume": [10000.0],
        }, index=[pd.Timestamp("2026-11-17")])
        with_completed = pd.concat([price_frame(), completed, partial_today])
        for bad in (with_completed, with_completed.iloc[::-1],
                    pd.concat([price_frame(), partial_today, partial_today])):
            with self.subTest(kind=len(bad)):
                self.assertIsNone(nse.stitch_one_verified_day(
                    bad, prior=records(MONDAY), latest=records(TUESDAY),
                    previous_day=MONDAY, expected_day=TUESDAY, now=NOW))

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

    def test_ten_symbol_archive_is_one_request_and_eleven_is_rejected(self):
        wanted = ["DUMMY%05d" % i for i in range(10)]
        calls = []
        def get(url, **kw):
            calls.append(url)
            return SimpleNamespace(ok=True, content=csv_bytes())
        self.assertEqual(set(nse.fetch_nse_bhavcopy_rows(TUESDAY, wanted, get=get)), set(wanted))
        self.assertEqual(len(calls), 1)
        self.assertEqual(nse.fetch_nse_bhavcopy_rows(TUESDAY, wanted + ["SAMPLEA"], get=get), {})
        self.assertEqual(len(calls), 1)

    def test_strict_overlap_rejects_price_volume_and_prev_close_mismatch(self):
        for field, value in (("Close", 100.2), ("Volume", 10001)):
            prior = dict(records(MONDAY), **{field: value})
            self.assertIsNone(nse.stitch_one_verified_day(
                price_frame(), prior=prior, latest=records(TUESDAY),
                previous_day=MONDAY, expected_day=TUESDAY, now=NOW))
        latest = dict(records(TUESDAY), PreviousClose=100.2)
        self.assertIsNone(nse.stitch_one_verified_day(
            price_frame(), prior=records(MONDAY), latest=latest,
            previous_day=MONDAY, expected_day=TUESDAY, now=NOW))

    def test_doctor_five_and_ten_stale_holdings_recovered_in_two_session_fetches(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        for count in (5, 10):
            with self.subTest(count=count):
                holdings = [{"symbol": "SAMPLE%02d" % i, "qty": 2, "buy": 90}
                            for i in range(count)]
                symbols = [h["symbol"] for h in holdings]
                frames = {s: price_frame() for s in symbols}
                def fetch(day, wanted):
                    self.assertEqual(set(wanted), set(symbols))
                    return {s: records(day) for s in wanted}
                with patch("market_data.bulk_history", return_value=frames), \
                     patch.object(nse, "fetch_nse_bhavcopy_rows", side_effect=fetch) as official, \
                     patch.object(doctor, "recover_fresh_daily_for_doctor") as alternate, \
                     patch.object(doctor, "datetime", Clock):
                    rows, verified = doctor.price_holdings(holdings, include_frames=True)
                self.assertEqual(len(rows), count)
                self.assertEqual(len(verified), count)
                self.assertEqual(official.call_count, 2)
                alternate.assert_not_called()
                self.assertTrue(all(row["close"] == 103 and row["date"] == TUESDAY.isoformat()
                                    and row["rsi14"] is not None and row["ema50"] is not None
                                    and "NSE EOD bhavcopy" in row["price_source"] for row in rows))
                self.assertAlmostEqual(sum(row["weight"] for row in rows), 100.)
                self.assertTrue(all(row["pnl"] == 26 for row in rows))

    def test_public_probe_accepts_same_date_streamed_archive_only(self):
        from scripts.msk_doctor_public_eod_probe import verify_final_archive
        complete = nse.stitch_one_verified_day(price_frame(), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        def transport(url, **kwargs):
            return SimpleNamespace(status_code=403, ok=False)
        def streamed(day, wanted):
            self.assertEqual(wanted, ["INFY.NS"])
            return {"INFY.NS": records(day)}
        with patch("requests.get", side_effect=transport), \
             patch("smart_chart_eod_recovery.final_archive_rows", side_effect=streamed) as fetch:
            verify_final_archive(complete, now=NOW, expected=TUESDAY)
            self.assertEqual(fetch.call_count, 2)
        with patch("requests.get", side_effect=transport), \
             patch("smart_chart_eod_recovery.final_archive_rows", return_value={}):
            with self.assertRaisesRegex(SystemExit, "archive_unverified"):
                verify_final_archive(complete, now=NOW, expected=TUESDAY)

    def test_public_probe_switches_from_conflicting_fresh_to_exact_official(self):
        from scripts.msk_doctor_public_eod_probe import verify_final_archive
        complete = nse.stitch_one_verified_day(price_frame(), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        primary = complete.copy()
        primary.loc[primary.index[-1], "Volume"] = 9999
        with patch("requests.get", return_value=SimpleNamespace(status_code=403, ok=False)), \
             patch("smart_chart_eod_recovery.final_archive_rows",
                   side_effect=lambda day, wanted: {"INFY.NS": records(day)}), \
             patch("builtins.print") as output:
            recovered = verify_final_archive(primary, now=NOW, expected=TUESDAY,
                alternate_fetch=lambda: self.fail("prior verified; no second provider needed"))
        self.assertEqual(recovered.iloc[-1]["Volume"], 10000)
        self.assertEqual(primary.iloc[-1]["Volume"], 9999)
        self.assertEqual(recovered.attrs["doctor_eod_path"], "primary:official-recovery-conflict")
        self.assertTrue(any("official_ohlcv_mismatch" in str(call) for call in output.call_args_list))

    def test_public_probe_switches_history_only_when_primary_overlap_fails(self):
        from scripts.msk_doctor_public_eod_probe import verify_final_archive
        primary = price_frame()
        primary.loc[primary.index[-1], "Volume"] = 9999
        attempts = []
        def alternate():
            attempts.append(True)
            return price_frame()
        with patch("requests.get", return_value=SimpleNamespace(status_code=403, ok=False)), \
             patch("smart_chart_eod_recovery.final_archive_rows",
                   side_effect=lambda day, wanted: {"INFY.NS": records(day)}):
            recovered = verify_final_archive(primary, now=NOW, expected=TUESDAY,
                                             alternate_fetch=alternate)
            self.assertEqual(attempts, [True])
            self.assertEqual(recovered.attrs["doctor_eod_path"], "alternate:official-recovery-stale")
            self.assertEqual(recovered.iloc[-1]["Close"], 103)
            with self.assertRaisesRegex(SystemExit, "prior_overlap_or_continuity"):
                verify_final_archive(primary, now=NOW, expected=TUESDAY,
                                     alternate_fetch=lambda: primary)
            duplicate = pd.concat([price_frame(), price_frame().iloc[-1:]])
            with self.assertRaisesRegex(SystemExit, "prior_overlap_or_continuity"):
                verify_final_archive(duplicate, now=NOW, expected=TUESDAY,
                                     alternate_fetch=lambda: None)

    def test_public_probe_missing_primary_and_failed_alternative_never_invent(self):
        from scripts.msk_doctor_public_eod_probe import verify_final_archive
        with patch("requests.get", return_value=SimpleNamespace(status_code=403, ok=False)), \
             patch("smart_chart_eod_recovery.final_archive_rows",
                   side_effect=lambda day, wanted: {"INFY.NS": records(day)}):
            recovered = verify_final_archive(None, now=NOW, expected=TUESDAY,
                                             alternate_fetch=price_frame)
            self.assertEqual(recovered.iloc[-1]["Close"], 103)
            def broken():
                raise TimeoutError("offline")
            with self.assertRaisesRegex(SystemExit, "prior_overlap_or_continuity"):
                verify_final_archive(None, now=NOW, expected=TUESDAY, alternate_fetch=broken)

    def test_doctor_live_fresh_conflict_recovers_exact_official_without_mutating_input(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        complete = nse.stitch_one_verified_day(price_frame(), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        bad = complete.copy()
        bad.loc[bad.index[-1], "Volume"] = 9999
        with patch("market_data.bulk_history", return_value={"SAMPLEA": bad}), \
             patch.object(nse, "fetch_nse_bhavcopy_rows",
                 side_effect=lambda day, wanted: {"SAMPLEA": records(day)}) as fetch, \
             patch.object(doctor, "recover_fresh_daily_for_doctor") as alt, \
             patch.object(doctor, "datetime", Clock):
            rows, frames = doctor.price_holdings(
                [{"symbol":"SAMPLEA", "qty":2, "buy":90}], include_frames=True)
        self.assertEqual(fetch.call_count, 2)
        alt.assert_not_called()
        self.assertEqual(rows[0]["close"], 103)
        self.assertEqual(frames["SAMPLEA"].iloc[-1]["Volume"], 10000)
        self.assertEqual(bad.iloc[-1]["Volume"], 9999)
        self.assertTrue(rows[0]["price_source"].startswith("NSE EOD"))

    def test_doctor_live_bad_prior_uses_only_officially_reconciled_alternate(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        complete = nse.stitch_one_verified_day(price_frame(), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        bad = complete.copy()
        bad.loc[bad.index[-2:], "Volume"] = 9999
        actual_recover = doctor.recover_fresh_daily_for_doctor
        for alternate, succeeds in ((price_frame(), True), (bad, False)):
            with self.subTest(succeeds=succeeds), \
                 patch("market_data.bulk_history", return_value={"SAMPLEA": bad}), \
                 patch.object(nse, "fetch_nse_bhavcopy_rows",
                     side_effect=lambda day, wanted: {"SAMPLEA": records(day)}) as fetch, \
                 patch.object(doctor, "recover_fresh_daily_for_doctor",
                     side_effect=lambda sym, **kw: actual_recover(
                         sym, fetchers=(lambda: alternate,), **kw)) as alt, \
                 patch.object(doctor, "datetime", Clock):
                holdings = [{"symbol":"SAMPLEA", "qty":2, "buy":90, "exchange":"NSE"}]
                if succeeds:
                    rows = doctor.price_holdings(holdings)
                    self.assertEqual(rows[0]["close"], 103)
                    self.assertIn("alternate prior-session", rows[0]["price_source"])
                else:
                    with self.assertRaisesRegex(doctor.PortfolioDoctorError, "NO_PRICES"):
                        doctor.price_holdings(holdings)
                self.assertEqual(fetch.call_count, 2)
                alt.assert_called_once()

    def test_doctor_live_matching_current_is_one_batch_and_conflict_without_prior_fails(self):
        complete = nse.stitch_one_verified_day(price_frame(), prior=records(MONDAY),
            latest=records(TUESDAY), previous_day=MONDAY, expected_day=TUESDAY, now=NOW)
        bad = complete.copy()
        bad.loc[bad.index[-1], "Volume"] = 9999
        with patch.object(nse, "fetch_nse_bhavcopy_rows",
                          return_value={"SAMPLEA": records(TUESDAY)}) as fetch:
            confirmed, repaired, conflicts = nse.crosscheck_completed_doctor_frames(
                {"SAMPLEA": complete}, ["SAMPLEA"], now=NOW)
            self.assertEqual(confirmed, {"SAMPLEA"})
            self.assertEqual((repaired, conflicts), ({}, {}))
            fetch.assert_called_once()
        with patch.object(nse, "fetch_nse_bhavcopy_rows",
                          side_effect=[{"SAMPLEA": records(TUESDAY)}, {}]):
            confirmed, repaired, conflicts = nse.crosscheck_completed_doctor_frames(
                {"SAMPLEA": bad}, ["SAMPLEA"], now=NOW)
            self.assertEqual((confirmed, repaired), (set(), {}))
            self.assertEqual(conflicts["SAMPLEA"], (None, records(TUESDAY)))

    def test_request_data_kept_in_memory_and_no_sensitive_logging(self):
        path = Path(nse.__file__).read_text(encoding="utf-8")
        for banned in ("logging.", "logger.", "psycopg", "sqlite3", "shelve",
                       "NamedTemporaryFile", "mkstemp", "open(path", "CREATE TABLE"):
            self.assertNotIn(banned, path)
        self.assertIn("io.StringIO(", path)
        self.assertIn("fetch_nse_bhavcopy_rows", path)


if __name__ == "__main__":
    unittest.main()
