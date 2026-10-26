"""Opt-in scheduled Telegram channel publisher.

Run as a separate Render cron job. Never starts on module import.
Required: TELEGRAM_TOKEN, TELEGRAM_CHANNEL_ID, CHANNEL_PUBLISH_ENABLED=true.
"""
import datetime as dt
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger(__name__)


def publish_once(env=None, opener=None, now=None):
    env = os.environ if env is None else env
    if env.get("CHANNEL_PUBLISH_ENABLED", "").lower() != "true":
        return {"status": "disabled"}
    token = env.get("TELEGRAM_TOKEN", "")
    channel = env.get("TELEGRAM_CHANNEL_ID", "")
    if not token or not channel:
        raise ValueError("Missing Telegram publishing configuration")
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    # Require an externally verified report; never publish a diagnostic heartbeat.
    text = env.get("CHANNEL_REPORT_TEXT", "").strip()
    if not text:
        raise ValueError("No verified channel report supplied; refusing to publish")
    if len(text) > 4096:
        raise ValueError("Channel report exceeds Telegram message length limit")
    payload = urllib.parse.urlencode({"chat_id": channel, "text": text}).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload, method="POST")
    opener = opener or urllib.request.urlopen
    with opener(request, timeout=15) as response:
        result = json.load(response)
    if not result.get("ok") or not result.get("result", {}).get("message_id"):
        raise RuntimeError("Telegram did not confirm message delivery")
    return {"status": "sent", "message_id": result["result"]["message_id"]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = publish_once()
        LOG.info("Channel publishing result: %s", result["status"])
    except Exception:
        LOG.exception("Channel publishing failed")
        raise SystemExit(1)
