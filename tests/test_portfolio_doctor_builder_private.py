"""Real Doctor Builder parser, arithmetic, private handler and PDF safety tests."""
import ast
import copy
import io
import os
from datetime import datetime, date
from pathlib import Path
import logging
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import pandas as pd
from pypdf import PdfReader

import portfolio_doctor_builder_private as builder
import portfolio_doctor_live as live

INPUT = ("/doctor_builder\n"
         "CAPITAL=1000 HORIZON=36 RISK=medium CASH=200 STOCKCAP=40 SECTORCAP=50 EXCLUDE=BADSTK\n"
         "SAMPLEA 30\n"
         "SAMPLEB 20")
TODAY = datetime(2026, 11, 17, 17, 10, tzinfo=live.IST)


def frames():
    days = pd.bdate_range(end="2026-11-17", periods=80)
    return {
        "SAMPLEA": pd.DataFrame(
            {"Open": [99]*80, "High": [102]*80, "Low": [98]*80,
             "Close": [100]*80, "Volume": [1000]*80}, index=days),
        "SAMPLEB": pd.DataFrame(
            {"Open": [124]*80, "High": [126]*80, "Low": [123]*80,
             "Close": [125]*80, "Volume": [1000]*80}, index=days),
    }


class BuilderParserTests(unittest.TestCase):
    def test_one_shot_required_preferences_exclusions_and_candidates(self):
        spec = builder.parse_builder_request(INPUT)
        self.assertEqual(spec["horizon_months"], 36)
        self.assertEqual(spec["excluded"], ["BADSTK"])
        self.assertEqual(spec["risk_tolerance"], "medium")
        self.assertEqual([x["symbol"] for x in spec["targets"]], ["SAMPLEA", "SAMPLEB"])
        self.assertEqual([x["target_weight_pct"] for x in spec["targets"]], [30.0, 20.0])

    def test_reject_incomplete_duplicate_or_overweighted_input(self):
        cases = [
            "", "/doctor_builder\nCAPITAL=100\nSAMPLEA 50",
            INPUT.replace("SAMPLEB 20", "SAMPLEA 20"),
            INPUT.replace("SAMPLEB 20", "SAMPLEB 75"),
            INPUT.replace("SAMPLEB 20", "BADSTK 20"),
            INPUT.replace("RISK=medium", "RISK=extreme"),
            INPUT.replace("CASH=200", "CASH=nan"),
            INPUT.replace("HORIZON=36", "HORIZON=2.5"),
            INPUT.replace("HORIZON=36", "HORIZON=36 BADKEY=1"),
            INPUT.replace("/doctor_builder", "/portfolio"),
            INPUT + "\n" + "\n".join("STK%d 1" % i for i in range(5)),
            "Z" * 3000,
        ]
        for text in cases:
            with self.subTest(text=text[:24]), self.assertRaises(builder.BuilderError):
                builder.parse_builder_request(text)

    def test_nothing_saved_or_sent_by_parser(self):
        with patch.object(builder.subprocess, "run") as proc:
            builder.parse_builder_request(INPUT)
            proc.assert_not_called()


class AutomaticSelectionTests(unittest.TestCase):
    def setUp(self):
        self.payload = ("/doctor_builder\nCAPITAL=1000000 HORIZON=18 RISK=medium "
                        "CASH=20000 STOCKCAP=35 SECTORCAP=45 AUTO_SELECT=TRUE")
        self.spec = builder.parse_builder_request(self.payload)
        self.names = ["SAMPLEA", "SAMPLEB", "SAMPLEC", "SAMPLED", "SAMPLEE"]
        self.industries = {name: "Sector-" + name for name in self.names}
        days = pd.bdate_range(end="2026-11-17", periods=250)
        self.history = {}
        for i, name in enumerate(self.names):
            closes = [100 + i * 10 + j * .09 for j in range(250)]
            self.history[name] = pd.DataFrame(
                {"Open": closes, "High": [x + 1 for x in closes],
                 "Low": [x - 1 for x in closes], "Close": closes,
                 "Volume": [100000] * 250}, index=days)

    def universe(self, _profile):
        return {"symbols": self.names, "industries": self.industries, "stale": False}

    def history_fetcher(self, symbols, *_args, **_kw):
        return {sym: self.history[sym] for sym in symbols}

    def assessor(self, symbol, industry, profile, *, today=None):
        self.assertEqual(profile, "moderate")
        self.assertEqual(industry, self.industries[symbol])
        return {"fund_score": 75}, "qualified"

    def test_parse_auto_no_manual_symbols_and_strict_preferences(self):
        self.assertTrue(self.spec["auto_select"])
        self.assertEqual(self.spec["targets"], [])
        for bad in (self.payload + "\nSBIN 25",
                    self.payload.replace("AUTO_SELECT=TRUE", "AUTO_SELECT=FALSE"),
                    self.payload.replace("HORIZON=18", "HORIZON=12"),
                    self.payload.replace("RISK=medium", "RISK=other"),
                    self.payload.replace("STOCKCAP=35", "STOCKCAP=0"),
                    self.payload.replace("CASH=20000", "CASH=2000000")):
            with self.subTest(case=bad[-30:]), self.assertRaises(builder.BuilderError):
                builder.parse_builder_request(bad)

    def test_verified_diversified_shares_cash_caps_and_no_orders(self):
        result = builder.build_scenario_current_closes(
            self.spec, history_fetcher=self.history_fetcher,
            universe_fetcher=self.universe, assessor=self.assessor, now=TODAY)
        self.assertEqual(len(result["proposed"]), 5)
        self.assertEqual(len({r["sector"] for r in result["proposed"]}), 5)
        self.assertTrue(result["no_automatic_trades"])
        self.assertGreaterEqual(result["cash"], 20000)
        self.assertLessEqual(max(r["proposed_total_weight_pct"] for r in result["proposed"]), 35)
        self.assertTrue(all(r["price_date"] == "2026-11-17" for r in result["proposed"]))

    def test_insufficient_evidence_and_stale_candidates_refuse_picks(self):
        with self.assertRaises(builder.BuilderError) as caught:
            builder.build_scenario_current_closes(
                self.spec, history_fetcher=self.history_fetcher,
                universe_fetcher=self.universe,
                assessor=lambda *a, **kw: (None, "quarterly_missing"), now=TODAY)
        self.assertEqual(caught.exception.code, "NO_QUALIFIED_PICKS")
        bad = dict(self.history)
        for sym in self.names[:3]:
            bad[sym] = bad[sym].iloc[:-1]
        with self.assertRaises(builder.BuilderError) as caught:
            builder.build_scenario_current_closes(
                self.spec, history_fetcher=lambda *a, **kw: bad,
                universe_fetcher=self.universe, assessor=self.assessor, now=TODAY)
        self.assertEqual(caught.exception.code, "NO_QUALIFIED_PICKS")

    def test_stale_official_universe_and_limits_fail_closed(self):
        with self.assertRaises(builder.BuilderError) as caught:
            builder.build_scenario_current_closes(
                self.spec, history_fetcher=self.history_fetcher,
                universe_fetcher=lambda *a: dict(self.universe("moderate"), stale=True),
                assessor=self.assessor, now=TODAY)
        self.assertEqual(caught.exception.code, "NO_QUALIFIED_PICKS")
        constrained = dict(self.spec, stock_cap_pct="10", sector_cap_pct="5")
        result = builder.build_scenario_current_closes(
            constrained, history_fetcher=self.history_fetcher,
            universe_fetcher=self.universe, assessor=self.assessor, now=TODAY)
        self.assertTrue(all(r["proposed_total_weight_pct"] <= 5 for r in result["proposed"]))
        self.assertGreaterEqual(result["cash"], 20000)


class BuilderVerifiedPriceTests(unittest.TestCase):
    def setUp(self):
        self.spec = builder.parse_builder_request(INPUT)

    def price(self, *, frame=None, resolver=None, now=TODAY):
        records = frames() if frame is None else frame
        def fake_fetch(symbols, *_args, **kwargs):
            self.assertEqual(symbols, ["SAMPLEA", "SAMPLEB"])
            self.assertEqual(kwargs["retries"], 0)
            return records
        return builder.build_scenario_current_closes(
            self.spec, history_fetcher=fake_fetch,
            sector_resolver=resolver or (lambda s: "IT" if s == "SAMPLEA" else "Healthcare"),
            now=now)

    def test_same_completed_price_session_cash_and_whole_share_weights(self):
        data = self.price()
        self.assertEqual([row["shares"] for row in data["proposed"]], [3, 1])
        self.assertEqual([row["price_date"] for row in data["proposed"]],
                         ["2026-11-17", "2026-11-17"])
        self.assertEqual(data["cash"], 575)
        self.assertEqual(data["planned_new_capital"], 1000)
        self.assertAlmostEqual(data["total_weights_pct"], 100, places=8)
        self.assertGreaterEqual(data["cash"], 200)
        self.assertEqual(data["risk_tolerance"], "medium")
        self.assertTrue(data["no_automatic_trades"])

    def test_missing_price_refuses_full_model_not_partial_totals(self):
        with self.assertRaises(builder.BuilderError) as err:
            self.price(frame={"SAMPLEA": frames()["SAMPLEA"]})
        self.assertEqual(err.exception.code, "NO_PRICES")

    def test_stale_completed_candle_refuses_full_model(self):
        records = frames()
        records["SAMPLEB"] = records["SAMPLEB"].iloc[:-1]
        with self.assertRaises(builder.BuilderError) as err:
            self.price(frame=records)
        self.assertEqual(err.exception.code, "STALE_PRICES")

    def test_unknown_sector_refuses_model(self):
        with self.assertRaises(builder.BuilderError) as err:
            self.price(resolver=lambda sym: "📦 Others" if sym == "SAMPLEB" else "IT")
        self.assertEqual(err.exception.code, "UNKNOWN_SECTOR")

    def test_builder_constraint_violation_returns_safe_code(self):
        original = self.spec
        self.spec = dict(original, stock_cap_pct="10")
        with self.assertRaises(builder.BuilderError) as err:
            self.price()
        self.assertEqual(err.exception.code, "CONSTRAINTS")


class PdfAndPrivateHandlerTests(unittest.TestCase):
    def setUp(self):
        spec = builder.parse_builder_request(INPUT)
        data = builder.build_scenario_current_closes(
            spec, history_fetcher=lambda *_args, **_kw: frames(),
            sector_resolver=lambda s: "IT" if s == "SAMPLEA" else "Healthcare", now=TODAY)
        self.spec, self.data = spec, data

    def test_pdf_is_readable_does_not_truncate_cash_or_source_label(self):
        raw = builder.builder_pdf_bytes(self.spec, self.data)
        self.assertTrue(raw.startswith(b"%PDF-"))
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        self.assertTrue(1 <= len(pages) <= 3)
        text = "\n".join(p.extract_text() or "" for p in pages)
        for term in ("PORTFOLIO BUILDER", "Rs 1,000.00", "Cash left",
                     "Target / actual", "SAMPLEA", "SAMPLEB", "completed close",
                     "sector exposure vs. cap", "cash minimum", "No automatic orders"):
            self.assertIn(term.lower(), text.lower())

    def test_isolated_parent_is_stdin_stdout_only(self):
        raw = builder.builder_pdf_bytes(self.spec, self.data)
        with patch.object(builder.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=raw, stderr=b"")) as proc:
            out = builder.run_isolated_builder(INPUT.encode("utf-8"))
        self.assertEqual(out, raw)
        args, kwargs = proc.call_args
        self.assertEqual(kwargs["input"], INPUT.encode("utf-8"))
        self.assertEqual(kwargs["stdout"], builder.subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], builder.subprocess.PIPE)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("cwd", kwargs)

    def test_concurrent_parent_timeout_no_trace_of_customer_text(self):
        with patch.object(builder.subprocess, "run",
                          side_effect=builder.subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaises(builder.BuilderError) as err:
                builder.run_isolated_builder(INPUT.encode("utf-8"), timeout=1)
        self.assertEqual(err.exception.code, "TIMEOUT")
        self.assertNotIn("SAMPLEA", str(err.exception))
        self.assertNotIn("CAPITAL=", str(err.exception))

    def _isolated_handler(self, sent, *, running=None):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and
                    n.name == "portfolio_doctor_builder_command")
        node = copy.deepcopy(node)
        node.decorator_list = []
        class FakeBot:
            def send_document(self, uid, handle, caption, *, protect_content):
                if not protect_content:
                    raise AssertionError("private PDF must be protected")
                sent.append((uid, handle.read()))
                return SimpleNamespace(message_id=12345,
                                       document=SimpleNamespace(file_id="telegram"))
        notices = []
        state = running if running is not None else set()
        env = {
            "os": os, "threading": threading, "io": io,
            "bot": FakeBot(), "_doctor_allowed": lambda _m: True, "_builder_allowed": lambda _m: True,
            "safe_send": lambda uid, txt: notices.append((uid, txt)),
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_running": state,
            "_portfolio_doctor_slots": threading.BoundedSemaphore(2),
            "executor": SimpleNamespace(submit=lambda fn: fn()),
            "logger": logging.getLogger("test-doctor-builder"),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     str(source), "exec"), env)
        return env["portfolio_doctor_builder_command"], env, notices

    def test_actual_command_send_and_cleanup_with_no_storage(self):
        sent = []
        handler, env, notices = self._isolated_handler(sent)
        m = SimpleNamespace(chat=SimpleNamespace(id=101, type="private"),
                            from_user=SimpleNamespace(id=101), text=INPUT)
        raw = builder.builder_pdf_bytes(self.spec, self.data)
        with (patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "1"}),
              patch.object(builder, "run_isolated_builder", return_value=raw)):
            handler(m)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 101)
        self.assertTrue(sent[0][1].startswith(b"%PDF-"))
        self.assertEqual(env["_portfolio_doctor_running"], set())
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertFalse(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertEqual(notices, [])

    def test_builder_flag_off_sends_no_document(self):
        sent = []
        handler, _, notices = self._isolated_handler(sent)
        m = SimpleNamespace(chat=SimpleNamespace(id=101, type="private"),
                            from_user=SimpleNamespace(id=101), text=INPUT)
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "0"}):
            handler(m)
        self.assertEqual(sent, [])
        self.assertIn("disabled", notices[-1][1].lower())

    def test_source_contains_no_holdings_files_or_db_operations(self):
        text = Path(builder.__file__).read_text()
        for bad in ("psycopg.connect(", "sqlite3.connect(", "NamedTemporaryFile(", "shelve.open(",
                    "open(path", "tempfile.mkstemp", "logging.basicConfig(", "CREATE TABLE"):
            self.assertNotIn(bad, text)
        self.assertIn("input=payload", text)
        self.assertIn("stderr=subprocess.PIPE", text)


if __name__ == "__main__":
    unittest.main()
