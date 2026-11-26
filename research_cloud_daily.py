"""Optional same-process daily cloud warmup. Never sends Telegram.

Single Render process, explicit symbol allowlist, no extra cron or paid runner.
A sleeping service cannot guarantee the window; on-demand retrieval is retained.
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
MAX_SYMBOLS = 20


def configured_symbols():
    raw = os.getenv("MSK_RESEARCH_DAILY_SYMBOLS", "")
    symbols = list(dict.fromkeys(x.strip().upper() for x in raw.split(",") if x.strip()))
    if (not symbols or len(symbols) > MAX_SYMBOLS or
            any(not re.fullmatch(r"[A-Z0-9&_-]{1,30}", x) for x in symbols)):
        return ()
    return tuple(symbols)


def eligible(now):
    if now.tzinfo is None:
        return False
    local = now.astimezone(IST)
    return local.weekday() < 5 and (17, 30) <= (local.hour, local.minute) < (19, 0)


def run_once(symbols):
    from research_pdf_drive import enabled, load_cloud_report
    from report_generator import generate_stock_report_pdf
    if not enabled() or not symbols:
        return
    for symbol in symbols:
        if not eligible(datetime.now(IST)):
            break
        path = None
        try:
            existing = load_cloud_report(symbol)
            if existing:
                path = existing[0]
                continue
            ok, result, _ = generate_stock_report_pdf(symbol)
            if ok:
                path = result
                LOG.info("MSK_DAILY_RESEARCH_GENERATED symbol=%s", symbol)
            else:
                LOG.warning("MSK_DAILY_RESEARCH_WITHHELD symbol=%s", symbol)
        except Exception as exc:
            LOG.warning("MSK_DAILY_RESEARCH_FAILED symbol=%s type=%s",
                        symbol, type(exc).__name__)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
        time.sleep(3)


def loop():
    from research_pdf_drive import enabled
    if os.getenv("MSK_RESEARCH_DAILY_ENABLED") != "1" or not enabled():
        return
    symbols = configured_symbols()
    if not symbols:
        LOG.warning("MSK_DAILY_RESEARCH_DISABLED reason=invalid_allowlist")
        return
    last = None
    while True:
        now = datetime.now(IST)
        if eligible(now) and now.date() != last:
            last = now.date()
            try:
                run_once(symbols)
            except Exception as exc:
                LOG.warning("MSK_DAILY_RESEARCH_LOOP_FAILED type=%s", type(exc).__name__)
        time.sleep(60)
