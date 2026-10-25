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

    def test_private_credentials_never_forwarded(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("provider must not be called")
        result = self.agent["answer_financial_question"](
            "OTP: 123456", provider=forbidden)
        self.assertIn("Do not share", result)

    def test_rejects_unattributed_evidence(self):
        captured = {}
        def fake(messages, max_tokens, system):
            captured["system"] = system
            return "A P/E ratio compares price to earnings.", ""
        self.agent["answer_financial_question"](
            "Evaluate INFY", provider=fake,
            evidence={"symbol": "INFY", "source": "NSE",
                      "source_url": "https://nseindia.com.evil.example/filing",
                      "as_of": "2026-10-24T10:00:00+05:30",
                      "metrics": {"pe": 21}})
        self.assertIn("VERIFIED DATA: none", captured["system"])

    def test_rejects_nonfinite_and_invalid_metrics(self):
        from datetime import datetime, timezone
        evidence = {"symbol": "INFY", "source": "NSE",
                    "source_url": "https://www.nseindia.com/companies",
                    "as_of": "2026-10-24T10:00:00+05:30",
                    "metrics": {"pe": float("nan"), "roe_pct": True,
                                "debt_to_equity": 0}}
        result = self.agent["_validated_evidence"](
            evidence, now=datetime(2026, 10, 25, tzinfo=timezone.utc))
        self.assertEqual(result["metrics"], {"debt_to_equity": 0.0})

    def test_blocks_fake_live_market_claim(self):
        result = self.agent["answer_financial_question"](
            "What is today's price?", provider=lambda *a, **kw:
            ("Current price is ₹999.99", ""))
        self.assertIn("cannot verify", result)

    def test_blocks_direct_buy_recommendation(self):
        result = self.agent["answer_financial_question"](
            "Should I invest?", provider=lambda *a, **kw:
            ("Strong buy this stock immediately", ""))
        self.assertIn("cannot verify", result)

    def test_rejects_stale_evidence(self):
        from datetime import datetime, timezone
        evidence = {"symbol": "INFY", "source": "NSE",
                    "source_url": "https://www.nseindia.com/companies",
                    "as_of": "2025-01-01T10:00:00+05:30",
                    "metrics": {"pe": 22}}
        self.assertIsNone(self.agent["_validated_evidence"](
            evidence, now=datetime(2026, 10, 25, tzinfo=timezone.utc)))

    def test_provider_receives_no_chat_history(self):
        def fake(messages, max_tokens, system):
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["role"], "user")
            return "Debt to equity compares borrowings with shareholder equity.", ""
        self.agent["answer_financial_question"](
            "Explain debt to equity", provider=fake)

    def test_glossary_needs_no_paid_provider(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("glossary must not call LLM")
        for question in ("What is ROE?", "What is PE?", "What is EPS?",
                         "What is debt to equity?", "What is PB?"):
            with self.subTest(question=question):
                result = self.agent["answer_financial_question"](
                    question, provider=forbidden)
                self.assertNotIn("unavailable", result)
                self.assertIn("Educational research", result)



if __name__ == "__main__":
    unittest.main()
