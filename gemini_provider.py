"""Small Gemini REST adapter shared by MSK AI code paths.

Uses the documented Gemini generateContent API and no legacy Google SDK.
Never put credentials in URLs, log bodies, or exception messages.
"""
import requests

# Stable, supported Gemini API models as of October 2026.
GEMINI_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"


def call_gemini(api_key, messages, max_tokens=500, system=""):
    """Return (generated_text, error) without leaking credentials or making a fake success."""
    if not api_key:
        return "", "Gemini API key not configured"
    contents = [
        {"role": "model" if message.get("role") == "assistant" else "user",
         "parts": [{"text": message["content"]}]}
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in ("user", "assistant")
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]
    if not contents:
        return "", "No Gemini message content"

    body = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max(1, min(int(max_tokens), 4096))},
    }
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}

    errors = []
    for model in GEMINI_MODELS:
        try:
            response = requests.post(
                _ENDPOINT.format(model),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=15,
            )
            if response.status_code in (401, 403):
                # Other models cannot repair an invalid key or denied project.
                return "", "Gemini authentication/permission failed (HTTP {})".format(response.status_code)
            if response.status_code != 200:
                errors.append("{}: HTTP {}".format(model, response.status_code))
                continue
            data = response.json()
            candidates = data.get("candidates", []) if isinstance(data, dict) else []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            answer = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and not part.get("thought")
                and isinstance(part.get("text"), str)
            ).strip()
            if answer:
                return answer, ""
            errors.append("{}: empty response".format(model))
        except requests.Timeout:
            errors.append("{}: timeout".format(model))
        except requests.RequestException:
            errors.append("{}: network error".format(model))
        except (ValueError, TypeError, IndexError, AttributeError, KeyError):
            errors.append("{}: invalid response".format(model))
    return "", "; ".join(errors)
