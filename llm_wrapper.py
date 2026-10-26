# llm_wrapper.py — Safe LLM wrapper with lazy imports + per-user limits
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Lazy Gemini init (no module-level crash) ──────────────────────────────
_gemini_configured = False

def _ensure_gemini():
    global _gemini_configured
    if _gemini_configured or not GEMINI_API_KEY:
        return _gemini_configured
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_configured = True
        logger.info("llm_wrapper: Gemini configured")
    except Exception as e:
        logger.error(f"llm_wrapper: Gemini config error: {e}")
    return _gemini_configured


def actual_llm_call(prompt: str, max_tokens: int = 500) -> str:
    """Use the shared verified provider chain; no global httpx monkeypatches.

    A configured key is NOT proof that the model/endpoint is operational.
    """
    from ai_engine import _call_ai
    response, reason = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        system=("Explain supplied market data only. Never infer a live price, "
                "as-of date, issuer results or news without a named source.")
    )
    if response:
        return response
    if reason == "No AI provider configured":
        return "⚠️ AI engine not configured. Set a supported AI provider key."
    logger.warning("LLM request failed (safe status): %s", reason)
    return "⚠️ AI engine error. All available providers failed."


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
