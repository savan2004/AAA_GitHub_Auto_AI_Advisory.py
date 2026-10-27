"""Single safe AI provider gateway for all MSK Research/Chat handlers.

Official model IDs checked 2026-10-26. Explicit bounded timeouts, no monkey
patches of httpx, no raw provider errors or API keys written to logs.
This layer has NO access to stock prices or filings. Callers must supply
dated verified market evidence; generated prose never becomes a data source.
"""
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

GROQ_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
GEMINI_MODELS = ("gemini-3.8-flash", "gemini-3.6-flash")
OPENAI_MODELS = ("gpt-4o-mini",)
PROVIDERS = ("groq", "gemini", "openai")
SYSTEM_GUARD = (
    "Use only supplied observations as factual current-market evidence. "
    "Never invent prices, financial ratios, dates, sources, news, volume, "
    "recommendations or performance. If evidence lacks source, as-of time "
    "or a required value, disclose 'Unavailable/not verified', not an estimate. "
    "Historical closes are NOT live LTP. Distinguish provider-defined "
    "revenueGrowth from audited annual revenue growth. "
    "Avoid guaranteed returns, exact future targets or certainty."
)
_TIMEOUT = (4, 14)
_MAX_INPUT_CHARS = 16000
_MAX_TURNS = 10


def _models(env, defaults):
    """Ignore retired/non-allowlisted model IDs, including env overrides."""
    raw = os.getenv(env, "").strip()
    candidates = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(defaults)
    accepted = tuple(dict.fromkeys(m for m in candidates if m in defaults))
    return accepted or defaults


def _messages(messages):
    """Allow only ordinary user/assistant turns; bound context and length."""
    result = []
    for item in messages[-_MAX_TURNS:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            result.append({"role": role, "content": content[:_MAX_INPUT_CHARS]})
    return result


def _read_text(response, provider):
    """Never expose model-internal reasoning; return only final prose."""
    obj = response.json()
    if provider == "gemini":
        candidates = obj.get("candidates") or []
        contents = (candidates[0].get("content") or {}) if candidates else {}
        return "\n".join(
            part["text"] for part in (contents.get("parts") or [])
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
    choices = obj.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = message.get("content")
    return text.strip() if isinstance(text, str) else ""


def _single_request(provider, model, api_key, messages, system, max_tokens):
    """Make one authenticated bounded HTTPS request without SDK version coupling."""
    if provider in ("groq", "openai"):
        url = (
            "https://api.groq.com/openai/v1/chat/completions"
            if provider == "groq" else "https://api.openai.com/v1/chat/completions"
        )
        data = {
            "model": model,
            "messages": [{"role": "system", "content": system}] + messages,
            "max_completion_tokens" if provider == "groq" else "max_tokens": max_tokens,
        }
        if provider == "groq":
            # OpenAI GPT-OSS on Groq needs an explicit low reasoning budget;
            # this avoids spending small output limits entirely on reasoning.
            data["reasoning_effort"] = "low"
        else:
            data["temperature"] = 0.1
        response = requests.post(
            url, headers={"Authorization": f"Bearer {api_key}",
                          "Content-Type": "application/json"},
            json=data, timeout=_TIMEOUT,
        )
    else:
        data = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if msg["role"] == "assistant" else "user",
                 "parts": [{"text": msg["content"]}]}
                for msg in messages
            ],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=data, timeout=_TIMEOUT,
        )
    if response.status_code != 200:
        # Deliberately do NOT print response body/URL/headers. Body can contain
        # credentials, request payloads or private position information.
        raise ProviderFailure(f"HTTP {response.status_code}")
    return _read_text(response, provider)


class ProviderFailure(Exception):
    """Safe sanitized error, with no user or credential payload."""


def call(messages, max_tokens=500, system="", *, include_askfuzz=None):
    """Groq -> Gemini -> OpenAI -> opt-in AskFuzz, fail closed if unavailable.

    Returns (text, safe diagnostic string). Zero network calls without valid
    prompt/credentials; never tests real services merely to report configured.
    """
    clean = _messages(messages)
    if not clean:
        return "", "No valid user message"
    max_tokens = min(1500, max(80, int(max_tokens)))
    guard = SYSTEM_GUARD + ("\n" + system[:5000] if system else "")
    errors = []
    configured = False
    key_map = {
        "groq": os.getenv("GROQ_API_KEY", "").strip(),
        "gemini": (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip(),
        "openai": (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY") or "").strip(),
    }
    for provider, models, env in (
        ("groq", GROQ_MODELS, "GROQ_MODELS"),
        ("gemini", GEMINI_MODELS, "GEMINI_MODELS"),
        ("openai", OPENAI_MODELS, "OPENAI_MODEL"),
    ):
        key = key_map[provider]
        if not key:
            continue
        configured = True
        for model in _models(env, models):
            try:
                text = _single_request(provider, model, key, clean, guard, max_tokens)
                if text:
                    return text, ""
                errors.append(f"{provider}/{model}: empty output")
            except ProviderFailure as err:
                errors.append(f"{provider}/{model}: {str(err)}")
            except (requests.RequestException, ValueError, TypeError, KeyError):
                errors.append(f"{provider}/{model}: transport/parse failure")
            except Exception:
                errors.append(f"{provider}/{model}: client failure")
    # An optional specialized vendor can be used only through its existing
    # adapter; it is not a substitute for verified company market data.
    if include_askfuzz is not None and os.getenv("ASKFUZZ_API_KEY", "").strip():
        configured = True
        try:
            text, err = include_askfuzz(os.getenv("ASKFUZZ_API_KEY", "").strip(),
                                         clean, max_tokens, guard)
            if isinstance(text, str) and text.strip():
                return text.strip(), ""
            errors.append("askfuzz: unavailable")
        except Exception:
            errors.append("askfuzz: request failed")
    return "", "; ".join(errors) if configured else "No AI provider configured"
