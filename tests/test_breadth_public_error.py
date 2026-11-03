"""Regression check: public market-breadth failures must not expose exceptions."""
import ast
from pathlib import Path
import unittest


class BreadthPublicErrorTests(unittest.TestCase):
    def test_breadth_handler_uses_safe_public_error(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "breadth_btn")
        body = ast.get_source_segment(source, handler)
        self.assertIn("Market breadth is temporarily unavailable", body)
        self.assertNotIn('safe_send(chat_id, f"❌ Error: {e}")', body)
        self.assertIn('logger.error(f"Breadth err: {e}", exc_info=True)', body)


if __name__ == "__main__":
    unittest.main()
