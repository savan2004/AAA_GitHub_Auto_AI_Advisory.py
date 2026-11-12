"""Fail-closed price labels for MSK Bot Analysis and Technical cards.

Daily OHLCV candles are historical facts, never a live market quote.
An explicit exchange-observed price may be used for illustrative target lines
only when its original source+time survives validation and market is open.
"""
import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
QUOTE_SOURCES = frozenset(("Yahoo Finance v8", "NSE India"))


def technical_price_context(history, quote=None, *, now=None):
    """Return display context or None if the last historical bar is unreliable.

    The helper is intentionally pure; quote retrieval/verification happens in
    data_engine.get_verified_quote. This double-checks the fields before use.
    Weekend/holiday gaps over four days fail closed, not guessed open sessions.
    """
    now = now or datetime.now(IST)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    now = now.astimezone(IST)
    try:
        if history is None or len(history) < 2 or not history.index.is_monotonic_increasing:
            return None
        last_index = history.index[-1]
        if not isinstance(last_index, (date, datetime, pd.Timestamp)):
            return None
        ts = pd.Timestamp(last_index)
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            candle_day = ts.tz_convert(IST).date()
        else:
            candle_day = ts.date()
        age_days = (now.date() - candle_day).days
        if age_days < 0 or age_days > 4:
            return None
        close = float(history["Close"].iloc[-1])
        previous = float(history["Close"].iloc[-2])
        if not (math.isfinite(close) and close > 0 and
                math.isfinite(previous) and previous > 0):
            return None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        return None

    text = (
        f"⏱ Historical close ({candle_day.isoformat()}): ₹{close:,.2f}"
        " | dated daily candle, not live LTP"
    )
    current_text = "⚠️ Timestamp-verified current quote unavailable."
    target_price = None
    if isinstance(quote, dict) and quote.get("quote_source") in QUOTE_SOURCES:
        raw_price = quote.get("price")
        raw_time = quote.get("quote_asof_ist")
        status = quote.get("quote_status")
        try:
            observed = float(raw_price)
            stamped = datetime.fromisoformat(str(raw_time)).astimezone(IST)
            age_sec = (now - stamped).total_seconds()
            is_open = (now.weekday() < 5 and
                       (9, 15) <= (now.hour, now.minute) < (15, 30))
            max_age = 1800 if is_open else 4 * 86400
            if (math.isfinite(observed) and observed > 0 and
                    stamped.tzinfo is not None and
                    -120 <= age_sec <= max_age and
                    status == ("recent_observation" if is_open else "last_observed_session")):
                label = "Recent exchange observation" if is_open else "Last observed session"
                current_text = (
                    f"📊 {label}: ₹{observed:,.2f} | "
                    f"{quote['quote_source']} | {stamped:%Y-%m-%d %H:%M} IST"
                )
                if is_open:
                    target_price = observed
        except (TypeError, ValueError, KeyError, OverflowError):
            pass
    return {"historical_close": close, "previous_close": previous,
            "candle_date": candle_day.isoformat(), "historical_line": text,
            "verified_quote_line": current_text, "target_price": target_price}


def guarded_target_line(formatter, trend, atr, market):
    """Prevent action-like target/SL output from undated or closed-session prices."""
    if not market or market.get("target_price") is None:
        return "🎯 Target/SL withheld — no verified current-session price."
    return formatter(trend, market["target_price"], atr)
