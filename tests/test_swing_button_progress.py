import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


class SwingButtonProgressTest(unittest.TestCase):
    def test_both_modes_send_one_progress_message_and_one_result(self):
        source = Path(__file__).resolve().parents[1] / "main.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        handler = next(node for node in module.body
                       if isinstance(node, ast.FunctionDef) and node.name == "swing_btn")
        handler.decorator_list = []
        compiled = compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec")

        for label, mode in (("🎯 Swing (Safe)", "conservative"),
                            ("🚀 Swing (Agr)", "aggressive")):
            with self.subTest(mode=mode):
                messages = []
                jobs = []
                modes = []

                def get_swing_trades(mode):
                    modes.append(mode)
                    return "Swing report"

                namespace = {
                    "_allow_heavy": lambda message: True,
                    "safe_send": lambda chat, message: messages.append((chat, message)),
                    "executor": SimpleNamespace(submit=jobs.append),
                    "get_swing_trades": get_swing_trades,
                }
                exec(compiled, namespace)
                namespace["swing_btn"](SimpleNamespace(chat=SimpleNamespace(id=17), text=label))
                self.assertEqual(len(jobs), 1)
                self.assertEqual(messages, [(17, "⏳ Swing scanning… (~25s)")])
                jobs[0]()
                self.assertEqual(modes, [mode])
                self.assertEqual(messages, [(17, "⏳ Swing scanning… (~25s)"),
                                            (17, "Swing report")])


if __name__ == "__main__":
    unittest.main()
