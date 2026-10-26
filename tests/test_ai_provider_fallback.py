"""Offline regression tests for shared AI provider fallback; no external calls."""
import os
import unittest
from unittest.mock import patch

import llm_wrapper


class AIWrapperTests(unittest.TestCase):
    @patch("ai_engine.ai_available", return_value=True)
    @patch("ai_engine._call_ai", return_value=("OpenAI fallback response", ""))
    def test_shared_provider_success(self, call, available):
        self.assertEqual(llm_wrapper.actual_llm_call("Review SBIN"), "OpenAI fallback response")
        self.assertEqual(call.call_args.kwargs["max_tokens"], 500)

    @patch("ai_engine.ai_available", return_value=False)
    @patch("ai_engine._call_ai")
    def test_missing_keys(self, call, available):
        self.assertIn("not configured", llm_wrapper.actual_llm_call("Hello"))
        call.assert_not_called()

    @patch("ai_engine.ai_available", return_value=True)
    @patch("ai_engine._call_ai", return_value=("", "GROQ: 429; OpenAI: 429"))
    def test_all_provider_failures(self, call, available):
        success, message = llm_wrapper.safe_llm_call("Review TCS")
        self.assertFalse(success)
        self.assertIn("All providers failed", message)

    @patch("ai_engine.ai_available", return_value=True)
    @patch("ai_engine._call_ai", side_effect=TimeoutError("provider timeout"))
    def test_provider_timeout(self, call, available):
        success, message = llm_wrapper.safe_llm_call("Review ITC")
        self.assertFalse(success)
        self.assertIn("AI engine error", message)

    @patch("ai_engine.ai_available", return_value=True)
    @patch("ai_engine._call_ai", return_value=("  Valid answer  ", ""))
    def test_trim_response(self, call, available):
        self.assertEqual(llm_wrapper.actual_llm_call("Review INFY"), "Valid answer")


if __name__ == "__main__":
    unittest.main()
