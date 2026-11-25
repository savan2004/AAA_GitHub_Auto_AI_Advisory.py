"""MSK AI provider connector v2: bounded, attributable and safe by default.

One request per configured provider, no automatic paid fallback, no model
hallucinations accepted as issuer financial evidence. Uses requests already in
requirements.txt and keeps the Telegram worker independent of vendor SDKs.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import re
from typing import Mapping

import requests


GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.5-flash-lite"
OPENAI_MODEL = "gpt-4o-mini"
_MAX_REQUEST_CHARS = 18000
_MAX_OUTPUT_CHARS = 14000
_MAX_TOKENS = 1800
_MAX_RESPONSE_BYTES = 65000
_NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?%?(?![\w])")
_BAD_MODEL = frozenset((
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemini-1.5-flash",
))
_MODEL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.\-/]{2,100}\Z")


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str
    error_code: str
    attempts: tuple

    @property
    def ok(self):
        return bool(self.text) and not self.error_code


def _key(env, *names):
    for name in names:
        v = env.get(name, "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def configured_providers(env=None):
    """Credentials are only *configured*, not proven available or free."""
    env = os.environ if env is None else env
    allowed = [
        ("groq", _key(env, "GROQ_API_KEY"), GROQ_MODEL),
        ("gemini", _key(env, "GEMINI_API_KEY", "GOOGLE_API_KEY"), GEMINI_MODEL),
    ]
    if env.get("MSK_AI_ALLOW_PAID") == "1":
        allowed.append(("openai", _key(env, "OPENAI_API_KEY", "OPENAI_KEY"), OPENAI_MODEL))
    if env.get("MSK_AI_ALLOW_ASKFUZZ") == "1":
        allowed.append(("askfuzz", _key(env, "ASKFUZZ_API_KEY"), "askfuzz-query"))
    return tuple((name, key, model) for name, key, model in allowed if key)


def _prepare(messages, system, max_tokens):
    if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 12:
        raise ValueError("AI_INPUT_INVALID")
    if not isinstance(system, str) or len(system) > 2500:
        raise ValueError("AI_INPUT_INVALID")
    if type(max_tokens) is not int or not 1 <= max_tokens <= _MAX_TOKENS:
        raise ValueError("AI_INPUT_INVALID")
    payload = []
    for row in messages:
        if (not isinstance(row, dict) or row.get("role") not in ("user", "assistant")
                or not isinstance(row.get("content"), str)):
            raise ValueError("AI_INPUT_INVALID")
        body = row["content"].strip()
        if not body or len(body) > _MAX_REQUEST_CHARS:
            raise ValueError("AI_INPUT_INVALID")
        payload.append({"role": row["role"], "content": body})
    if sum(len(x["content"]) for x in payload) > _MAX_REQUEST_CHARS:
        raise ValueError("AI_INPUT_INVALID")
    return ([{"role":"system", "content":system}] if system else []) + payload


def _numbers(text):
    # Only financial numbers from the supplied facts may appear in an AI report.
    # Numbered Markdown list markers are formatting, not financial assertions.
    cleaned = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    result = set()
    for match in _NUMBER.finditer(cleaned):
        token = match.group().replace(",", "").rstrip("%")
        try:
            d = Decimal(token)
        except InvalidOperation:
            continue
        if d.is_finite():
            result.add(d)
    return result


def _response_text(obj, provider):
    if not isinstance(obj, dict):
        raise ValueError("AI_PROVIDER_SCHEMA")
    if provider in ("groq", "openai"):
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("AI_PROVIDER_SCHEMA")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = message.get("content") if isinstance(message, dict) else None
    elif provider == "gemini":
        candidates = obj.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("AI_PROVIDER_SCHEMA")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "".join(str(x.get("text") or "") for x in parts
                       if isinstance(x, dict)) if isinstance(parts, list) else None
    else:
        text = obj.get("response")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= _MAX_OUTPUT_CHARS:
        raise ValueError("AI_EMPTY_OR_OVERSIZED")
    text = text.strip()
    if "<think" in text.lower() or "</think" in text.lower():
        raise ValueError("AI_UNSAFE_REASONING_OUTPUT")
    return text


def _request(provider, key, messages, tokens, model, transport):
    if not _MODEL_ID.fullmatch(model) or model in _BAD_MODEL:
        raise ValueError("AI_MODEL_RETIRED_OR_INVALID")
    user_messages = [x for x in messages if x["role"] != "system"]
    instruction = next((x["content"] for x in messages if x["role"] == "system"), "")
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": "Bearer " + key}
        body = {"model": model, "messages": messages,
                "max_completion_tokens": tokens, "temperature": 0.1}
    elif provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent"
        headers = {"x-goog-api-key": key}
        body = {
            "contents": [{"role":"user" if m["role"] == "user" else "model",
                          "parts":[{"text":m["content"]}]}
                         for m in user_messages],
            "generationConfig":{"temperature":0.1, "maxOutputTokens": tokens},
        }
        if instruction:
            body["systemInstruction"] = {"parts":[{"text":instruction}]}
    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization":"Bearer " + key}
        body = {"model":model, "messages":messages,
                "max_tokens":tokens, "temperature":0.1}
    elif provider == "askfuzz":
        url = "https://api.askfuzz.ai/v1/query"
        headers = {"Authorization":"Bearer " + key}
        body = {"query": (instruction + "\n" if instruction else "")
                + user_messages[-1]["content"], "max_tokens":tokens}
    else:
        raise ValueError("AI_PROVIDER_INVALID")
    response = transport(
        url, headers=headers, json=body, timeout=(4, 18),
        allow_redirects=False)
    if response.status_code != 200:
        raise ValueError("AI_HTTP_" + str(response.status_code)
                         if type(response.status_code) is int and 100 <= response.status_code <= 599
                         else "AI_HTTP_ERROR")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ValueError("AI_PROVIDER_RESPONSE_OVERSIZED")
    try:
        payload = response.json()
    except (ValueError, TypeError):
        raise ValueError("AI_PROVIDER_SCHEMA") from None
    return _response_text(payload, provider)


def request_ai(messages, max_tokens=500, system="", *, financial=False,
               env=None, transport=None):
    """Returns bounded result; no raw vendor exceptions, secrets or response bodies.

    For financial=True, reject any number absent from the trusted caller's
    supplied facts. This protects reported metrics, NOT subjective narratives.
    """
    env = os.environ if env is None else env
    try:
        payload = _prepare(messages, system, max_tokens)
    except ValueError:
        return AIResponse("", "", "", "AI_INPUT_INVALID", ())
    providers = configured_providers(env)
    if not providers:
        return AIResponse("", "", "", "AI_PROVIDER_NOT_CONFIGURED_OR_ALLOWED", ())
    post = transport or requests.post
    attempts = []
    source_numbers = _numbers(" ".join(x["content"] for x in payload))
    for provider, key, default_model in providers:
        model = env.get("MSK_" + provider.upper() + "_MODEL", default_model)
        # Explicitly reject retired IDs rather than wasting requests/quotas.
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model) or model in _BAD_MODEL:
            attempts.append(provider + ":AI_MODEL_RETIRED_OR_INVALID")
            continue
        try:
            text = _request(provider, key, payload, max_tokens, model, post)
            if financial and not _numbers(text).issubset(source_numbers):
                attempts.append(provider + ":AI_UNGROUNDED_NUMBER")
                continue
            return AIResponse(text, provider, model, "", tuple(attempts))
        except requests.Timeout:
            attempts.append(provider + ":AI_TIMEOUT")
        except requests.RequestException:
            attempts.append(provider + ":AI_TRANSPORT_ERROR")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            code = str(exc)
            if not re.fullmatch(r"AI_[A-Z0-9_]{2,90}", code):
                code = "AI_PROVIDER_FAILURE"
            attempts.append(provider + ":" + code)
        except Exception:
            # Connector is a best-effort explanatory helper; never crash the bot.
            attempts.append(provider + ":AI_PROVIDER_FAILURE")
    return AIResponse("", "", "", "AI_ALL_PROVIDERS_UNAVAILABLE", tuple(attempts))
