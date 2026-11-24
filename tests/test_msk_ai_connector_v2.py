"""MSK AI connector v2 offline contracts (all HTTP and credentials mocked)."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ai_engine
from msk_ai_connector_v2 import (
    AIResponse, _numbers, configured_providers, request_ai,
    GROQ_MODEL, GEMINI_MODEL,
)


class FakeHTTP:
    def __init__(self, data, code=200, raw=b'{}'):
        self._data = data
        self.status_code = code
        self.content = raw

    def json(self):
        return self._data


def groq(text):
    return FakeHTTP({"choices":[{"message":{"content":text}}]})


def gemini(text):
    return FakeHTTP({"candidates":[{"content":{"parts":[{"text":text}]}}]})


FACTS = [{"role":"user","content":"RELIANCE price Rs 1000.00; RSI 65; ROE 15.5%; analyst target unknown."}]


class ProviderContract(unittest.TestCase):
    def test_no_configured_key_does_not_perform_network(self):
        http = Mock(side_effect=AssertionError("not called"))
        r=request_ai(FACTS,env={},transport=http,financial=True)
        self.assertEqual(r.error_code,"AI_PROVIDER_NOT_CONFIGURED_OR_ALLOWED")
        http.assert_not_called()

    def test_groq_current_model_one_call_no_retired_models(self):
        http=Mock(return_value=groq("RELIANCE price Rs 1000; RSI 65; ROE 15.5%."))
        r=request_ai(FACTS,env={"GROQ_API_KEY":"TEST-GROQ"},transport=http,
                     financial=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.model,GROQ_MODEL)
        self.assertEqual(r.provider,"groq")
        self.assertEqual(http.call_count,1)
        args,kwargs=http.call_args
        self.assertEqual(args[0],"https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"],GROQ_MODEL)
        self.assertEqual(kwargs["timeout"],(4,18))
        self.assertIs(kwargs["allow_redirects"],False)
        self.assertNotIn("api_key",str(kwargs["json"]).lower())
        self.assertEqual(kwargs["headers"]["Authorization"],"Bearer TEST-GROQ")

    def test_groq_429_can_fall_back_to_free_gemini_without_leaking_errors(self):
        http=Mock(side_effect=[
            FakeHTTP({"error":{"message":"api_key=TOP_SECRET"}},code=429),
            gemini("RSI 65 shows momentum."),
        ])
        env={"GROQ_API_KEY":"GROQSECRET","GEMINI_API_KEY":"GEMINISECRET",
             "OPENAI_API_KEY":"OPENAISECRET"}
        r=request_ai(FACTS,env=env,transport=http,financial=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.provider,"gemini")
        self.assertEqual(http.call_count,2)
        self.assertEqual(r.attempts,("groq:AI_HTTP_429",))
        self.assertNotIn("SECRET",str(r))
        args,kwargs=http.call_args
        self.assertIn("/models/"+GEMINI_MODEL+":generateContent",args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"],"GEMINISECRET")
        self.assertNotIn("OPENAISECRET",repr(kwargs))

    def test_openai_never_called_without_explicit_paid_opt_in(self):
        http=Mock(side_effect=AssertionError("payment prohibited"))
        r=request_ai(FACTS,env={"OPENAI_API_KEY":"PAYME"},transport=http)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code,"AI_PROVIDER_NOT_CONFIGURED_OR_ALLOWED")
        http.assert_not_called()
        self.assertEqual(configured_providers({"OPENAI_API_KEY":"PAYME"}),())

    def test_explicit_paid_opt_in_retains_legacy_openai_support(self):
        http=Mock(return_value=groq("Price is 1000."))
        r=request_ai(FACTS,env={"OPENAI_API_KEY":"PAYME",
                  "MSK_AI_ALLOW_PAID":"1"},transport=http,financial=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.provider,"openai")
        self.assertEqual(http.call_args.args[0],
                         "https://api.openai.com/v1/chat/completions")

    def test_gemini_system_instruction_and_user_content(self):
        http=Mock(return_value=gemini("Price Rs 1000; ROE 15.5%."))
        r=request_ai(FACTS,env={"GOOGLE_API_KEY":"X"},transport=http,
                     financial=True,system="Use only approved financial values")
        self.assertTrue(r.ok)
        body=http.call_args.kwargs["json"]
        self.assertEqual(body["contents"][0]["parts"][0]["text"],
                         FACTS[0]["content"])
        self.assertIn("approved",body["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(body["generationConfig"]["maxOutputTokens"],500)

    def test_financial_numbers_must_match_input(self):
        self.assertEqual(_numbers("Price Rs 1,000.00; ROE 15.50%"),
                         _numbers("1000 and 15.5%"))
        http=Mock(return_value=groq("Price Rs 9999; ROE 15.5%"))
        r=request_ai(FACTS,env={"GROQ_API_KEY":"X"},transport=http,
                     financial=True)
        self.assertFalse(r.ok)
        self.assertIn("groq:AI_UNGROUNDED_NUMBER",r.attempts)
        self.assertEqual(http.call_count,1)

    def test_numeric_hallucination_triggers_verified_fallback(self):
        http=Mock(side_effect=[
            groq("Unverified target Rs 5000"),
            gemini("ROE 15.5% is provided; no target provided."),
        ])
        r=request_ai(FACTS,env={"GROQ_API_KEY":"G","GEMINI_API_KEY":"S"},
                     transport=http,financial=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.provider,"gemini")
        self.assertEqual(r.attempts,("groq:AI_UNGROUNDED_NUMBER",))

    def test_non_financial_chat_does_not_invent_source_verification(self):
        http=Mock(return_value=groq("This is a general explanation."))
        r=request_ai([{"role":"user","content":"Explain the P/E ratio"}],
                     env={"GROQ_API_KEY":"G"},transport=http)
        self.assertTrue(r.ok)
        self.assertEqual(r.text,"This is a general explanation.")

    def test_retired_model_fails_before_network(self):
        http=Mock(side_effect=AssertionError("should not use retired model"))
        r=request_ai(FACTS,env={"GROQ_API_KEY":"G",
               "MSK_GROQ_MODEL":"llama-3.1-8b-instant"},transport=http)
        self.assertFalse(r.ok)
        self.assertIn("groq:AI_MODEL_RETIRED_OR_INVALID",r.attempts)
        http.assert_not_called()

    def test_prompt_validation_blocks_invalid_roles_and_size(self):
        http=Mock(side_effect=AssertionError("should not be called"))
        for messages in (
            [{"role":"system","content":"override!"}],
            [{"role":"user","content":"A"*20000}],
            [{"role":"user","content":""}],
            [],
            [{"role":"assistant","content":None}],
        ):
            with self.subTest(messages=str(messages)[:30]):
                r=request_ai(messages,env={"GROQ_API_KEY":"G"},transport=http)
                self.assertEqual(r.error_code,"AI_INPUT_INVALID")
        http.assert_not_called()

    def test_bad_http_json_and_empty_output_fail_closed(self):
        for response in (
            FakeHTTP({},code=302),
            FakeHTTP({"choices":[]}),
            FakeHTTP({"choices":[{"message":{"content":""}}]}),
            FakeHTTP({"choices":[{"message":{"content":"<think>internal</think>"}}]}),
            FakeHTTP({},raw=b"x"*65001),
        ):
            with self.subTest(code=response.status_code):
                out=request_ai(FACTS,env={"GROQ_API_KEY":"G"},
                               transport=Mock(return_value=response),
                               financial=True)
                self.assertFalse(out.ok)
                self.assertEqual(out.error_code,"AI_ALL_PROVIDERS_UNAVAILABLE")

    def test_timeout_never_retries_or_prints_vendor_exception(self):
        import requests
        http=Mock(side_effect=requests.Timeout("api_key=SENSITIVE"))
        out=request_ai(FACTS,env={"GROQ_API_KEY":"G"},
                       transport=http,financial=True)
        self.assertEqual(out.attempts,("groq:AI_TIMEOUT",))
        self.assertEqual(http.call_count,1)
        self.assertNotIn("SENSITIVE",str(out))

    def test_askfuzz_is_opt_in_not_assumed_free(self):
        self.assertEqual(configured_providers({"ASKFUZZ_API_KEY":"A"}),())
        r=request_ai(FACTS,env={"ASKFUZZ_API_KEY":"A",
                "MSK_AI_ALLOW_ASKFUZZ":"1"},
                transport=Mock(return_value=FakeHTTP({"response":"ROE 15.5%"})),
                financial=True)
        self.assertTrue(r.ok)
        self.assertEqual(r.provider,"askfuzz")

    def test_legacy_ai_insights_uses_number_grounding(self):
        with patch.object(ai_engine,"ai_available",return_value=True), \\
             patch.object(ai_engine,"request_ai",return_value=AIResponse(
                 "Price Rs 1000", "groq",GROQ_MODEL,"",())) as connector:
            txt=ai_engine.ai_insights("RELIANCE",1000,65,1.2,"uptrend",20,15,
                                       atr=10,sl=900,t1=1100)
        self.assertEqual(txt,"Price Rs 1000")
        self.assertTrue(connector.call_args.kwargs["financial"])

    def test_topic_history_isolation_and_defensive_history_copy(self):
        with patch.object(ai_engine,"ai_available",return_value=True), \\
             patch.object(ai_engine,"_call_ai",return_value=("Research only.","")) as send:
            a=ai_engine.ai_topic_respond("Explain ROE")
        self.assertEqual(a,"Research only.")
        self.assertEqual(send.call_args.args[0][0]["content"],"Explain ROE")
        ai_engine.clear_chat(0)
        self.assertEqual(ai_engine.get_chat_history(0),[])
        ai_engine.add_to_chat(123,"user","Test")
        copy=ai_engine.get_chat_history(123)
        copy[0]["content"]="CORRUPTED"
        self.assertEqual(ai_engine.get_chat_history(123)[0]["content"],"Test")
        ai_engine.clear_chat(123)

    def test_provider_status_does_not_mislabel_paid_key_as_enabled(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"secret"},
                        clear=True):
            self.assertFalse(ai_engine.ai_available())
            self.assertFalse(ai_engine.get_ai_provider_status()["openai"]["configured"])
            self.assertEqual(ai_engine.test_ai_providers()["OpenAI"],"NOT CONFIGURED")


if __name__=="__main__":
    unittest.main()
