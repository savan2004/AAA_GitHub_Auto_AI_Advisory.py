"""Conservative, testable educational signal and ATR risk plan."""
import math


def make_signal(close, ema20, ema50, atr, *, age_days=0):
    """Return a transparent trading plan, or NO TRADE when inputs are unreliable."""
    values = (close, ema20, ema50, atr)
    if age_days > 5 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        return {"signal": "NO TRADE", "reason": "Missing, invalid or stale market data"}
    if atr / close > 0.12:
        return {"signal": "NO TRADE", "reason": "Excessive volatility"}
    if close > ema20 > ema50:
        stop = close - 1.5 * atr
        if stop <= 0:
            return {"signal": "NO TRADE", "reason": "Invalid stop-loss"}
        return {"signal": "BUY", "entry": round(close, 2), "stop": round(stop, 2), "target": round(close + 3 * atr, 2), "rr": 2.0, "reason": "Price above rising EMA20/EMA50 structure"}
    if close < ema20 < ema50:
        return {"signal": "SELL / EXIT WATCH", "reason": "Price below bearish EMA20/EMA50 structure; not a short-sale instruction"}
    return {"signal": "HOLD / WAIT", "reason": "Trend not confirmed"}
