import unittest
from types import SimpleNamespace
from group_member_filter import is_member_message


def msg(**overrides):
    values = dict(chat=SimpleNamespace(type="supergroup"),
                  from_user=SimpleNamespace(id=123, is_bot=False),
                  sender_chat=None, is_automatic_forward=False,
                  forward_from_chat=None, forward_origin=None)
    values.update(overrides)
    return SimpleNamespace(**values)


class MemberFilterTests(unittest.TestCase):
    def test_real_member(self):
        self.assertTrue(is_member_message(msg()))

    def test_automatic_channel_forward(self):
        self.assertFalse(is_member_message(msg(is_automatic_forward=True)))

    def test_channel_sender(self):
        self.assertFalse(is_member_message(msg(sender_chat=SimpleNamespace(id=-100))))

    def test_anonymous_admin(self):
        self.assertFalse(is_member_message(msg(sender_chat=SimpleNamespace(type="supergroup"))))

    def test_other_bot(self):
        self.assertFalse(is_member_message(msg(from_user=SimpleNamespace(id=1, is_bot=True))))

    def test_forwarded_channel(self):
        self.assertFalse(is_member_message(msg(forward_origin=SimpleNamespace(type="channel"))))

    def test_legacy_channel_forward(self):
        self.assertFalse(is_member_message(msg(forward_from_chat=SimpleNamespace(type="channel"))))

    def test_forward_origin_chat(self):
        self.assertFalse(is_member_message(msg(forward_origin=SimpleNamespace(type="chat"))))

    def test_private_chat(self):
        self.assertFalse(is_member_message(msg(chat=SimpleNamespace(type="private"))))

    def test_missing_sender(self):
        self.assertFalse(is_member_message(msg(from_user=None)))

    def test_executor_shutdown_returns_friendly_error(self):
        import ast
        from pathlib import Path
        from unittest.mock import Mock

        source = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))
        handler = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                       and node.name == "group_stock_review")
        handler.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
        executor = Mock()
        executor.submit.side_effect = RuntimeError("cannot schedule new futures after shutdown")
        limiter = Mock()
        limiter.is_allowed.return_value = True
        send = Mock()
        context = {"is_member_message": is_member_message,
                   "API_RATE_LIMITER": limiter, "extract_stock_request": lambda _: "RELIANCE",
                   "executor": executor, "_group_stock_review": Mock(),
                   "logger": Mock(), "safe_send": send}
        exec(compile(module, "<group-handler-test>", "exec"), context)
        message = msg(text="MSK review Reliance", message_id=456)
        message.chat.id = -100100
        context["group_stock_review"](message)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["reply_to_message_id"], 456)



    def test_unmentioned_member_review_dispatches(self):
        """Exercise the real group handler with a natural-language group message."""
        import ast
        from pathlib import Path
        from unittest.mock import Mock
        from group_stock_requests import extract_stock_request

        source = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))
        handler = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                       and node.name == "group_stock_review")
        handler.decorator_list = []
        isolated = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
        executor, limiter = Mock(), Mock()
        limiter.is_allowed.return_value = True
        ctx = {
            "is_member_message": is_member_message,
            "API_RATE_LIMITER": limiter,
            "extract_stock_request": extract_stock_request,
            "executor": executor,
            "_group_stock_review": Mock(),
            "logger": Mock(),
            "safe_send": Mock(),
        }
        exec(compile(isolated, "<group-handler-test>", "exec"), ctx)
        message = msg(text="Share review on Reliance", message_id=987)
        message.chat.id = -100123
        ctx["group_stock_review"](message)
        executor.submit.assert_called_once_with(ctx["_group_stock_review"], -100123, 987, "RELIANCE")

        # Channel forwards and bot-originated copies must not trigger research.
        forwarded = msg(text="Share review on Reliance", message_id=988, is_automatic_forward=True)
        forwarded.chat.id = -100123
        ctx["group_stock_review"](forwarded)
        executor.submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
