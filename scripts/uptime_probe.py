"""One-shot external MSK Bot HTTP probe. Run OUTSIDE Render.

This measures one HTTP sample, not continuous bot uptime or Telegram delivery.
No credentials, custom URLs, paid APIs, or Telegram sends are required.

Usage: python scripts/uptime_probe.py
"""
from datetime import datetime, timezone
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://aaa-github-auto-ai-advisory-py.onrender.com/"
TIMEOUT_SECONDS = 20


def check(url=URL, opener=urlopen, clock=time.monotonic):
    start = clock()
    result = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "http_ok": False,
        "http_status": None,
        "latency_ms": None,
        "telegram_delivery": "not_verified",
        "note": "Single HTTP probe only; not an uptime percentage.",
    }
    try:
        request = Request(url, headers={"User-Agent": "MSK-bot-uptime-probe/1.0",
                                        "Cache-Control": "no-cache"})
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            # Read a bounded response; never log headers or tokens.
            payload = json.loads(response.read(2048).decode("utf-8"))
        result["http_status"] = status
        result["http_ok"] = bool(status == 200 and isinstance(payload, dict) and
                                 payload.get("status") in ("ok", "alive"))
    except HTTPError as exc:
        result["http_status"] = exc.code
        result["error_type"] = "HTTPError"
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        result["error_type"] = type(exc).__name__
    finally:
        result["latency_ms"] = round(max(0, clock() - start) * 1000, 1)
    return result


def main():
    record = check()
    print(json.dumps(record, separators=(",", ":")))
    return 0 if record["http_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
