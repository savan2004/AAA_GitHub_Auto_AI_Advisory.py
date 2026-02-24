# llm_wrapper.py
import limits
import history

# --- Replace this with your actual LLM call (DeepSeek, Groq, Gemini, etc.) ---
def actual_llm_call(prompt: str) -> str:
    """
    This is a placeholder. Replace with your real API call.
    """
    # Example using your existing code:
    # response = your_existing_function(prompt)
    # return response
    return f"Simulated LLM response for: {prompt[:50]}..."

def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    """
    Main entry point for all LLM calls. Handles quota, caching, and history.
    """
    allowed, remaining, limit = limits.can_use_llm(user_id)

    # Quota message (used later if needed)
    quota_msg = f"📊 You have {remaining} out of {limit} AI calls left today."

    if not allowed:
        return (
            f"❌ You've used all {limit} AI analyses for today.\n\n"
            f"Please try again tomorrow or upgrade to Pro (200 calls/day)."
        )

    # ---- Call the actual LLM ----
    try:
        response = actual_llm_call(prompt)
    except Exception as e:
        return f"⚠️ LLM service error: {e}"

    # ---- Register usage and store in history ----
    limits.register_llm_usage(user_id)
    history.add_history_item(user_id, prompt, response, item_type)

    # Append low‑quota warning if needed
    if remaining - 1 <= 3:
        response += f"\n\n⚠️ You have {remaining-1} AI calls left today."

    return response
