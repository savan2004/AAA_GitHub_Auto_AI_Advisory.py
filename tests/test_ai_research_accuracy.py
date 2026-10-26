"""Offline acceptance tests for shared MSK AI handlers and fact gates.

No external APIs/keys/Telegram calls. Full provider HTTP responses are mocked.
"""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_TOKEN','offline-test-token-no-live-bot')

import ai_provider_gateway as gw
import ai_engine
import health_monitor
import llm_wrapper
from research_engine import numbers_grounded

ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self,status=200,body=None):
        self.status_code=status
        self.body=body if body is not None else {}
        self.text="SECRET_DO_NOT_LOG"
    def json(self):
        return self.body


class SharedGatewayTests(unittest.TestCase):
    def env(self,**kwargs):
        clean={"GROQ_API_KEY":"","GEMINI_API_KEY":"","GOOGLE_API_KEY":"",
               "OPENAI_API_KEY":"","OPENAI_KEY":"","ASKFUZZ_API_KEY":"",
               "GROQ_MODELS":"","GEMINI_MODELS":"","OPENAI_MODEL":""}
        clean.update(kwargs)
        return patch.dict("os.environ",clean)

    def test_old_models_are_not_in_default_config(self):
        self.assertEqual(gw.GROQ_MODELS[0],"openai/gpt-oss-120b")
        self.assertEqual(gw.GEMINI_MODELS[0],"gemini-3.8-flash")
        for retired in ("mixtral-8x7b-32768","llama-3.3-70b-versatile",
                        "llama-3.1-8b-instant","gemini-1.5-flash"):
            self.assertNotIn(retired, gw.GROQ_MODELS+gw.GEMINI_MODELS)

    def test_bad_env_override_is_ignored(self):
        with self.env(GROQ_MODELS="mixtral-8x7b-32768",GEMINI_MODELS="gemini-1.5-flash"):
            self.assertEqual(gw._models("GROQ_MODELS",gw.GROQ_MODELS),gw.GROQ_MODELS)
            self.assertEqual(gw._models("GEMINI_MODELS",gw.GEMINI_MODELS),gw.GEMINI_MODELS)

    def test_groq_success_uses_new_model_and_sanitized_prompt(self):
        body={"choices":[{"message":{"content":"Data are unavailable."}}]}
        with self.env(GROQ_API_KEY="SENSITIVE_123"):
            with patch("ai_provider_gateway.requests.post",return_value=Response(body=body)) as post:
                text,err=ai_engine._call_ai([{"role":"user","content":"Analyze INFY"}],300)
        self.assertEqual(text,"Data are unavailable.")
        self.assertEqual(err,"")
        args,kwargs=post.call_args
        self.assertIn("groq.com",args[0])
        self.assertEqual(kwargs["json"]["model"],"openai/gpt-oss-120b")
        self.assertIn("max_completion_tokens",kwargs["json"])
        self.assertIn("source",kwargs["json"]["messages"][0]["content"].lower())

    def test_groq_401_failover_to_gemini_with_no_secret_echo(self):
        body={"candidates":[{"content":{"parts":[{"text":"No confirmed quote."}]}}]}
        def responder(url,**kwargs):
            if "groq" in url:
                return Response(status=401,body={"message":"SENSITIVE_123"})
            return Response(body=body)
        with self.env(GROQ_API_KEY="SENSITIVE_123",GEMINI_API_KEY="OTHER_SECRET_456"):
            with patch("ai_provider_gateway.requests.post",side_effect=responder) as calls:
                text,err=ai_engine._call_ai([{"role":"user","content":"price?"}])
        self.assertEqual(text,"No confirmed quote.")
        self.assertEqual(err,"")
        self.assertEqual(calls.call_count,3)
        self.assertIn("generativelanguage.googleapis.com",calls.call_args.args[0])
        self.assertNotIn("SENSITIVE_123",str(err))
        self.assertEqual(calls.call_args.kwargs["json"]["contents"][-1]["role"],"user")

    def test_errors_not_provider_html_or_secrets(self):
        with self.env(GROQ_API_KEY="SECRET_TOKEN"):
            with patch("ai_provider_gateway.requests.post",return_value=Response(status=503)):
                text,err=ai_engine._call_ai([{"role":"user","content":"help"}])
        self.assertEqual(text,"")
        self.assertIn("HTTP 503",err)
        self.assertNotIn("SECRET",err)

    def test_invalid_messages_never_hit_network(self):
        with self.env(GROQ_API_KEY="secret"):
            with patch("ai_provider_gateway.requests.post") as req:
                self.assertEqual(gw.call([{"role":"system","content":"secret"}])[0],"")
                req.assert_not_called()

    def test_openai_third_provider_and_preexisting_quota_wrapper(self):
        body={"choices":[{"message":{"content":"Verified source absent."}}]}
        with self.env(OPENAI_API_KEY="SECRET"):
            with patch("ai_provider_gateway.requests.post",return_value=Response(body=body)) as req:
                self.assertEqual(llm_wrapper.actual_llm_call("Price now?"),"Verified source absent.")
                self.assertIn("api.openai.com",req.call_args.args[0])
                self.assertEqual(req.call_args.kwargs["json"]["model"],"gpt-4o-mini")

    def test_topic_requests_do_not_reuse_chat_history(self):
        with patch("ai_engine.ai_available",return_value=True),\
             patch("ai_engine._call_ai",return_value=("General educational response","")) as call:
            ai_engine._chat_history[0]=[{"role":"user","content":"OTHER_USER_PRIVATE"}]
            out=ai_engine.ai_topic_respond("How does PE work?")
            self.assertEqual(out,"General educational response")
            self.assertEqual(call.call_args.args[0],[{"role":"user","content":"How does PE work?"}])
            ai_engine._chat_history.pop(0,None)

    def test_api_key_not_equivalent_to_health(self):
        with self.env(GROQ_API_KEY="CONFIGURED_ONLY"):
            result=health_monitor.check_ai_providers()
        self.assertEqual(result["groq"][0],health_monitor.HealthStatus.UNKNOWN)
        self.assertIn("not tested",result["groq"][1])
        self.assertNotIn("ready",result["groq"][1])

    def test_no_fake_api_confidence(self):
        response=Response(body={"response":"Caution","confidence":.99})
        with patch("ai_engine.requests.post",return_value=response):
            value,err=ai_engine._call_askfuzz_ai("key",[{"role":"user","content":"hello"}],100,"")
        self.assertEqual(value,"Caution")
        self.assertNotIn("Confidence",value)


class GroundedResearchTests(unittest.TestCase):
    def test_numeric_guard_rejects_unsupported_small_percent(self):
        self.assertFalse(numbers_grounded("ROE grew 7%","ROE 29.95%"))
        self.assertFalse(numbers_grounded("P/E 8.5","P/E 12.2"))
        self.assertFalse(numbers_grounded("The 2024 filing","Price date: 2026"))

    def test_numeric_guard_rejects_unverified_price_or_target(self):
        self.assertFalse(numbers_grounded("Target Rs 500","Close 1201.80"))
        self.assertFalse(numbers_grounded("Price 1650.20","Close 1201.80"))

    def test_grounded_numbers_are_allowed(self):
        self.assertTrue(numbers_grounded("ROE 29.95%, PE 15.53","ROE 29.95%; PE 15.53"))
        self.assertTrue(numbers_grounded("Quarter under review has no verified update.","PE n/a"))

    def test_report_writer_attaches_last_session_not_current_ltp(self):
        source=(ROOT/"report_generator.py").read_text("utf-8")
        self.assertIn('t["asof"] = last_index.strftime("%Y-%m-%d")',source)
        self.assertIn("historical OHLCV feed",source)

    def test_missing_quote_source_causes_no_ai_analyst_note(self):
        from research_engine import ai_analyst_note
        with patch("ai_engine.ai_available",return_value=True),\
             patch("ai_engine._call_ai") as api:
            result=ai_analyst_note("Infy","INFY","Technology",{}, {}, {}, {}, {}, [])
            self.assertEqual(result,"")
            api.assert_not_called()


class MarketNewsEvidenceTests(unittest.TestCase):
    def test_ai_never_returns_static_market_headlines_as_current(self):
        with patch("market_news.get_market_news",
                   return_value="📰 MARKET NEWS (Auto-generated)\n• Static claim"):
            self.assertEqual(ai_engine.fetch_market_news(), "")

    def test_real_headline_date_limit_is_explicit(self):
        with patch("market_news.get_market_news",
                   return_value="📰 MARKET NEWS\n• New verified source item"):
            output=ai_engine.fetch_market_news()
        self.assertIn("Headline dates and original links not independently verified",output)

    def test_canned_per_stock_headline_is_not_evidence(self):
        import market_news
        import data_validation
        canned=next(iter(market_news._STOCK_NEWS_FALLBACK.values()))
        with patch("market_news.get_stock_news",return_value="📰 "+canned):
            self.assertEqual(data_validation.get_stock_news_robust("RELIANCE"),"")

    def test_live_per_stock_headline_can_be_returned(self):
        with patch("data_validation.get_stock_news_robust",
                   return_value="📰 Company results published, source pending"):
            self.assertEqual(ai_engine.fetch_news("INFY"),
                             "📰 Company results published, source pending")


class FinanceUnitContracts(unittest.TestCase):
    """Provider-native units are never inferred from values or blended."""

    @staticmethod
    def _get(info, other=None, yh=None):
        import fundamentals
        with patch("data_engine.get_info", return_value=info),\
             patch("fundamentals._get_cached", return_value=None),\
             patch("fundamentals._set_cached"),\
             patch("fundamentals._fetch_screener", return_value=None),\
             patch("fundamentals._fetch_finnhub", return_value=other),\
             patch("fundamentals._fetch_yfinance", return_value=yh):
            return fundamentals.get_fundamentals("INFY")

    def test_yahoo_roe_is_fraction_and_debt_field_is_percent(self):
        obj=self._get({
            "name":"Infosys Ltd","pe":16.2,"roe":.29951,
            "debtToEquity":5.0,"market_cap":4_000_000_000_000,
            "totalRevenue":1_800_000_000_000,
        })
        self.assertAlmostEqual(obj["roe"],29.951,places=3)
        self.assertAlmostEqual(obj["de"],.05,places=3)

    def test_roe_over_one_is_still_yahoo_fraction(self):
        obj=self._get({"roe":1.20,"debtToEquity":0.0})
        self.assertEqual(obj["roe"],120.)
        self.assertEqual(obj["de"],0.0)

    def test_finnhub_already_normalized_ratio_not_divided_again(self):
        obj=self._get({},other={"roe":18.5,"de":.76})
        self.assertEqual(obj["roe"],18.5)
        self.assertEqual(obj["de"],.76)

    def test_yfinance_native_units(self):
        obj=self._get({},yh={"returnOnEquity":.20,"debtToEquity":6.0})
        self.assertEqual(obj["roe"],20.)
        self.assertEqual(obj["de"],.06)

    def test_high_real_debt_multiple_is_not_diluted(self):
        from research_engine import _de_ratio
        self.assertEqual(_de_ratio({"de":12},None),12.0)
        self.assertEqual(_de_ratio({"de":.08},None),.08)
        self.assertIsNone(_de_ratio({"de":float("nan")},None))


if __name__ == "__main__":
    unittest.main()
