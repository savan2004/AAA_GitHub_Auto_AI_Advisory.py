"""Acceptance tests for all MSK research buttons' opt-in AI workflow.

These tests use injected fake AI calls. No Telegram, model billing, real
credentials, market fetches or portfolio records are accessed.
"""
import os
os.environ.setdefault("TELEGRAM_TOKEN","offline-test-token-no-live-bot")

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import ai_button_assistant as assist

ROOT=Path(__file__).resolve().parents[1]


class DraftContextContracts(unittest.TestCase):
    def setUp(self):
        with assist._LOCK:
            assist._CONTEXTS.clear()

    def test_context_is_isolated_per_chat_and_topic(self):
        self.assertTrue(assist.remember(100,"technical","INFY NSE closes ₹1,166.00 at 26-Oct-2026",now=10))
        self.assertEqual(assist.recall(100,"technical",now=11).startswith("INFY"),True)
        self.assertIsNone(assist.recall(101,"technical",now=11))
        self.assertIsNone(assist.recall(100,"fundamental",now=11))

    def test_expired_context_is_refused_not_recopied(self):
        assist.remember(10,"breadth","Market breadth data from observed public feed",now=1)
        result,used=assist.draft(10,"breadth","Draft a market summary",
                                 now=assist.TTL_SECONDS+10,caller=lambda *a:("FAKE",""))
        self.assertFalse(used)
        self.assertIn("expired",result.lower())

    def test_private_portfolio_is_never_stored_or_transmitted(self):
        sensitive="My portfolio: INFY Qty 10 Avg: ₹1000 P&L ₹700"
        self.assertFalse(assist.remember(10,"portfolio",sensitive))
        captured={}
        def fake(messages,tokens,system):
            captured["prompt"]=messages[0]["content"]
            return "Focus on long-term diversification and risk.", ""
        output,used=assist.draft(10,"portfolio","How can I diversify?",caller=fake)
        self.assertTrue(used)
        self.assertNotIn("INFY",captured["prompt"])
        self.assertNotIn("₹700",captured["prompt"])
        self.assertIn("PRIVATE",captured["prompt"])
        self.assertIn("diversification",output)

    def test_general_educational_roE_allowed_without_market_feed(self):
        responses=[]
        def fake(messages,tokens,system):
            responses.append(messages[0]["content"])
            return "ROE measures a company's return on shareholder equity.", ""
        content,used=assist.draft(99,"general","What is ROE?",caller=fake)
        self.assertTrue(used)
        self.assertIn("ROE measures",content)
        self.assertEqual(len(responses),1)

    def test_no_latest_price_question_without_evidence(self):
        def bad(*args):
            raise AssertionError("Should not send unfounded quote question")
        answer,used=assist.draft(9,"general","What is INFY price today?",caller=bad)
        self.assertFalse(used)
        self.assertIn("not supplied",answer)

    def test_numerically_grounded_draft_passes(self):
        assist.remember(1,"fundamental","Infosys P/E 15.53 and ROE 29.95%; source Yahoo unverified, as-of unknown.",now=100)
        def good(messages,tokens,system):
            return "Valuation: P/E 15.53; quality: ROE 29.95%. Source unverified.", ""
        text,used=assist.draft(1,"fundamental","Explain the valuation",now=101,caller=good)
        self.assertTrue(used)
        self.assertIn("29.95",text)

    def test_invented_pe_roe_or_price_rejected(self):
        assist.remember(1,"fundamental","INFY P/E 15.53 ROE 29.95%, unverified session 2026.",now=101)
        def invented(msgs,max_tokens,system):
            return "ROE is 41.2%, BUY target 2200 with guaranteed returns.", ""
        txt,used=assist.draft(1,"fundamental","Discuss these facts",now=102,caller=invented)
        self.assertFalse(used)
        self.assertIn("rejected",txt.lower())

    def test_markup_escaped(self):
        assert assist.escape_telegram('<script>alert("hi")</script>') == '&lt;script&gt;alert("hi")&lt;/script&gt;'
        cleaned=assist.clean_excerpt("<b>INFY</b> NSE price 1,166")
        self.assertIn("INFY",cleaned)
        self.assertNotIn("<b>",cleaned)

    def test_invalid_or_token_based_context_blocked(self):
        self.assertFalse(assist.remember(1,"technical","API_KEY: SECRET_ABCD value 2015"))
        self.assertFalse(assist.remember(1,"unknown","NSE verified Market Report 2026"))
        self.assertFalse(assist.remember(1,"technical","short"))
        self.assertLessEqual(assist.MAX_CONTEXT,3500)

    def test_unconfigured_ai_never_claims_model_success(self):
        assist.remember(1,"stock","Verified-looking stock lookup, timestamp unconfirmed",now=20)
        with patch("ai_button_assistant.ai_available",return_value=False):
            text,used=assist.draft(1,"stock","Explain the current setup",now=21)
        self.assertFalse(used)
        self.assertIn("not configured",text)

    def test_all_menu_button_paths_wired(self):
        source=(ROOT/"main.py").read_text(encoding="utf-8")
        tree=ast.parse(source)
        names={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}
        self.assertIn("send_research",names)
        self.assertIn("_send_ai_draft",names)
        self.assertIn("ai_question_button",names)
        self.assertIn("handle_analysis_callback",names)
        self.assertIn("handle_text",names)
        for topic in ("portfolio","technical","fundamental","breadth","chart",
                      "swing_safe","swing_agr","sector","buzz","pdf"):
            self.assertIn('"'+topic+'"',source)
        for action in ("aiquestion|","aisummary|","ai:","compose_ai_draft"):
            self.assertIn(action,source)
        self.assertIn('if getattr(call.message.chat, "type", "private") != "private"',source)
        # Existing deterministic buttons must not disappear.
        for button in ("🏦 Conservative","⚖️ Moderate","🚀 Aggressive",
                       "🏭 Sector Report","📄 Report","📈 Chart","💼 Portfolio",
                       "🔥 Stocks Buzz"):
            self.assertIn(button,source)


if __name__=="__main__":
    unittest.main()
