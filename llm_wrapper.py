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
    used_any = False

    # ── GROQ ──────────────────────────────────────────────────────────────
    if GROQ_API_KEY:
        used_any = True
        try:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            for model in ["llama-3.3-70b-versatile", "llama3-70b-8192", "mixtral-8x7b-32768"]:
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "You are a concise Indian stock market analysis assistant."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=max_tokens,
                        temperature=0.3,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text:
                        return text
                except Exception as model_error:
                    logger.warning(f"Groq model {model} failed: {model_error}")
        except Exception as e:
            logger.error(f"Groq client error: {e}")

    # ── Gemini ────────────────────────────────────────────────────────────
    if GEMINI_API_KEY and _ensure_gemini():
        used_any = True
        try:
            import google.generativeai as genai
            for model_name in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
                try:
                    model = genai.GenerativeModel(model_name=model_name)
                    resp = model.generate_content(
                        prompt,
                        generation_config={"max_output_tokens": max_tokens, "temperature": 0.3}
                    )
                    text = (getattr(resp, "text", "") or "").strip()
                    if text:
                        return text
                except Exception as model_error:
                    logger.warning(f"Gemini model {model_name} failed: {model_error}")
        except Exception as e:
            logger.error(f"Gemini client error: {e}")

    if not used_any:
        return "⚠️ AI engine not configured. Set GROQ_API_KEY or GEMINI_API_KEY."
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
