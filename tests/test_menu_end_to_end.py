"""Offline black-box-like MSK Telegram menu tests against real production handlers.

The actual main.py handler functions and decorator predicates are extracted via
AST. All external APIs and Telegram delivery are mocked, so this test never
accesses a user account, internet, exchange, real holdings, or tokens.
"""
import ast
import copy
from datetime import datetime
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from msk_daily_reports import parse_request as parse_daily_report_request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

CHAT = 70001
SENDER = 70001


def function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def actual_menu_labels(name="main_keyboard"):
    """Read only labels on the keyboard; do not duplicate the expected menu."""
    node = function(name)
    labels = []
    for statement in node.body:
        if (not isinstance(statement, ast.Expr)
                or not isinstance(statement.value, ast.Call)):
            continue
        call = statement.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "add":
            continue
        for argument in call.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                labels.append(argument.value)
            else:
                raise AssertionError("Unexpected computed menu label")
    return labels


def matching_handlers(label):
    """Evaluate the actual decorator predicates in Python source order."""
    message = SimpleNamespace(
        text=label, chat=SimpleNamespace(id=CHAT, type="private"),
        from_user=SimpleNamespace(id=SENDER),
    )
    names = []
    for node in TREE.body:
        if not isinstance(node, ast.FunctionDef) or node.name == "handle_text":
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not (isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "message_handler"):
                continue
            predicate_node = next(
                (kw.value for kw in decorator.keywords if kw.arg == "func"),
                None,
            )
            if predicate_node is None:
                continue
            predicate = eval(compile(ast.Expression(body=predicate_node),
                                     "<handler-predicate>", "eval"), {
                                         "is_member_message": lambda m: False,
                                         "extract_stock_request": lambda text: None,
                                         "parse_daily_report_request": parse_daily_report_request,
                                     })
            if predicate(message):
                names.append(node.name)
    return names


class FakeState:
    def __init__(self):
        self.values = {}

    def clear(self, chat_id):
        self.values.pop(chat_id, None)

    def set(self, chat_id, value):
        self.values[chat_id] = value


class ImmediateExecutor:
    def submit(self, func, *args, **kwargs):
        func(*args, **kwargs)
        return SimpleNamespace(done=lambda: True)


class NoStartThread:
    def __init__(self, target, daemon=False, **kwargs):
        self.target = target

    def start(self):
        # Deliberately suppress 15-second "still scanning" timer thread.
        return None


def isolated_environment():
    output = []
    state = FakeState()
    class FakeGenerator:
        def send_to_telegram(self, bot, chat_id):
            output.append((chat_id, "📈 Mock chart ready"))

    def send(chat_id, text, **kwargs):
        output.append((chat_id, str(text)))

    namespace = {
        "safe_send": send,
        "executor": ImmediateExecutor(),
        "state": state,
        "_allow_heavy": lambda m: True,
        "main_keyboard": lambda: object(),
        "stock_research_keyboard": lambda: object(),
        "top_five_keyboard": lambda: object(),
        "get_chart_generator": lambda: FakeGenerator(),
        "build_scan": lambda profile: "Mock research basket: " + profile,
        "request_index_top5": lambda profile, chat, send: "Mock full-index prepared research: " + profile,
        "render_latest_profile": lambda profile: "Mock full-index prepared research: " + profile,
        "_require_private_portfolio": lambda m: True,
        "_doctor_allowed": lambda m: True,
        "_builder_allowed": lambda m: True,
        "_builder_form": {},
        "_portfolio_doctor_lock": threading.Lock(),
        "_portfolio_doctor_waiting": {},
        "_portfolio_audit_mode": {},
        "_portfolio_doctor_running": set(),
        "os": __import__("os"),
        "build_breadth": lambda: "Mock breadth snapshot (offline fixture)",
        "build_intraday_trade_report": lambda: "Mock intraday: NSE CLOSED — NO TRADE",
        "get_swing_trades": lambda mode: "Mock swing scan: " + mode,
        "build_portfolio_card": lambda uid: "Mock portfolio: no real holdings",
        "test_ai_providers": lambda: {"_status": "MOCK/OFFLINE"},
        "run_buzz": lambda cid: send(cid, "Mock Stocks Buzz"),
        "run_sector_report": lambda cid: send(cid, "Mock sector PDF acknowledgement"),
        "logger": logging.getLogger("msk-button-test"),
        "parse_daily_report_request": parse_daily_report_request,
        "datetime": datetime,
        "threading": SimpleNamespace(Thread=NoStartThread),
        "time": SimpleNamespace(sleep=lambda seconds: None, monotonic=lambda: 1000.0),
        "bot": SimpleNamespace(),
    }
    # Runtime delegation is real: port_btn -> cmd_portfolio, stat_btn -> cmd_status.
    names = {
        "cmd_start", "cmd_menu", "cmd_portfolio", "chart_button", "port_btn",
        "scan_btn", "breadth_btn", "swing_btn", "intraday_btn", "analysis_btn", "report_btn",
        "deliver_daily_reports", "plain_daily_report",
        "buzz_btn", "sector_btn", "portfolio_doctor_start", "portfolio_builder_button",
        "stock_research_menu", "top_five_menu", "mutual_fund_doctor_status", "back_menu",
    }
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            fresh = copy.deepcopy(node)
            fresh.decorator_list = []
            module = ast.fix_missing_locations(ast.Module(body=[fresh], type_ignores=[]))
            exec(compile(module, "<isolated-main>", "exec"), namespace)
    return namespace, output, state


class MainMenuAcceptanceTests(unittest.TestCase):
    def test_every_main_menu_button_reaches_one_direct_handler(self):
        labels = actual_menu_labels()
        self.assertEqual(labels, ["🏗️ Portfolio Builder", "🩺 Portfolio Doctor",
                                  "🔍 Stock Research", "🔥 Stocks Buzz",
                                  "🏆 Top 5 Stocks", "💰 Mutual Fund Doctor"])
        self.assertNotIn("💼 Portfolio", labels)
        self.assertIn("🩺 Portfolio Doctor", labels)
        self.assertIn("🏗️ Portfolio Builder", labels)
        self.assertEqual(len(labels), len(set(labels)))
        for label in labels:
            with self.subTest(button=label):
                matches = matching_handlers(label)
                self.assertEqual(len(matches), 1,
                                 "Menu button must map to one non-catchall handler")
                self.assertIn(matches[0], isolated_environment()[0])

    def test_every_menu_button_produces_a_reply_or_useful_prompt(self):
        for label in dict.fromkeys(actual_menu_labels() + actual_menu_labels("stock_research_keyboard") + actual_menu_labels("top_five_keyboard")):
            with self.subTest(button=label):
                namespace, output, state = isolated_environment()
                message = SimpleNamespace(
                    text=label, chat=SimpleNamespace(id=CHAT),
                    from_user=SimpleNamespace(id=SENDER),
                )
                namespace[matching_handlers(label)[0]](message)
                self.assertTrue(output, "Button must acknowledge or return data")
                self.assertTrue(all(chat_id == CHAT for chat_id, _ in output))
                self.assertTrue(any(len(msg.strip()) > 5 for _, msg in output))
                self.assertFalse(any("Traceback" in msg for _, msg in output))

    def test_submenus_preserve_every_existing_tool_and_version_button(self):
        research = actual_menu_labels("stock_research_keyboard")
        top = actual_menu_labels("top_five_keyboard")
        self.assertEqual(set(research), {
            "🔍 Analysis", "📄 Report", "📈 Chart", "🩺 Market Health",
            "🏭 Sector Report", "⚡ Intraday Trade", "🎯 Swing (Safe)",
            "🚀 Swing (Agr)", "🆕 New Version", "🔙 Menu"})
        self.assertEqual(set(top), {
            "🏦 Large Cap Top 5", "⚖️ Mid Cap Top 5", "🚀 Small Cap Top 5", "🔙 Menu"})
        for label in set(research + top):
            matches = matching_handlers(label)
            self.assertEqual(len(matches), 1)
            self.assertIn(matches[0], isolated_environment()[0])

    def test_fund_placeholder_never_runs_research(self):
        env, output, _ = isolated_environment()
        env["mutual_fund_doctor_status"](SimpleNamespace(chat=SimpleNamespace(id=CHAT)))
        self.assertEqual(len(output), 1)
        self.assertIn("not available yet", output[0][1])

    def test_legacy_portfolio_button_is_retired_without_touching_storage(self):
        self.assertNotIn("💼 Portfolio", actual_menu_labels())
        self.assertEqual(matching_handlers("💼 Portfolio"), ["port_btn"])
        ns, output, _ = isolated_environment()
        ns["port_btn"](SimpleNamespace(text="💼 Portfolio",
            chat=SimpleNamespace(id=CHAT), from_user=SimpleNamespace(id=SENDER)))
        self.assertIn("temporarily hidden", output[0][1])
        self.assertNotIn("Mock portfolio:", output[0][1])

    def test_new_intraday_button_is_on_demand_only(self):
        self.assertEqual(matching_handlers("⚡ Intraday Trade"), ["intraday_btn"])
        ns, output, _ = isolated_environment()
        self.assertEqual(output, [])
        ns["intraday_btn"](SimpleNamespace(text="⚡ Intraday Trade",
            chat=SimpleNamespace(id=CHAT, type="private"),
            from_user=SimpleNamespace(id=SENDER)))
        self.assertEqual(len(output), 1)
        self.assertIn("NSE CLOSED", output[0][1])
        self.assertIn("NO TRADE", output[0][1])
        self.assertEqual(len([x for x in actual_menu_labels("stock_research_keyboard")
                              if x == "⚡ Intraday Trade"]), 1)

    def test_model_portfolio_buttons_keep_separate_risk_profiles(self):
        for label, profile in (
            ("🏦 Large Cap Top 5", "conservative"),
            ("⚖️ Mid Cap Top 5", "moderate"),
            ("🚀 Small Cap Top 5", "aggressive"),
        ):
            with self.subTest(profile=profile):
                env, output, state = isolated_environment()
                env[matching_handlers(label)[0]](
                    SimpleNamespace(text=label, chat=SimpleNamespace(id=CHAT))
                )
                self.assertIn("Mock full-index prepared research: " + profile,
                              "\n".join(msg for _, msg in output))

    def test_sell_is_throttled_before_any_storage_mutation(self):
        handler = function("cmd_sell")
        first = handler.body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIn("_allow_heavy", ast.unparse(first.test))
        self.assertIsInstance(first.body[0], ast.Return)
        isolated = copy.deepcopy(handler)
        isolated.decorator_list = []
        storage = Mock()
        ns = {
            "_allow_heavy": lambda m: False,
            "portfolio": storage,
            "safe_send": Mock(),
        }
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=[isolated], type_ignores=[])),
            "<sell-fixture>", "exec"), ns)
        ns["cmd_sell"](SimpleNamespace(
            text="/sell SBIN", chat=SimpleNamespace(id=CHAT)))
        storage.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
