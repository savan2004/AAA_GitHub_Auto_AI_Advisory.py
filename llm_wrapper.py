# llm_wrapper.py — Safe LLM wrapper with lazy imports + per-user limits
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

def actual_llm_call(prompt: str, max_tokens: int = 500) -> str:
    """Use the shared provider chain instead of separate, unsafe SDK clients."""
    from ai_engine import _call_ai, ai_available

    if not ai_available():
        return "⚠️ AI engine not configured. Set GROQ_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY."
    try:
        response, error = _call_ai(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            system="You are a concise Indian stock market analysis assistant.",
        )
        if response and response.strip():
            return response.strip()
        logger.warning("AI provider chain exhausted: %s", (error or "empty response")[:250])
    except Exception:
        logger.exception("AI provider chain failed")
    return "⚠️ AI engine error. All providers failed."


def safe_llm_call(prompt: str, max_tokens: int = 500) -> Tuple[bool, str]:
    result = actual_llm_call(prompt, max_tokens=max_tokens)
    failed_prefixes = ("⚠️ AI engine not configured", "⚠️ AI engine error")
    return not result.startswith(failed_prefixes), result


def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    import history as hist
    import limits as lim
    allowed, remaining, limit = lim.can_use_llm(user_id)
    if not allowed:
        return f"🚫 You've used all {limit} AI analyses for today. Please try again tomorrow."

    success, response = safe_llm_call(prompt)
    if not success:
        return "⚠️ AI service temporarily unavailable. Your quota was not used."

    lim.register_llm_usage(user_id)
    hist.add_history_item(user_id, prompt, response, item_type)

    if (remaining - 1) <= 3:
        response += f"\n\n<i>⚠️ {remaining - 1} AI calls left today.</i>"

    return response
