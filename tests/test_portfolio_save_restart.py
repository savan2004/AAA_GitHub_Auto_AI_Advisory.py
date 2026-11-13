"""Portfolio save, restart and reload regression without touching real holdings."""
import ast
import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_legacy_class():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "PortfolioManager")
    namespace = {"os": os, "json": json, "threading": threading,
                 "logger": logging.getLogger("portfolio-restart-test")}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "main.py", "exec"), namespace)
    return namespace["PortfolioManager"]

class PortfolioRestartProof(unittest.TestCase):
    def test_save_restart_reload_and_remove(self):
        cls = load_legacy_class()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "holdings.json"
            first = cls(str(path), allow_empty_init=True)
            first.add(123456, "SBIN", 3, 754.25)
            self.assertEqual(first.get(123456)["SBIN"]["qty"], 3)
            reopened = cls(str(path))
            self.assertEqual(reopened.get(123456)["SBIN"],
                             {"qty": 3, "avg": 754.25})
            self.assertTrue(reopened.remove(123456, "SBIN"))
            final = cls(str(path))
            self.assertEqual(final.get(123456), {})
            self.assertEqual(json.loads(path.read_text())["123456"], {})

    def test_telegram_confirmation_contains_storage_warning(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("Portfolio saved and read back", source)
        self.assertIn("temporary server file; backup required", source)
        self.assertIn("Portfolio save verified", source)

if __name__ == "__main__":
    unittest.main()
