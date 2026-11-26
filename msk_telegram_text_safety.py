"""Retry only structured Telegram API rejection; never ambiguous transport."""
import html
import re
import time


def send_text_once(bot, chat_id, text, parse_mode, kwargs, logger, *, pause=None):
    pause = time.sleep if pause is None else pause
    payload, mode = text, parse_mode
    for attempt in range(2):
        try:
            sent = bot.send_message(chat_id, payload, parse_mode=mode, **kwargs)
        except Exception as exc:
            code = getattr(exc, "error_code", None)
            body = getattr(exc, "result_json", None)
            if (type(code) is not int or not isinstance(body, dict)
                    or body.get("ok") is not False or body.get("error_code") != code):
                code = None
            logger.warning("[Telegram API] text rejected/uncertain: HTTP %s",
                           code if code in (400, 401, 403, 429) else "unknown")
            if attempt or code is None:
                return None
            if code == 429:
                params = body.get("parameters")
                delay = params.get("retry_after") if isinstance(params, dict) else None
                if type(delay) is not int or not 1 <= delay <= 5:
                    return None
                pause(delay)
                continue
            description = body.get("description")
            if (code == 400 and mode == "HTML" and isinstance(description, str)
                    and "can't parse entities" in description.casefold()):
                if getattr(bot, "parse_mode", None) is not None or kwargs.get("entities"):
                    return None
                payload = html.unescape(re.sub(r"<[^>]+>", "", str(payload)))
                mode = None
                if not payload.strip():
                    return None
                continue
            return None
        mid = getattr(sent, "message_id", None)
        if type(mid) is not int or mid <= 0:
            logger.warning("[Telegram API] missing valid text message ID; no resend")
            return None
        return sent
    return None
