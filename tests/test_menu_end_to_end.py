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

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

CHAT = 70001
SENDER = 70002


def function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def actual_menu_labels():
    """Read only labels on the keyboard; do not duplicate the expected menu."""
    node = function("main_keyboard")
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
        "get_chart_generator": lambda: FakeGenerator(),
        "build_scan": lambda profile: "Mock research basket: " + profile,
        "build_breadth": lambda: "Mock breadth snapshot (offline fixture)",
        "get_swing_trades": lambda mode: "Mock swing scan: " + mode,
        "build_portfolio_card": lambda uid: "Mock portfolio: no real holdings",
        "test_ai_providers": lambda: {"_status": "MOCK/OFFLINE"},
        "run_buzz": lambda cid: send(cid, "Mock Stocks Buzz"),
        "run_sector_report": lambda cid: send(cid, "Mock sector PDF acknowledgement"),
        "logger": logging.getLogger("msk-button-test"),
        "datetime": datetime,
        "threading": SimpleNamespace(Thread=NoStartThread),
        "time": SimpleNamespace(sleep=lambda seconds: None),
        "bot": SimpleNamespace(),
    }
    # Runtime delegation is real: port_btn -> cmd_portfolio, stat_btn -> cmd_status.
    names = {
        "cmd_portfolio", "cmd_status", "chart_button", "port_btn", "stat_btn",
        "scan_btn", "breadth_btn", "swing_btn", "analysis_btn", "report_btn",
        "buzz_btn", "sector_btn",
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
        self.assertGreaterEqual(len(labels), 13)
        self.assertEqual(len(labels), len(set(labels)))
        for label in labels:
            with self.subTest(button=label):
                matches = matching_handlers(label)
                self.assertEqual(len(matches), 1,
                                 "Menu button must map to one non-catchall handler")
                self.assertIn(matches[0], isolated_environment()[0])

    def test_every_menu_button_produces_a_reply_or_useful_prompt(self):
        for label in actual_menu_labels():
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

    def test_model_portfolio_buttons_keep_separate_risk_profiles(self):
        for label, profile in (
            ("🏦 Conservative", "conservative"),
            ("⚖️ Moderate", "moderate"),
            ("🚀 Aggressive", "aggressive"),
        ):
            with self.subTest(profile=profile):
                env, output, state = isolated_environment()
                env[matching_handlers(label)[0]](
                    SimpleNamespace(text=label, chat=SimpleNamespace(id=CHAT))
                )
                self.assertIn("Mock research basket: " + profile,
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
