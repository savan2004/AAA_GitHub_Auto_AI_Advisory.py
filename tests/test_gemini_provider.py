"""Offline Gemini model, fallback, credential and integration tests."""
import unittest
from unittest.mock import Mock, patch

import ai_engine
import gemini_provider
import llm_wrapper


def response(status=200, text="Gemini mock reply"):
    result = Mock(status_code=status)
    result.json.return_value = {"candidates": [
        {"content": {"parts": [{"text": text}]}}
    ]}
    return result


class GeminiProviderTests(unittest.TestCase):
    def test_supported_models_only(self):
        self.assertEqual(gemini_provider.GEMINI_MODELS,
                         ("gemini-3.8-flash", "gemini-3.5-flash-lite"))

    def test_no_key_makes_no_request(self):
        with patch.object(gemini_provider.requests, "post") as post:
            self.assertEqual(gemini_provider.call_gemini("", [{"role": "user", "content": "hi"}])[0], "")
            post.assert_not_called()

    def test_primary_model_and_secret_only_in_header(self):
        with patch.object(gemini_provider.requests, "post", return_value=response()) as post:
            text, error = gemini_provider.call_gemini(
                "not-a-real-secret", [{"role": "user", "content": "hello"}], 650, "Be precise"
            )
        self.assertEqual((text, error), ("Gemini mock reply", ""))
        self.assertTrue(post.call_args.args[0].endswith(
            "/models/gemini-3.8-flash:generateContent"))
        self.assertNotIn("not-a-real-secret", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "not-a-real-secret")
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["maxOutputTokens"], 650)
        self.assertEqual(post.call_args.kwargs["json"]["system_instruction"]["parts"][0]["text"], "Be precise")

    def test_first_model_unavailable_uses_second(self):
        with patch.object(gemini_provider.requests, "post",
                          side_effect=[response(404), response(text="Lite backup")]) as post:
            text, error = gemini_provider.call_gemini(
                "dummy", [{"role": "user", "content": "hi"}]
            )
        self.assertEqual((text, error), ("Lite backup", ""))
        self.assertEqual(post.call_count, 2)
        self.assertIn("gemini-3.5-flash-lite", post.call_args.args[0])

    def test_auth_failure_does_not_retry_models_or_echo_key(self):
        with patch.object(gemini_provider.requests, "post", return_value=response(401)) as post:
            text, error = gemini_provider.call_gemini(
                "hidden-key", [{"role": "user", "content": "hi"}]
            )
        self.assertEqual(text, "")
        self.assertIn("authentication", error)
        self.assertNotIn("hidden-key", error)
        self.assertEqual(post.call_count, 1)

    def test_chat_history_maps_assistant_to_model(self):
        with patch.object(gemini_provider.requests, "post", return_value=response()) as post:
            gemini_provider.call_gemini("dummy", [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "next"},
            ])
        roles = [i["role"] for i in post.call_args.kwargs["json"]["contents"]]
        self.assertEqual(roles, ["user", "model", "user"])

    def test_ai_engine_uses_gemini_when_groq_unconfigured(self):
        keys = lambda name, *aliases: "dummy" if name == "GEMINI_API_KEY" else ""
        with patch.object(ai_engine, "_key", side_effect=keys), patch.object(
            gemini_provider.requests, "post", return_value=response(text="AI engine answer")
        ):
            text, error = ai_engine._call_ai([{"role": "user", "content": "test"}])
        self.assertEqual((text, error), ("AI engine answer", ""))

    def test_legacy_wrapper_uses_same_supported_gemini_api(self):
        with patch.object(llm_wrapper, "GROQ_API_KEY", ""), patch.object(
            llm_wrapper, "GEMINI_API_KEY", "dummy"
        ), patch.object(gemini_provider.requests, "post",
                        return_value=response(text="Wrapper answer")):
            self.assertEqual(llm_wrapper.actual_llm_call("test"), "Wrapper answer")

    def test_no_provider_response_does_not_look_successful(self):
        with patch.object(gemini_provider.requests, "post",
                          side_effect=[response(404), response(503)]):
            text, error = gemini_provider.call_gemini(
                "dummy", [{"role": "user", "content": "hi"}]
            )
        self.assertEqual(text, "")
        self.assertIn("HTTP 503", error)


if __name__ == "__main__":
    unittest.main()
