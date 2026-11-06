"""Isolated regression tests for the file-backed portfolio safety patch."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "main.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
portfolio_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
module = ast.Module(body=[portfolio_node], type_ignores=[])
namespace = {"os": __import__("os"), "json": json, "threading": __import__("threading")}
exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
PortfolioManager = namespace["PortfolioManager"]


class PortfolioSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "portfolio.json")
        self.manager = PortfolioManager(self.path)

    def test_limit_and_existing_position(self):
        for i in range(10):
            self.manager.add(123, f"SYM{i}", 1, 100.0)
        with self.assertRaisesRegex(ValueError, "Maximum 10"):
            self.manager.add(123, "ELEVENTH", 1, 100.0)
        self.manager.add(123, "SYM0", 2, 130.0)
        self.assertEqual(len(self.manager.get(123)), 10)
        self.assertEqual(self.manager.get(123)["SYM0"]["qty"], 3)

    def test_write_failure_does_not_mutate_memory_or_disk(self):
        self.manager.add(123, "SBIN", 1, 100.0)
        before = Path(self.path).read_bytes()
        with patch("os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.manager.add(123, "RELIANCE", 1, 200.0)
        self.assertEqual(before, Path(self.path).read_bytes())
        self.assertEqual(set(self.manager.get(123)), {"SBIN"})

    def test_delete_failure_rolls_back(self):
        self.manager.add(123, "SBIN", 1, 100.0)
        with patch("os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.manager.remove(123, "SBIN")
        self.assertIn("SBIN", self.manager.get(123))

    def test_corrupt_file_fails_closed(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            PortfolioManager(self.path)

    def test_invalid_price_rejected(self):
        for price in [float("nan"), float("inf"), -1.0, 0.0]:
            with self.assertRaises(ValueError):
                self.manager.add(123, "SBIN", 1, price)


if __name__ == "__main__":
    unittest.main()
