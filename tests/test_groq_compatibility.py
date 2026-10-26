"""Offline Groq compatibility tests: no provider credentials or network calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ai_engine
import llm_wrapper


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class GroqCompatibilityTests(unittest.TestCase):
    def test_only_supported_groq_models_in_shared_list(self):
        self.assertEqual(ai_engine._GROQ_MODELS, [
            "openai/gpt-oss-120b", "openai/gpt-oss-20b",
        ])

    def test_primary_engine_falls_back_to_second_groq_model(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            RuntimeError("first model unavailable"), reply("Verified test reply"),
        ]
        key = lambda name, *others: "dummy-key" if name == "GROQ_API_KEY" else ""
        with patch.object(ai_engine, "_groq", client), patch.object(ai_engine, "_key", side_effect=key):
            result, error = ai_engine._call_ai([{"role": "user", "content": "test"}])
        self.assertEqual((result, error), ("Verified test reply", ""))
        self.assertEqual(
            [c.kwargs["model"] for c in client.chat.completions.create.call_args_list],
            ai_engine._GROQ_MODELS,
        )

    def test_primary_engine_reaches_next_provider_when_groq_unavailable(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("unavailable")
        from gemini_provider import call_gemini
        key = lambda name, *others: "dummy-key" if name in {"GROQ_API_KEY", "GEMINI_API_KEY"} else ""
        with patch.object(ai_engine, "_groq", client), patch("gemini_provider.call_gemini", return_value=("Gemini mock fallback", "")) as backup, patch.object(ai_engine, "_key", side_effect=key):
            result, error = ai_engine._call_ai([{"role": "user", "content": "test"}])
        backup.assert_called_once()
        self.assertEqual((result, error), ("Gemini mock fallback", ""))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_legacy_wrapper_uses_shared_models(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            RuntimeError("first model unavailable"), reply("Legacy wrapper mock reply"),
        ]
        with patch.object(llm_wrapper, "GROQ_API_KEY", "dummy-key"), patch.object(llm_wrapper, "GEMINI_API_KEY", ""), patch.object(ai_engine, "_make_groq_client", return_value=client):
            result = llm_wrapper.actual_llm_call("test")
        self.assertEqual(result, "Legacy wrapper mock reply")
        self.assertEqual(
            [c.kwargs["model"] for c in client.chat.completions.create.call_args_list],
            ai_engine._GROQ_MODELS,
        )

    def test_pinned_sdk_clients_initialize_without_network(self):
        # Client creation must not fail with httpx 'proxies' TypeError.
        from openai import OpenAI
        groq_client = ai_engine._make_groq_client("not-a-live-key")
        openai_client = OpenAI(api_key="not-a-live-key")
        try:
            self.assertIsNotNone(groq_client)
            self.assertIsNotNone(openai_client)
        finally:
            groq_client.close()
            openai_client.close()


    def test_sensitive_provider_error_is_redacted(self):
        secret = "example-sensitive-token"
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError(
            "provider rejected key " + secret + " and private request body"
        )
        key = lambda name, *others: "dummy" if name == "GROQ_API_KEY" else ""
        with patch.object(ai_engine, "_groq", client), patch.object(
            ai_engine, "_key", side_effect=key
        ):
            response, error = ai_engine._call_ai(
                [{"role": "user", "content": "test"}]
            )
        self.assertEqual(response, "")
        self.assertIn("RuntimeError", error)
        self.assertNotIn(secret, error)
        self.assertNotIn("private request body", error)

    def test_legacy_wrapper_does_not_log_sensitive_groq_errors(self):
        secret = "example-sensitive-token"
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError(
            "provider rejected key " + secret
        )
        with patch.object(llm_wrapper, "GROQ_API_KEY", "dummy"), patch.object(
            llm_wrapper, "GEMINI_API_KEY", ""
        ), patch.object(ai_engine, "_make_groq_client", return_value=client), patch.object(
            llm_wrapper.logger, "warning"
        ) as warning:
            result = llm_wrapper.actual_llm_call("test")
        self.assertIn("All providers failed", result)
        self.assertNotIn(secret, str(warning.call_args_list))

    def test_legacy_wrapper_openai_fallback_after_groq_unavailable(self):
        client = Mock()
        client.chat.completions.create.return_value = reply("OpenAI backup success")
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        groq = Mock()
        groq.chat.completions.create.side_effect = RuntimeError("offline")
        with patch.object(llm_wrapper, "GROQ_API_KEY", "dummy"), patch.object(
            llm_wrapper, "GEMINI_API_KEY", ""
        ), patch.object(ai_engine, "_make_groq_client", return_value=groq), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "placeholder-openai"}
        ), patch("openai.OpenAI", return_value=client):
            result = llm_wrapper.actual_llm_call("test")
        self.assertEqual(result, "OpenAI backup success")

    def test_legacy_wrapper_openai_error_never_logs_secret(self):
        secret = "private-test-secret"
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.chat.completions.create.side_effect = RuntimeError(secret)
        with patch.object(llm_wrapper, "GROQ_API_KEY", ""), patch.object(
            llm_wrapper, "GEMINI_API_KEY", ""
        ), patch.dict("os.environ", {"OPENAI_API_KEY": "placeholder-openai"}), patch(
            "openai.OpenAI", return_value=client
        ), patch.object(llm_wrapper.logger, "warning") as warnings:
            response = llm_wrapper.actual_llm_call("test")
        self.assertIn("All providers failed", response)
        self.assertNotIn(secret, str(warnings.call_args_list))


if __name__ == "__main__":
    unittest.main()
