"""Exercise actual Doctor Telegram handlers in isolation, without network or DB."""
import ast
import copy
import io
import logging
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import portfolio_doctor_live as doctor

SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def _function(name, env):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(SOURCE), "exec"), env)
    return env[name]


def _message(uid, text=None):
    return SimpleNamespace(chat=SimpleNamespace(id=uid, type="private"),
                           from_user=SimpleNamespace(id=uid), text=text)


class FakeBot:
    def __init__(self):
        self.sent = []
    def send_document(self, uid, handle, caption, timeout=None, protect_content=None,
                      reply_markup=None):
        self.delivery_options = (timeout, protect_content)
        self.research_markup = reply_markup
        self.last_handle = handle
        data = handle.read()
        self.sent.append((uid, data, caption))
        return SimpleNamespace(message_id=4201 + uid, document=SimpleNamespace(file_id="temporary-platform-file"))
    def get_file(self, file_id):
        raise AssertionError("typed report must not fetch Telegram upload")
    def download_file(self, file_path):
        raise AssertionError("typed report must not download any Telegram upload")


class SessionTests(unittest.TestCase):
    def _env(self, *, user_ids=(101,), executor=None):
        bot = FakeBot()
        notices = []
        class FakeMarkup:
            def __init__(self, row_width=2):
                self.row_width = row_width
                self.buttons = []
            def add(self, *buttons):
                self.buttons.extend(buttons)

        button = lambda text, callback_data: SimpleNamespace(
            text=text, callback_data=callback_data)
        env = {
            "types": SimpleNamespace(InlineKeyboardMarkup=FakeMarkup,
                                     InlineKeyboardButton=button),
            "threading": threading, "time": time,
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_waiting": {uid: time.monotonic()+180 for uid in user_ids},
            "_portfolio_audit_mode": {},
            "_portfolio_doctor_running": set(),
            "_portfolio_doctor_slots": threading.BoundedSemaphore(2),
            "_doctor_allowed": lambda m: True,
            "_builder_form": {},
            "portfolio_builder_form_text": lambda m, text: False,
            "safe_send": lambda uid, msg: notices.append((uid, msg)),
            "bot": bot, "executor": executor or SimpleNamespace(submit=lambda fn: fn()),
            "logger": logging.getLogger("doctor-private-test"),
        }
        return env, bot, notices

    def test_doctor_switch_cancels_old_builder_and_routes_real_holdings(self):
        env, _, notices = self._env(user_ids=())
        env["_builder_form"][101] = {"step": "stocks", "until": time.monotonic() + 180}
        env["_portfolio_audit_mode"][101] = time.monotonic() + 180
        start = _function("portfolio_doctor_start", env)
        start(_message(101, "🩺 Portfolio Doctor"))
        self.assertEqual(env["_builder_form"], {})
        self.assertEqual(env["_portfolio_audit_mode"], {})
        self.assertGreater(env["_portfolio_doctor_waiting"][101], time.monotonic())
        self.assertIn("Portfolio Doctor", notices[-1][1])

        raw = "Sbin 100 609\\nBhel 124 90\\nVedl 68 120"
        routed, builder_calls = [], []
        env.update(
            API_RATE_LIMITER=SimpleNamespace(is_allowed=lambda _uid: True),
            portfolio_doctor_text=lambda m: routed.append(m.text),
            portfolio_builder_form_text=lambda m, text: builder_calls.append(text) or True,
        )
        handler = _function("handle_text", env)
        handler(_message(101, raw))
        self.assertEqual(routed, [raw])
        self.assertEqual(builder_calls, [])

        # This screenshot input is a 3-column holdings format, not percentages.
        parsed = doctor.parse_holdings("typed_holdings.csv", raw.encode("utf-8"))
        self.assertEqual([(r["symbol"], r["qty"], r["buy"]) for r in parsed],
                         [("SBIN", 100.0, 609.0), ("BHEL", 124.0, 90.0),
                          ("VEDL", 68.0, 120.0)])

    def test_doctor_has_priority_even_if_legacy_builder_state_survives(self):
        env, _, notices = self._env()
        routed, legacy = [], []
        env["_builder_form"][101] = {"step": "stocks", "until": time.monotonic() + 180}
        env.update(
            API_RATE_LIMITER=SimpleNamespace(is_allowed=lambda _uid: True),
            portfolio_doctor_text=lambda m: routed.append(m.text),
            portfolio_builder_form_text=lambda m, text: legacy.append(text) or True,
        )
        _function("handle_text", env)(_message(101, "SBIN 100 609"))
        self.assertEqual(routed, ["SBIN 100 609"])
        self.assertEqual(legacy, [])
        self.assertEqual(notices, [])

    def test_in_memory_pdf_delivery_clears_state_after_success(self):
        env, bot, notices = self._env()
        fn = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          return_value=b"%PDF-" + b"ok" * 1500) as run:
            fn(_message(101, "SAMPLEA 7 120"), _typed_payload=b"SAMPLEA 7 120")
        run.assert_called_once_with("typed_holdings.csv", b"SAMPLEA 7 120", timeout=150)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.delivery_options, (30, True))
        self.assertTrue(bot.last_handle.closed)
        self.assertEqual([button.callback_data for button in bot.research_markup.buttons],
                         ["doctorpdf|SAMPLEA"])
        self.assertNotIn("7 120", str(bot.research_markup.buttons))
        self.assertEqual(bot.sent[0][0], 101)
        self.assertTrue(bot.sent[0][1].startswith(b"%PDF-"))
        self.assertEqual(env["_portfolio_doctor_running"], set())
        self.assertEqual(env["_portfolio_doctor_waiting"], {})
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertFalse(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertNotIn("SAMPLEA", str(notices))

    def test_timeout_keeps_private_data_out_of_user_error_and_releases_slot(self):
        env, bot, notices = self._env()
        fn = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          side_effect=doctor.PortfolioDoctorError("TIMEOUT")):
            fn(_message(101), _typed_payload=b"SAMPLEA 7 120")
        self.assertEqual(bot.sent, [])
        self.assertEqual(env["_portfolio_doctor_running"], set())
        self.assertEqual(env["_portfolio_doctor_waiting"], {})
        self.assertIn("timed out", notices[-1][1])
        self.assertNotIn("SAMPLEA", str(notices))

    def test_multiple_clients_isolated_and_busy_client_can_retry(self):
        pending = []
        executor = SimpleNamespace(submit=lambda fn: pending.append(fn))
        env, bot, notices = self._env(user_ids=(101, 102, 103), executor=executor)
        fn = _function("portfolio_doctor_document", env)
        fn(_message(101), _typed_payload=b"SAMPLEA 7 120")
        fn(_message(102), _typed_payload=b"SAMPLEB 8 230")
        fn(_message(103), _typed_payload=b"SAMPLEC 9 340")
        self.assertEqual(len(pending), 2)
        self.assertEqual(env["_portfolio_doctor_running"], {101, 102})
        self.assertIn(103, env["_portfolio_doctor_waiting"])
        self.assertIn("busy", notices[-1][1])
        def distinct_report(name, payload, timeout):
            return b"%PDF-" + payload + b"_"*3000
        with patch.object(doctor, "run_isolated_memory", side_effect=distinct_report):
            for work in pending:
                work()
        self.assertEqual([q[0] for q in bot.sent], [101, 102])
        self.assertIn(b"SAMPLEA", bot.sent[0][1])
        self.assertNotIn(b"SAMPLEB", bot.sent[0][1])
        self.assertIn(b"SAMPLEB", bot.sent[1][1])
        self.assertNotIn(b"SAMPLEA", bot.sent[1][1])
        self.assertEqual(env["_portfolio_doctor_running"], set())

    def test_expired_waiting_request_discards_state_without_delivery(self):
        env, bot, _ = self._env()
        env["_portfolio_doctor_waiting"][101] = time.monotonic() - 1
        fn = _function("portfolio_doctor_document", env)
        fn(_message(101), _typed_payload=b"SAMPLEA 7 120")
        self.assertEqual(env["_portfolio_doctor_waiting"], {})
        self.assertEqual(bot.sent, [])

    def test_invalid_typed_input_preserves_waiting_request(self):
        env, _, notices = self._env()
        env["portfolio_doctor_document"] = lambda *a, **kw: self.fail("must not launch")
        fn = _function("portfolio_doctor_text", env)
        deadline = env["_portfolio_doctor_waiting"][101]
        fn(_message(101, text="NOT VALID"))
        self.assertEqual(env["_portfolio_doctor_waiting"], {101: deadline})
        self.assertTrue(notices)

    def test_catchall_routes_doctor_input_and_while_running_blocks_generic_analysis(self):
        env, _, notices = self._env(user_ids=(101, 102))
        routed = []
        env.update(
            API_RATE_LIMITER=SimpleNamespace(is_allowed=lambda _uid: True),
            portfolio_doctor_text=lambda m: routed.append(m.chat.id))
        handler = _function("handle_text", env)
        handler(_message(101, text="SAMPLEA 7 120"))
        self.assertEqual(routed, [101])
        env["_portfolio_doctor_waiting"].pop(102, None)
        env["_portfolio_doctor_running"].add(102)
        handler(_message(102, text="SAMPLEB 8 230"))
        self.assertIn("already running", notices[-1][1])
        self.assertEqual(routed, [101])


    def test_missing_owner_session_recovers_only_authorized_private_tester(self):
        """A restart-lost session must not redirect the owner's five holdings."""
        raw = "BEL 10 100\\nreliance 50 800\\nsbin 150 865.5\\nDLF 500 458\\nSJVN 40000 15"
        env, _, notices = self._env(user_ids=())
        routed = []
        env.update(
            os=os,
            API_RATE_LIMITER=SimpleNamespace(is_allowed=lambda _uid: True),
            portfolio_doctor_text=lambda m: routed.append(m.text),
        )
        handler = _function("handle_text", env)
        flags = {"MSK_PORTFOLIO_DOCTOR_TEST_ENABLED": "1",
                 "MSK_PORTFOLIO_DOCTOR_TEST_USER_ID": "101"}
        with patch.dict(os.environ, flags):
            handler(_message(101, text=raw))
        self.assertEqual(routed, [raw])
        self.assertGreater(env["_portfolio_doctor_waiting"][101], time.monotonic())
        self.assertEqual(notices, [])

        # Another account is never routed to the Doctor via recovery.
        env["_portfolio_doctor_waiting"].clear()
        with patch.dict(os.environ, flags):
            handler(_message(102, text=raw))
        self.assertEqual(routed, [raw])
        self.assertNotIn(102, env["_portfolio_doctor_waiting"])
        self.assertIn("enabled private", notices[-1][1])

    def test_missing_owner_session_is_not_recovered_when_test_disabled(self):
        env, _, notices = self._env(user_ids=())
        routed = []
        env.update(os=os,
                   API_RATE_LIMITER=SimpleNamespace(is_allowed=lambda _uid: True),
                   portfolio_doctor_text=lambda m: routed.append(m.text))
        handler = _function("handle_text", env)
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_TEST_ENABLED": "0",
                                    "MSK_PORTFOLIO_DOCTOR_TEST_USER_ID": "101"}):
            handler(_message(101, text="BEL 10 100"))
        self.assertEqual(routed, [])
        self.assertEqual(env["_portfolio_doctor_waiting"], {})
        self.assertIn("enabled private", notices[-1][1])

    def test_upload_delivery_uses_memory_and_closes_pdf(self):
        env, bot, _ = self._env()
        payload = b"SAMPLEA,7,120"
        bot.get_file = lambda _: SimpleNamespace(file_size=len(payload), file_path="platform-upload")
        bot.download_file = lambda _: payload
        message = _message(101)
        message.document = SimpleNamespace(file_name="private.csv", file_size=len(payload),
                                           file_id="platform-file")
        fn = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          return_value=b"%PDF-" + b"_" * 3000) as run:
            fn(message)
        run.assert_called_once_with("private.csv", payload, timeout=150)
        self.assertEqual(len(bot.sent), 1)
        self.assertTrue(bot.last_handle.closed)
        self.assertEqual(env["_portfolio_doctor_running"], set())

    def test_invalid_delivery_receipt_does_not_retry_pdf(self):
        env, bot, notices = self._env()
        calls = []
        def ambiguous(uid, handle, **options):
            calls.append(handle)
            return SimpleNamespace(message_id=None, document=None)
        bot.send_document = ambiguous
        fn = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          return_value=b"%PDF-" + b"_" * 3000):
            fn(_message(101), _typed_payload=b"SAMPLEA 7 120")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].closed)
        self.assertEqual(env["_portfolio_doctor_running"], set())
        self.assertEqual(env["_portfolio_doctor_waiting"], {})
        self.assertTrue(notices)
        self.assertNotIn("SAMPLEA", str(notices))

    def test_executor_rejection_releases_capacity_and_request_state(self):
        def rejected(fn):
            raise RuntimeError("executor unavailable")
        env, bot, notices = self._env(executor=SimpleNamespace(submit=rejected))
        fn = _function("portfolio_doctor_document", env)
        fn(_message(101), _typed_payload=b"SAMPLEA 7 120")
        self.assertFalse(bot.sent)
        self.assertEqual(env["_portfolio_doctor_running"], set())
        self.assertIn(101, env["_portfolio_doctor_waiting"])
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertTrue(env["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertFalse(env["_portfolio_doctor_slots"].acquire(blocking=False))



    def test_disabled_or_expired_holdings_never_reach_stock_lookup(self):
        for enabled in (True, False):
            for text in ("SAMPLEA 7 120", "SAMPLEA,7,120",
                         "SAMPLEA;7;120", "SAMPLEA -7 nan",
                         "Symbol,Qty,Buy Price\nSAMPLEA,7,120"):
                with self.subTest(enabled=enabled, text=text):
                    env, bot, notices = self._env()
                    env["_portfolio_doctor_waiting"][101] = time.monotonic() - 1
                    env["_doctor_allowed"] = lambda m, value=enabled: value
                    env["API_RATE_LIMITER"] = SimpleNamespace(is_allowed=lambda uid: True)
                    env["state"] = SimpleNamespace(get=lambda uid: "analysis")
                    def prohibited(*args, **kwargs):
                        self.fail("private holdings reached generic stock/provider lookup")
                    env["send_symbol_menu"] = prohibited
                    env["executor"] = SimpleNamespace(submit=prohibited)
                    handler = _function("handle_text", env)
                    handler(_message(101, text=text))
                    self.assertEqual(env["_portfolio_doctor_waiting"], {})
                    self.assertEqual(bot.sent, [])
                    self.assertIn("Portfolio Doctor session", notices[-1][1])
                    self.assertNotIn("SAMPLEA", str(notices))

    def test_holdings_classifier_keeps_ordinary_stock_queries_available(self):
        for text in ("INFY", "NSE:SBIN", "M&M", "Tata Motors",
                     "Lloyds Metals and Energy", "3M India Limited", "hello"):
            with self.subTest(text=text):
                self.assertFalse(doctor.looks_like_holdings_text(text))
        for text in ("SAMPLEA 7 120", '"SAMPLEA",7,120',
                     "SAMPLEA 7 120 extra", "SAMPLEA 7 nan",
                     "SAMPLEA\t7\t120", "SAMPLEA.NS 7 120"):
            with self.subTest(text=text):
                self.assertTrue(doctor.looks_like_holdings_text(text))



    def test_expired_or_disabled_session_blocks_even_unrecognised_text(self):
        for expired, enabled in ((True, True), (True, False), (False, False)):
            env, _, notices = self._env()
            if expired:
                env["_portfolio_doctor_waiting"][101] = time.monotonic() - 1
            env["_doctor_allowed"] = lambda m, value=enabled: value
            env["API_RATE_LIMITER"] = SimpleNamespace(is_allowed=lambda uid: True)
            def prohibited(*args, **kwargs):
                self.fail("private session input escaped to generic analysis")
            env["executor"] = SimpleNamespace(submit=prohibited)
            env["state"] = SimpleNamespace(get=prohibited)
            env["portfolio_doctor_text"] = prohibited
            fn = _function("handle_text", env)
            fn(_message(101, text="private malformed row without numeric fields"))
            self.assertEqual(env["_portfolio_doctor_waiting"], {})
            self.assertIn("Portfolio Doctor session", notices[-1][1])
            self.assertNotIn("private malformed", str(notices))

    def test_typed_csv_header_does_not_reduce_five_holding_capacity(self):
        env, _, _ = self._env()
        delivered = []
        env["portfolio_doctor_document"] = lambda m, **kw: delivered.append(kw["_typed_payload"])
        raw = "Symbol,Qty,Buy Price\n" + "\n".join(
            "SAMPLE" + str(i) + ",2,100" for i in range(5))
        fn = _function("portfolio_doctor_text", env)
        fn(_message(101, text=raw))
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(doctor.parse_holdings("input.csv", delivered[0])), 5)

    def test_running_private_job_stays_private_after_access_flag_changes(self):
        env, _, notices = self._env()
        env["_portfolio_doctor_waiting"].clear()
        env["_portfolio_doctor_running"].add(101)
        env["_doctor_allowed"] = lambda m: False
        env["API_RATE_LIMITER"] = SimpleNamespace(is_allowed=lambda uid: True)
        env["state"] = SimpleNamespace(get=lambda uid: self.fail("generic lookup reached"))
        fn = _function("handle_text", env)
        fn(_message(101, text="private incomplete request"))
        self.assertIn("already running", notices[-1][1])


if __name__ == "__main__":
    unittest.main()
