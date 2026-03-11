# llm_wrapper.py
# Full working LLM wrapper using Groq (primary) and Gemini (fallback)

import os
import logging
from typing import Tuple

from groq import Groq
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Load keys from environment ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Configure Gemini if key present
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("[LLM] Gemini configured successfully")
    except Exception as e:
        logger.error(f"[LLM] Gemini config error: {e}")


def actual_llm_call(prompt: str, max_tokens: int = 500) -> str:
    """
    Calls Groq (llama-3.3-70b-versatile) first.
    Falls back to Gemini (gemini-1.5-flash) if Groq fails.
    Returns raw text string. Never raises exception.
    """
    used_any = False

    # ---- 1. Try Groq: llama-3.3-70b-versatile (best free model) ----
    if GROQ_API_KEY:
        used_any = True
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional SEBI-registered financial analyst "
                            "specializing in Indian equities (NSE/BSE). "
                            "Give clear, data-driven, actionable advice."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info("[LLM] Groq response received successfully")
                return text
        except Exception as e:
            logger.error(f"[LLM] Groq llama-3.3-70b error: {e}")

        # Groq fallback model
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info("[LLM] Groq llama3-70b fallback response received")
                return text
        except Exception as e:
            logger.error(f"[LLM] Groq llama3-70b error: {e}")

    # ---- 2. Try Gemini: gemini-1.5-flash ----
    if GEMINI_API_KEY:
        used_any = True
        try:
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config={
                    "max_output_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            resp = model.generate_content(prompt)
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                logger.info("[LLM] Gemini response received successfully")
                return text
        except Exception as e:
            logger.error(f"[LLM] Gemini 1.5-flash error: {e}")

        # Gemini fallback model
        try:
            model = genai.GenerativeModel(
                model_name="gemini-pro",
                generation_config={
                    "max_output_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            resp = model.generate_content(prompt)
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                logger.info("[LLM] Gemini-pro fallback response received")
                return text
        except Exception as e:
            logger.error(f"[LLM] Gemini-pro error: {e}")

    # ---- 3. Final failure message ----
    if not used_any:
        logger.error("[LLM] No API keys configured (GROQ_API_KEY and GEMINI_API_KEY both missing)")
        return "AI engine not configured: set GROQ_API_KEY or GEMINI_API_KEY in environment."

    logger.error("[LLM] All LLM providers failed. Check API keys and server logs.")
    return "AI engine error: all providers failed. Check server logs for details."


def safe_llm_call(prompt: str, max_tokens: int = 500) -> Tuple[bool, str]:
    """
    Wrapper that returns (success: bool, text: str).
    success=False only if both providers fail completely.
    """
    result = actual_llm_call(prompt, max_tokens=max_tokens)
    failed_msgs = [
        "AI engine not configured",
        "AI engine error",
    ]
    success = not any(msg in result for msg in failed_msgs)
    return success, result


def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    """
    Main entry point. Checks quota, calls LLM, registers usage, stores history.
    Import limits and history from main.py context when needed.
    """
    try:
        import limits as lim
        import history as hist

        allowed, remaining, limit = lim.can_use_llm(user_id)
        if not allowed:
            return (
                f"You have used all {limit} AI analyses for today.\n"
                f"Please try again tomorrow."
            )

        success, response = safe_llm_call(prompt)

        if not success:
            return f"AI service temporarily unavailable.\n{response}"

        lim.register_llm_usage(user_id)
        hist.add_history_item(user_id, prompt, response, item_type)

        if remaining - 1 <= 3:
            response += f"\n\nYou have {remaining - 1} AI calls left today."

        return response

    except ImportError:
        # If called standalone without limits/history modules
        success, response = safe_llm_call(prompt)
        return response if success else f"LLM error: {response}"
