"""Fail-closed MSK Analysis entry screen, not a ticker-name guessing tool."""
from datetime import datetime, timedelta, timezone
import html
import math
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


# NSE-listed BeES funds. Scheme reference: https://etf.nipponindiaim.com/Funds
BEES_ETFS = {
    "NIFTYBEES": "Nifty BeES",
    "BANKBEES": "Bank BeES",
    "JUNIORBEES": "Junior BeES",
    "GOLDBEES": "Gold BeES",
    "LIQUIDBEES": "Liquid BeES",
    "ITBEES": "IT BeES",
    "MID150BEES": "Midcap 150 BeES",
    "PHARMABEES": "Pharma BeES",
    "AUTOBEES": "Auto BeES",
    "INFRABEES": "Infra BeES",
}


def bees_etf_symbol(query):
    """Resolve unambiguous aliases for the supported NSE BeES funds."""
    text = str(query or "").strip().upper().removeprefix("NSE:").removesuffix(".NS")
    text = "".join(text.split()).replace("-", "")
    for prefix in ("NIPPONINDIAETF", "NIPPONINDIA"):
        if text.startswith(prefix):
            text = text.removeprefix(prefix)
            break
    text = text.removesuffix("ETF")
    aliases = {"".join(name.upper().split()): symbol for symbol, name in BEES_ETFS.items()}
    return text if text in BEES_ETFS else aliases.get(text)


def format_etf_profile(symbol):
    name = BEES_ETFS[symbol]
    benchmark = {"NIFTYBEES": "Nifty 50", "GOLDBEES": "Domestic price of gold"}.get(symbol)
    return (
        f"💼 <b>Nippon India ETF {name}</b> ({symbol})\n"
        "Instrument: NSE-listed exchange-traded fund.\n" +
        (f"Benchmark: {benchmark}.\n" if benchmark else "") +
        "The exchange price and the fund's NAV are separate figures.\n"
        'See the <a href="https://etf.nipponindiaim.com/Funds">Nippon India ETF fund directory</a> '
        "for the scheme benchmark, NAV, expense ratio and tracking disclosures."
    )


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
        if not c.apply(lambda series: series.map(lambda price: math.isfinite(price) and price > 0)).all().all():
            return None
        if ((c.High < c.Close) | (c.Low > c.Close)).any():
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
        first_session = c.index[0].date().isoformat()
        return {
            "ltp": round(last, 2), "chg": round(chg, 2), "ema20": round(ema20, 2),
            "ema50": round(ema50, 2), "rsi": round(rsi, 1), "trend": trend,
            "asof": c.index[-1].date().isoformat(), "bars": len(c),
            "first_session": first_session,
            "history_high": round(float(c.High.max()), 2),
            "history_low": round(float(c.Low.min()), 2),
        }
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError):
        return None


def format_snapshot(sym, cname, snapshot, *, is_etf=False):
    """Telegram HTML safe; no unverified current-price claim."""
    sym = html.escape(str(sym), quote=False)
    cname = html.escape(str(cname or sym), quote=False)
    x = snapshot
    color = "🟢" if x["chg"] >= 0 else "🔴"
    lines = [
        f"🔍 <b>{cname}</b> ({sym}) — ANALYSIS",
        f"✅ NSE-listed {'ETF' if is_etf else 'series'} | completed session {x['asof']} ({x['bars']} bars)",
        f"{color} Verified historical close ₹{x['ltp']:,.2f} ({x['chg']:+.2f}%)",
        f"📈 Trend <b>{x['trend']}</b> | RSI(14) {x['rsi']}",
        f"📐 EMA20 ₹{x['ema20']:,.2f} | EMA50 ₹{x['ema50']:,.2f}",
    ]
    if "first_session" in x:
        lines.append(f"📊 Observed daily range ₹{x['history_low']:,.2f}–₹{x['history_high']:,.2f} "
                     f"({x['first_session']} to {x['asof']})")
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              ("Choose Technical, ETF Profile or Chart below." if is_etf else
               "Choose Technical, Fundamental, Chart or Full PDF below."),
              "⚠️ Completed daily data, not a live price or trade recommendation."]
    return "\n".join(lines)
