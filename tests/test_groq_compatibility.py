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
        gemini = Mock()
        gemini.generate_content.return_value = SimpleNamespace(text="Gemini mock fallback")
        key = lambda name, *others: "dummy-key" if name in {"GROQ_API_KEY", "GEMINI_API_KEY"} else ""
        with patch.object(ai_engine, "_groq", client), patch.object(ai_engine, "_gemini", gemini), patch.object(ai_engine, "_key", side_effect=key):
            result, error = ai_engine._call_ai([{"role": "user", "content": "test"}])
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


if __name__ == "__main__":
    unittest.main()
