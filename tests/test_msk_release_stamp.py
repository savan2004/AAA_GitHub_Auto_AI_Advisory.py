import ast
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from msk_release_stamp import release_banner, write_build_stamp

COMMIT = "a" * 40


class ReleaseStampTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "stamp.json"
        self.env = {"RENDER_GIT_COMMIT": COMMIT}

    def test_fixed_ist_stamp_and_rollover(self):
        write_build_stamp(self.path, environ=self.env,
                          now=datetime(2026, 11, 21, 21, 45, tzinfo=timezone.utc))
        result = release_banner(self.path, environ=self.env)
        self.assertIn("2026.11.22", result)
        self.assertIn("22 Nov 2026, 03:15 AM IST", result)
        self.assertIn("aaaaaaa", result)
        self.assertEqual(result, release_banner(self.path, environ=self.env))

    def test_missing_corrupt_naive_and_wrong_commit(self):
        expected = "🆕 Version timestamp unavailable"
        self.assertEqual(release_banner(self.path, environ=self.env), expected)
        for raw in ('not json', '[]', '{"built_at":"2026-11-22T02:00:00","commit":"' + COMMIT + '"}'):
            self.path.write_text(raw)
            self.assertEqual(release_banner(self.path, environ=self.env), expected)
        write_build_stamp(self.path, environ=self.env)
        self.assertEqual(release_banner(self.path, environ={"RENDER_GIT_COMMIT": "b"*40}), expected)

    def test_new_build_changes_stamp(self):
        write_build_stamp(self.path, environ=self.env,
                          now=datetime(2026,11,22,1,tzinfo=timezone.utc))
        first = release_banner(self.path, environ=self.env)
        write_build_stamp(self.path, environ=self.env,
                          now=datetime(2026,11,22,2,tzinfo=timezone.utc))
        self.assertNotEqual(first, release_banner(self.path, environ=self.env))

    def test_real_menu_handler_single_message_and_menu_preserved(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text())
        funcs = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("cmd_start", "cmd_menu")]
        self.assertEqual(len(funcs), 2)
        for node in funcs:
            node.decorator_list = []
        sent, state = Mock(), Mock()
        ns = {"safe_send": sent, "state": state, "main_keyboard": lambda: "MENU"}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), "main.py", "exec"), ns)
        with patch("msk_release_stamp.release_banner", return_value="FIXED BUILD STAMP"):
            ns["cmd_menu"](SimpleNamespace(chat=SimpleNamespace(id=42)))
        state.clear.assert_called_once_with(42)
        sent.assert_called_once()
        self.assertIn("FIXED BUILD STAMP", sent.call_args.args[1])
        self.assertEqual(sent.call_args.kwargs["reply_markup"], "MENU")


if __name__ == "__main__":
    unittest.main()
