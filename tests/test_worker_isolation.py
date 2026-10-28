"""Verify isolation and direct-handler rate limiting from production main.py AST."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
DIRECT = ("cmd_report", "cmd_chart", "cmd_status", "cmd_buzz",
          "cmd_sectors", "cmd_portfolio", "cmd_buy", "scan_btn",
          "breadth_btn", "swing_btn", "chart_button", "buzz_btn", "sector_btn")


def function(name):
    return next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def helper_environment(allowed):
    state = Mock()
    limiter = Mock()
    limiter.is_allowed.return_value = allowed
    safe_send = Mock()
    node = function("_allow_heavy")
    namespace = {
        "state": state,
        "API_RATE_LIMITER": limiter,
        "safe_send": safe_send,
        "RATE_LIMIT_WINDOW": 60,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<helper>", "exec"), namespace)
    return namespace, state, limiter, safe_send


class IsolationTests(unittest.TestCase):
    def test_webhook_pool_is_not_heavy_job_executor(self):
        root_assign = ast.get_source_segment(SOURCE, TREE)
        self.assertIn("webhook_executor = ThreadPoolExecutor(", root_assign)
        self.assertIn("executor = ThreadPoolExecutor(", root_assign)
        webhook_source = ast.get_source_segment(SOURCE, function("webhook"))
        self.assertIn("webhook_executor.submit(_process_webhook", webhook_source)
        self.assertNotIn("future = executor.submit(_process_webhook", webhook_source)
        self.assertIn("_webhook_slots = threading.BoundedSemaphore(value=16)", SOURCE)

    def test_all_heavy_buttons_and_commands_are_rate_limited_first(self):
        for name in DIRECT:
            node = function(name)
            first = node.body[0]
            self.assertIsInstance(first, ast.If, name)
            self.assertIn("_allow_heavy", ast.get_source_segment(SOURCE, first.test), name)
            self.assertIsInstance(first.body[0], ast.Return, name)

    def test_rate_limit_rejects_direct_action_and_clears_stale_state(self):
        ns, state, limiter, sender = helper_environment(False)
        msg = SimpleNamespace(chat=SimpleNamespace(id=999),
                              from_user=SimpleNamespace(id=123))
        self.assertFalse(ns["_allow_heavy"](msg))
        state.clear.assert_called_once_with(999)
        limiter.is_allowed.assert_called_once_with(123)
        sender.assert_called_once()
        self.assertIn("Rate limited", sender.call_args.args[1])

    def test_permitted_direct_action(self):
        ns, state, limiter, sender = helper_environment(True)
        msg = SimpleNamespace(chat=SimpleNamespace(id=10),
                              from_user=SimpleNamespace(id=20))
        self.assertTrue(ns["_allow_heavy"](msg))
        sender.assert_not_called()

    def test_inline_callback_is_rate_limited(self):
        callback = ast.get_source_segment(SOURCE, function("handle_analysis_callback"))
        self.assertIn("API_RATE_LIMITER.is_allowed(uid)", callback)
        self.assertIn("state.clear(chat_id)", callback)


if __name__ == "__main__":
    unittest.main()
