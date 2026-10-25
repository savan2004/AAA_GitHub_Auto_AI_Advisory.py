"""Offline financial agent tests: no network, no credentials, no paid calls."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "financial_research_agent.py"
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def load_agent():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    ns = {"__name__": "financial_agent_test"}
    exec(compile(tree, str(SOURCE), "exec"), ns)
    return ns


class FinancialAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = load_agent()

    def test_question_limits(self):
        self.assertIsNone(self.agent["validate_question"](" "))
        self.assertIsNone(self.agent["validate_question"]("x" * 1201))
        self.assertIsNone(self.agent["validate_question"]("bad\x00input"))

    def test_no_provider_fails_closed(self):
        self.assertEqual(self.agent["answer_financial_question"](
            "What is ROE?", provider=lambda *a, **kw: ("", "timeout")),
            self.agent["UNAVAILABLE"])

    def test_provider_exception_fails_closed(self):
        def broken(*args, **kwargs):
            raise TimeoutError()
        self.assertEqual(self.agent["answer_financial_question"](
            "Explain PE", provider=broken), self.agent["UNAVAILABLE"])

    def test_provider_gets_research_guardrails(self):
        captured = {}
        def fake(messages, max_tokens, system):
            captured.update(messages=messages, system=system, max_tokens=max_tokens)
            return "ROE measures profitability relative to equity.", ""
        result = self.agent["answer_financial_question"]("What is ROE?", provider=fake)
        self.assertIn("ROE measures", result)
        self.assertEqual(captured["messages"], [{"role": "user", "content": "What is ROE?"}])
        self.assertIn("VERIFIED DATA: none", captured["system"])
        self.assertIn("not personalized investment advice", result)

    def test_button_and_command_registered_before_catchall(self):
        self.assertIn('kb.add("🏭 Sector Report", "🧠 Ask AI Advisor")', MAIN)
        self.assertIn('commands=["ask"]', MAIN)
        self.assertLess(MAIN.index('commands=["ask"]'), MAIN.index("def handle_text(m):"))

    def test_no_live_data_or_portfolio_sent(self):
        src = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("get_live_price(", src)
        self.assertNotIn("portfolio.get(", src)


if __name__ == "__main__":
    unittest.main()
