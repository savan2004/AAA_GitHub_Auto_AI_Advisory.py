# llm_wrapper.py — Safe LLM wrapper with lazy imports + per-user limits
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")

def actual_llm_call(prompt: str, max_tokens: int = 500) -> str:
    used_any = False

    # ── GROQ ──────────────────────────────────────────────────────────────
    if GROQ_API_KEY:
        used_any = True
        try:
            from ai_engine import _GROQ_MODELS, _make_groq_client, _provider_error_code
            # Share validated Groq model IDs and safe client creation.
            # Do not monkey-patch httpx.Client globally (thread unsafe).
            client = _make_groq_client(GROQ_API_KEY)
            for model in _GROQ_MODELS:
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
                    logger.warning("Groq model %s failed: %s", model, _provider_error_code(model_error))
        except Exception as e:
            logger.error("Groq client error: %s", _provider_error_code(e))

    # ── Gemini: share the supported, credential-safe REST adapter ─────────
    if GEMINI_API_KEY:
        used_any = True
        from gemini_provider import call_gemini
        text, error = call_gemini(
            GEMINI_API_KEY,
            [{"role": "user", "content": prompt}],
            max_tokens,
            "You are a concise Indian stock market analysis assistant.",
        )
        if text:
            return text
        logger.warning("Gemini provider failed: %s", error)

    # ── OpenAI: tertiary provider when Groq and Gemini cannot answer ───
    from ai_engine import _key, _provider_error_code
    openai_key = _key("OPENAI_API_KEY", "OPENAI_KEY")
    if openai_key:
        used_any = True
        try:
            from openai import OpenAI
            with OpenAI(api_key=openai_key, timeout=15.0, max_retries=1) as client:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a concise Indian stock market analysis assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                answer = (response.choices[0].message.content or "").strip()
                if answer:
                    return answer
        except Exception as exc:
            logger.warning("OpenAI fallback failed: %s", _provider_error_code(exc))

    if not used_any:
        return "⚠️ AI engine not configured. Set GROQ_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY."
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
