import ast
import os
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "main.py"
module = ast.parse(SOURCE.read_text(encoding="utf-8"))
portfolio_node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
namespace = {"os": os, "__name__": "portfolio_test"}
import sqlite3
import threading
namespace.update(sqlite3=sqlite3, threading=threading)
exec(compile(ast.Module(body=[portfolio_node], type_ignores=[]), str(SOURCE), "exec"), namespace)
PortfolioManager = namespace["PortfolioManager"]


class PortfolioReliabilityTests(unittest.TestCase):
    def test_add_remove_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "holdings.sqlite")
            manager = PortfolioManager(path)
            manager.add(1, "RELIANCE", 2, 100)
            manager.add(1, "RELIANCE", 2, 200)
            self.assertEqual(manager.get(1)["RELIANCE"], {"qty": 4, "avg": 150})
            self.assertEqual(PortfolioManager(path).get(1)["RELIANCE"]["qty"], 4)
            self.assertTrue(manager.remove(1, "RELIANCE"))
            self.assertFalse(manager.remove(1, "RELIANCE"))
            self.assertEqual(manager.get(1), {})

    def test_invalid_quantity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PortfolioManager(str(Path(directory) / "holdings.sqlite"))
            with self.assertRaises(ValueError):
                manager.add(1, "TEST", 0, 100)


class WebhookReliabilityTests(unittest.TestCase):
    def test_acknowledgement_after_processing(self):
        source = SOURCE.read_text(encoding="utf-8")
        webhook = source[source.index("def webhook():"):source.index("# ── Runner")]
        self.assertLess(webhook.index("_process_webhook(json.dumps(payload))"), webhook.index('return "ok", 200', webhook.index("_process_webhook(json.dumps(payload))")))


if __name__ == "__main__":
    unittest.main()
