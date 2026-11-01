"""Offline regression for the ten-distinct-stock portfolio cap."""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

class PortfolioCapTests(unittest.TestCase):
    def test_ten_distinct_holdings_and_existing_symbol_update(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
        ns = {"os": __import__("os"), "json": __import__("json"), "threading": __import__("threading"), "logger": Mock()}
        exec(compile(ast.Module(body=[manager], type_ignores=[]), "<portfolio>", "exec"), ns)
        with tempfile.TemporaryDirectory() as directory:
            p = ns["PortfolioManager"](str(Path(directory) / "holdings.json"))
            for i in range(10):
                self.assertTrue(p.add(123, f"STOCK{i}", 1, 100))
            self.assertFalse(p.add(123, "ELEVENTH", 1, 100))
            self.assertEqual(len(p.get(123)), 10)
            self.assertTrue(p.add(123, "STOCK0", 2, 200))
            self.assertEqual(p.get(123)["STOCK0"]["qty"], 3)
            self.assertTrue(p.remove(123, "STOCK9"))
            self.assertTrue(p.add(123, "ELEVENTH", 1, 100))
            self.assertEqual(len(p.get(123)), 10)

if __name__ == "__main__":
    unittest.main()
