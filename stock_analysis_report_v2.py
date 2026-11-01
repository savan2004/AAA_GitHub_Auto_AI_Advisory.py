"""Integrated evidence-based stock analysis; no invented prices or AI verdicts."""
from datetime import datetime, timezone
from html import escape
from math import isfinite
from stock_analysis_v2 import assess_stock

def numeric(x):
    try:
        y = float(x)
        return y if isfinite(y) else None
    except (TypeError, ValueError):
        return None

def technical_snapshot(df):
    if df is None or len(df) < 55 or "Close" not in df:
        return {"status": "Insufficient price history (55 sessions required)"}
    prices = [numeric(v) for v in df["Close"].tail(55)]
    if any(v is None or v <= 0 for v in prices):
        return {"status": "Invalid price history"}
    last = prices[-1]
    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    high20, low20 = max(prices[-20:]), min(prices[-20:])
    returns = [(b/a-1)*100 for a,b in zip(prices[-21:-1], prices[-20:])]
    daily_vol = (sum((x-sum(returns)/len(returns))**2 for x in returns)/len(returns))**0.5
    trend = "Bullish" if last > sma20 > sma50 else "Bearish" if last < sma20 < sma50 else "Mixed"
    return {"status":"OK","close":round(last,2),"sma20":round(sma20,2),
            "sma50":round(sma50,2),"support20":round(low20,2),
            "resistance20":round(high20,2),"daily_volatility_pct":round(daily_vol,2),
            "trend":trend}

def format_report(symbol, fundamentals, technical, observed_at=None):
    timestamp = observed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    f = fundamentals or {}
    metrics = {"roe":f.get("roe"), "debt_equity":f.get("de"),
               "revenue_growth":f.get("revenue_growth"),
               "earnings_growth":f.get("earnings_growth"),
               "operating_margin":f.get("operating_margin"),
               "free_cash_flow":f.get("free_cash_flow")}
    a = assess_stock(metrics)
    lines = [f"🔎 <b>{escape(str(symbol))} — STOCK ANALYSIS V2</b>",
             f"🕒 Generated: {escape(timestamp)}",
             "━━━━━━━━━━━━━━━━━━━━",
             f"🏦 Fundamental assessment: <b>{escape(a['verdict'])}</b>",
             f"📋 Data coverage: {a['coverage']}% (of six scoring indicators)",
             f"⭐ Score: {a['score']}/10" if a["score"] is not None else "⭐ Score: Withheld — insufficient data"]
    for strength in a["strengths"]:
        lines.append("✅ " + escape(strength))
    for risk in a["risks"]:
        lines.append("⚠️ " + escape(risk))
    lines += ["━━━━━━━━━━━━━━━━━━━━", "📈 <b>Technical snapshot</b>"]
    if technical.get("status") != "OK":
        lines.append("⚠️ " + escape(technical.get("status", "Data unavailable")))
    else:
        lines += [f"Trend: <b>{technical['trend']}</b>",
                  f"Last historical close: ₹{technical['close']:,.2f} (not live LTP)",
                  f"SMA20 / SMA50: ₹{technical['sma20']:,.2f} / ₹{technical['sma50']:,.2f}",
                  f"20-session low/high: ₹{technical['support20']:,.2f} / ₹{technical['resistance20']:,.2f}",
                  f"Daily historical volatility: {technical['daily_volatility_pct']:.2f}%"]
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "ℹ️ Fundamental figures may reflect different reporting periods; verify filings before trading.",
              "⚠️ Educational analysis, not a buy/sell recommendation. Not SEBI-registered advice."]
    return "\n".join(lines)
