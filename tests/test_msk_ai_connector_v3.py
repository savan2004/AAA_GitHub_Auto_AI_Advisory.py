"""Offline v3 contracts; no API tokens, network or Telegram delivery."""
import unittest
from unittest.mock import Mock, patch
from msk_ai_connector_v2 import AIResponse, request_ai
from msk_ai_market_context_v3 import market_context
import ai_engine
import llm_wrapper
import health_monitor

class Response:
    status_code = 200
    content = b"{}"
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload

def reply(text, **extra):
    return Response({"choices": [{"message": {"content": text}, **extra}]})

class V3(unittest.TestCase):
    def test_truncated_and_tool_calls_rejected(self):
        for response in (reply("Price 100", finish_reason="length"),
                         reply("Price 100", finish_reason="tool_calls")):
            result = request_ai([{"role":"user","content":"Price 100"}],
                env={"GROQ_API_KEY":"test-key"}, transport=Mock(return_value=response))
            self.assertFalse(result.ok)
            self.assertIn("groq:AI_INCOMPLETE_OUTPUT", result.attempts)

    def test_assistant_and_system_numbers_not_evidence(self):
        result = request_ai([{"role":"user","content":"Price 100"},
            {"role":"assistant","content":"Target 999"},
            {"role":"user","content":"Explain"}], system="Never make up 888",
            env={"GROQ_API_KEY":"test-key"}, financial=True,
            transport=Mock(return_value=reply("Target 999 or 888")))
        self.assertIn("groq:AI_UNGROUNDED_NUMBER", result.attempts)

    def test_reasoning_and_echoed_key_rejected(self):
        for body in ("<analysis>internal</analysis>", "secret-test-key", "a\x00b"):
            result = request_ai([{"role":"user","content":"Explain"}],
                env={"GROQ_API_KEY":"secret-test-key"}, transport=Mock(return_value=reply(body)))
            self.assertFalse(result.ok)

    def test_gemini_thought_not_visible(self):
        response = Response({"candidates":[{"finishReason":"STOP","content":{"parts":[
            {"thought":True,"text":"private 999"},{"text":"Price 100"}]}}]})
        result = request_ai([{"role":"user","content":"Price 100"}],
            env={"GEMINI_API_KEY":"test-key"}, financial=True, transport=Mock(return_value=response))
        self.assertTrue(result.ok)
        self.assertEqual(result.text,"Price 100")

    def test_legacy_wrapper_delegates(self):
        with patch.object(llm_wrapper,"request_ai",return_value=AIResponse("Good","groq","model","",())) as mock:
            self.assertEqual(llm_wrapper.safe_llm_call("Explain"),(True,"Good"))
            self.assertTrue(mock.call_args.kwargs["financial"])

    def test_market_context_missing_or_dated(self):
        quote = Mock(side_effect=[{"price":25000,"quote_asof_ist":"2026-11-23T15:30:00+05:30",
            "quote_source":"Yahoo Finance v8","quote_status":"last_observed_session"},None])
        out = market_context(quote)
        self.assertIn("last observed session",out)
        self.assertIn("Bank Nifty: timestamp-verified observation unavailable",out)

    def test_no_false_provider_health(self):
        with patch.dict("os.environ",{"GROQ_API_KEY":"test-key"},clear=True):
            status, detail = health_monitor.check_ai_providers()["groq"]
            self.assertEqual(status,health_monitor.HealthStatus.UNKNOWN)
            self.assertIn("unverified",detail)

    def test_missing_price_does_not_crash(self):
        with patch.object(ai_engine,"ai_available",return_value=True), patch.object(ai_engine,"_call_ai",return_value=("","")) as call:
            self.assertIn("no volatility range is claimed",ai_engine.ai_insights("ABC",None,None,None,"unknown",None,None))
            prompt = call.call_args.args[0][0]["content"]
            self.assertIn("Price Rs N/A",prompt)
            self.assertIn("stop N/A; t1 N/A",prompt)

    def test_news_adapters_without_network(self):
        import sys
        fake = Mock()
        fake.get_stock_news.return_value = "headline [date unverified]"
        fake.get_market_news.return_value = "publication times unverified"
        with patch.dict(sys.modules,{"market_news":fake}):
            self.assertIn("unverified",ai_engine.fetch_news("ABC",2))
            self.assertIn("unverified",ai_engine.fetch_market_news(3))
            fake.get_stock_news.assert_called_once_with("ABC",n=2)

if __name__ == "__main__":
    unittest.main()
