"""Read-only MSK chart context shared by automatic and user-requested charts.

Uses only completed, ordered OHLCV bars supplied by the existing generator.
No network, no orders, no targets, no score thresholds, no extra dependencies.
Price-level reference windows EXCLUDE the candle being assessed.
"""
from __future__ import annotations

import math


def completed_weekly_reference(daily):
    """Weekly trend from the SAME verified daily candles, with no partial week.

    A Monday-Thursday last session is never promoted into a completed Friday
    weekly bar. Holiday-shortened weeks are conservatively excluded if the
    calendar Friday wasn't independently verified.
    """
    import pandas as pd
    if daily is None or daily.empty or "Close" not in daily:
        return None
    weekly = daily["Close"].resample("W-FRI").last().dropna()
    last = pd.Timestamp(daily.index[-1])
    if last.weekday() != 4:
        weekly = weekly.iloc[:-1]
    return pd.DataFrame({"Close": weekly}) if len(weekly) >= 10 else None


def summarize_chart(highs, lows, closes, volumes, ema_fast, ema_slow, macd_hist):
    """Causal 20/50/100/200-session structure + a descriptive momentum label."""
    n = len(closes)
    if n < 55 or len(highs) != n or len(lows) != n or len(volumes) != n:
        raise ValueError("At least 55 aligned completed OHLCV candles required")
    if not all(math.isfinite(float(v)) for row in (highs, lows, closes, volumes)
               for v in row):
        raise ValueError("Non-finite OHLCV input")
    if any(float(lows[i]) <= 0 or float(lows[i]) > float(closes[i])
           or float(highs[i]) < float(closes[i]) or float(highs[i]) < float(lows[i])
           or float(volumes[i]) < 0 for i in range(n)):
        raise ValueError("Invalid OHLCV relationship")
    if not all(math.isfinite(float(x)) for x in
               (ema_fast, ema_slow, macd_hist)):
        raise ValueError("Unstable trend indicator")

    last = float(closes[-1])
    windows = {}
    for lookback in (20, 50, 100, 200):
        if n <= lookback:
            continue
        previous_high = max(float(x) for x in highs[-(lookback + 1):-1])
        previous_low = min(float(x) for x in lows[-(lookback + 1):-1])
        windows[lookback] = ("ABOVE" if last > previous_high else
                             "BELOW" if last < previous_low else "WITHIN")
    if 20 not in windows:
        raise ValueError("20 completed prior candles unavailable")

    support = min(float(x) for x in lows[-21:-1])
    resistance = max(float(x) for x in highs[-21:-1])
    level = ("20D UPSIDE CLOSE" if last > resistance else
             "20D DOWNSIDE CLOSE" if last < support else
             "INSIDE PREVIOUS 20D RANGE")
    outlook = ("POSITIVE MOMENTUM" if last > ema_fast > ema_slow and macd_hist > 0 else
               "NEGATIVE MOMENTUM" if last < ema_fast < ema_slow and macd_hist < 0 else
               "MIXED MOMENTUM")
    prior_volumes = [float(v) for v in volumes[-21:-1]]
    if all(v > 0 for v in prior_volumes) and float(volumes[-1]) > 0:
        ratio = float(volumes[-1]) / (sum(prior_volumes) / 20)
    else:
        ratio = None
    if level == "INSIDE PREVIOUS 20D RANGE":
        confirmation = "NO NEW LEVEL BREAK"
    elif (ratio is not None and ratio >= 1.5 and
          ((level == "20D UPSIDE CLOSE" and outlook == "POSITIVE MOMENTUM") or
           (level == "20D DOWNSIDE CLOSE" and outlook == "NEGATIVE MOMENTUM"))):
        confirmation = "PRICE + TREND + VOLUME ALIGNED"
    else:
        confirmation = "PRICE BREAK; CONFIRMATION MIXED"

    periods = "  ".join(f"{k}D:{v}" for k, v in windows.items())
    return {"support": support, "resistance": resistance,
            "outlook": outlook, "level": level, "confirmation": confirmation,
            "volume_ratio": ratio, "periods": periods, "windows": windows}
