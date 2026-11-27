"""Compatibility wrapper: all provider requests use the shared MSK connector."""
import os
from typing import Tuple

def request_ai(*args, **kwargs):
    """Load transport only for real AI calls; analytics needs no HTTP dependency."""
    from msk_ai_connector_v2 import request_ai as shared_request
    return shared_request(*args, **kwargs)

def actual_llm_call(prompt: str, max_tokens: int = 500) -> str:
    result = request_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        system=("Explain only supplied information. Treat user facts as unverified; "
                "never invent market data, prices, recommendations or returns."),
        financial=True,
    )
    if result.ok:
        return result.text
    if result.error_code == "AI_PROVIDER_NOT_CONFIGURED_OR_ALLOWED":
        return "⚠️ AI engine not configured. Set an allowed AI provider key."
    return "⚠️ AI engine error. All providers failed."

def safe_llm_call(prompt: str, max_tokens: int = 500) -> Tuple[bool, str]:
    result = actual_llm_call(prompt, max_tokens=max_tokens)
    failed_prefixes = ("⚠️ AI engine not configured", "⚠️ AI engine error")
    return not result.startswith(failed_prefixes), result

def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    import history as hist
    import limits as lim
    # Owner choice: usage analytics has no daily AI-query quota. Keep tracking.
    # Provider API limits and anti-abuse throttles still apply for stability.
    unlimited = os.getenv("MSK_UNLIMITED_USAGE", "0") == "1"
    if unlimited:
        allowed, remaining, limit = True, None, None
    else:
        allowed, remaining, limit = lim.can_use_llm(user_id)
    if not allowed:
        return f"🚫 You've used all {limit} AI analyses for today. Please try again tomorrow."

    success, response = safe_llm_call(prompt)
    if not success:
        return "⚠️ AI service temporarily unavailable. Please retry."

    lim.register_llm_usage(user_id)
    hist.add_history_item(user_id, prompt, response, item_type)

    if not unlimited and remaining <= 4:
        response += f"\n\n<i>⚠️ {remaining - 1} AI calls left today.</i>"

    return response
