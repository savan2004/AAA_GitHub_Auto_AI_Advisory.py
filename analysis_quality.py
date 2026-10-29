"""Fail-closed MSK Analysis entry screen, not a ticker-name guessing tool."""
from datetime import datetime, timedelta, timezone
import html
import math
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


def inspect_history(df, now=None):
    """Return a verified-completed-candle snapshot or None (never fake a find)."""
    if df is None or df.empty or not {"Close", "High", "Low"}.issubset(df.columns):
        return None
    now = now or datetime.now(IST)
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    try:
        c = df[["Close", "High", "Low"]].copy()
        index = pd.DatetimeIndex(pd.to_datetime(c.index, errors="coerce"))
        if index.isna().any():
            return None
        if index.tz is not None:
            index = index.tz_convert(IST).tz_localize(None)
        c.index = index
        c = c.loc[~c.index.duplicated(keep="last")].sort_index()
        today = now.date()
        # Ignore unfinished daily bars prior to exchange EOD publication buffer.
        if now.hour < 16:
            today -= timedelta(days=1)
        c = c.loc[c.index.date <= today]
        if len(c) < 55 or (now.date() - c.index[-1].date()).days > 7:
            return None
        c = c.apply(pd.to_numeric, errors="coerce")
        if not c.tail(55).notna().all().all():
            return None
        if (c.Close.tail(55) <= 0).any():
            return None
        if (c.High.tail(55) < c.Low.tail(55)).any():
            return None
        last, previous = float(c.Close.iloc[-1]), float(c.Close.iloc[-2])
        ema20 = float(c.Close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(c.Close.ewm(span=50, adjust=False).mean().iloc[-1])
        chg = (last / previous - 1) * 100
        trend = ("BULLISH" if last > ema20 > ema50 else
                 "BEARISH" if last < ema20 < ema50 else "NEUTRAL")
        delta = c.Close.diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        down = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        a, b = float(up.iloc[-1]), float(down.iloc[-1])
        rsi = 100.0 if b == 0 and a > 0 else (
            50.0 if a == 0 and b == 0 else 100 - 100 / (1 + a / max(b, 1e-12)))
        vals = [last, previous, ema20, ema50, chg, rsi]
        if not all(math.isfinite(v) for v in vals):
            return None
        # 52-week high/low is never shown from only six months' candles.
        has_year = len(c) >= 235 and (c.index[-1] - c.index[-235]).days >= 310
        return {
            "ltp": round(last, 2), "chg": round(chg, 2), "ema20": round(ema20, 2),
            "ema50": round(ema50, 2), "rsi": round(rsi, 1), "trend": trend,
            "asof": c.index[-1].date().isoformat(), "bars": len(c),
            "high52": round(float(c.High.tail(252).max()), 2) if has_year else None,
            "low52": round(float(c.Low.tail(252).min()), 2) if has_year else None,
        }
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError):
        return None


def format_snapshot(sym, cname, snapshot):
    """Telegram HTML safe; no unverified current-price claim."""
    sym = html.escape(str(sym), quote=False)
    cname = html.escape(str(cname or sym), quote=False)
    x = snapshot
    color = "🟢" if x["chg"] >= 0 else "🔴"
    lines = [
        f"🔍 <b>{cname}</b> ({sym}) — ANALYSIS",
        f"✅ NSE-listed series | completed session {x['asof']} ({x['bars']} bars)",
        f"{color} Verified historical close ₹{x['ltp']:,.2f} ({x['chg']:+.2f}%)",
        f"📈 Trend <b>{x['trend']}</b> | RSI(14) {x['rsi']}",
        f"📐 EMA20 ₹{x['ema20']:,.2f} | EMA50 ₹{x['ema50']:,.2f}",
    ]
    if x["high52"] is not None:
        lines.append(f"📊 52W range ₹{x['low52']:,.2f}–₹{x['high52']:,.2f}")
    else:
        lines.append("📊 52W high/low withheld: insufficient full-year history")
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "Choose Technical, Fundamental, Chart or Full PDF below.",
              "⚠️ Completed daily data, not a live price or trade recommendation."]
    return "\n".join(lines)
