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
        with patch.dict('os.environ', {'MSK_PORTFOLIO_INITIALIZE_EMPTY': '1'}):
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

    def test_missing_file_requires_explicit_initialization(self):
        other = str(Path(self.tmp.name) / "lost.json")
        with patch.dict("os.environ", {"MSK_PORTFOLIO_INITIALIZE_EMPTY": "0"}):
            with self.assertRaisesRegex(RuntimeError, "storage missing"):
                PortfolioManager(other)
        self.assertFalse(Path(other).exists())

    def test_explicit_first_run_creates_empty_storage(self):
        other = str(Path(self.tmp.name) / "new.json")
        with patch.dict("os.environ", {"MSK_PORTFOLIO_INITIALIZE_EMPTY": "1"}):
            manager = PortfolioManager(other)
        self.assertEqual(manager.get(123), {})
        self.assertEqual(json.loads(Path(other).read_text()), {})

    def test_corrupt_file_fails_closed(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            PortfolioManager(self.path)

    def test_invalid_persisted_holdings_fail_closed(self):
        bad_positions = [
            {"qty": 0, "avg": 100.0},
            {"qty": -1, "avg": 100.0},
            {"qty": 1, "avg": float("nan")},
            {"qty": 1, "avg": 0},
            {"qty": "1", "avg": 100.0},
            {"qty": 1},
        ]
        for position in bad_positions:
            with self.subTest(position=position):
                Path(self.path).write_text(
                    json.dumps({"123": {"SBIN": position}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    PortfolioManager(self.path)

    def test_persisted_holding_limit_rejected(self):
        Path(self.path).write_text(
            json.dumps({"123": {f"SYM{i}": {"qty": 1, "avg": 100.0}
                                for i in range(11)}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "maximum holdings"):
            PortfolioManager(self.path)

    def test_invalid_price_rejected(self):
        for price in [float("nan"), float("inf"), -1.0, 0.0]:
            with self.assertRaises(ValueError):
                self.manager.add(123, "SBIN", 1, price)


if __name__ == "__main__":
    unittest.main()
