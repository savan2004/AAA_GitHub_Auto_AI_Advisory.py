"""Shared provider-output quality boundary. Not a financial evidence verifier."""
import re

def check_completion(payload, provider):
    """Refuse explicitly truncated, blocked or tool-call-only completions."""
    if not isinstance(payload, dict):
        raise ValueError("AI_PROVIDER_SCHEMA")
    if provider in ("groq", "openai"):
        rows = payload.get("choices")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ValueError("AI_PROVIDER_SCHEMA")
        row = rows[0]
        if row.get("finish_reason") not in (None, "stop"):
            raise ValueError("AI_INCOMPLETE_OUTPUT")
        message = row.get("message")
        if not isinstance(message, dict):
            raise ValueError("AI_PROVIDER_SCHEMA")
        if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
            raise ValueError("AI_INCOMPLETE_OUTPUT")
    elif provider == "gemini":
        feedback = payload.get("promptFeedback") or {}
        if not isinstance(feedback, dict) or feedback.get("blockReason"):
            raise ValueError("AI_INCOMPLETE_OUTPUT")
        rows = payload.get("candidates")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ValueError("AI_PROVIDER_SCHEMA")
        if rows[0].get("finishReason") not in (None, "STOP"):
            raise ValueError("AI_INCOMPLETE_OUTPUT")

def gemini_text(parts):
    if not isinstance(parts, list):
        raise ValueError("AI_PROVIDER_SCHEMA")
    visible = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("AI_PROVIDER_SCHEMA")
        if part.get("thought") is True:
            continue
        if not isinstance(part.get("text"), str):
            raise ValueError("AI_PROVIDER_SCHEMA")
        visible.append(part["text"])
    return "".join(visible)

def check_visible_text(text, key):
    if re.search(r"<\s*/?\s*(?:think|analysis|reasoning)\b", text, re.I):
        raise ValueError("AI_UNSAFE_REASONING_OUTPUT")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise ValueError("AI_UNSAFE_OUTPUT")
    if isinstance(key, str) and len(key) >= 8 and key in text:
        raise ValueError("AI_UNSAFE_OUTPUT")
    return text
