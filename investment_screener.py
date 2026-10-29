"""Evidence-gated investment ideas for Moderate and Aggressive MSK buttons.

Only completed NSE daily candles. Heuristic scores are NOT calibrated returns,
profit probabilities, or verified suitability recommendations.
"""
from datetime import date, datetime, timedelta, timezone
import math
import html

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
PROFILE = {
    "moderate": dict(min_bars=105, min_price=50, min_turnover=5e7,
                     min_rsi=53, max_rsi=72, min_adx=17, max_atr_pct=5.5,
                     min_20d=1, max_20d=22, risk_cap_pct=9, alloc_cap=15,
                     target_r=1.6, horizon="2–6 months", title="⚖️ MODERATE"),
    "aggressive": dict(min_bars=105, min_price=20, min_turnover=3e7,
                       min_rsi=55, max_rsi=79, min_adx=17, max_atr_pct=9,
                       min_20d=2, max_20d=40, risk_cap_pct=13, alloc_cap=12,
                       target_r=1.8, horizon="2–12 weeks", title="🚀 AGGRESSIVE"),
}


def _last_complete_day(asof):
    # At market open and before the provider's 16:00 EOD publication buffer,
    # the current daily bar can still be forming and must not be scored.
    if isinstance(asof, datetime):
        d = asof.astimezone(IST) if asof.tzinfo else asof.replace(tzinfo=IST)
        today = d.date()
        if d.hour < 16:
            today -= timedelta(days=1)
    else:
        today = asof if isinstance(asof, date) else datetime.now(IST).date()
        # Caller passes an IST calendar date; conservatively exclude today.
        today -= timedelta(days=1)
    while today.weekday() >= 5:
        today -= timedelta(days=1)
    return today


def _clean(df, asof, required):
    if df is None or df.empty or not {"Close", "High", "Low", "Volume"}.issubset(df.columns):
        return None
    try:
        frame = df[["Close", "High", "Low", "Volume"]].copy()
        index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
        if index.isna().any():
            return None
        if index.tz is not None:
            index = index.tz_convert(IST).tz_localize(None)
        frame.index = index
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
        frame = frame.loc[frame.index.date <= _last_complete_day(asof)]
        if len(frame) < required:
            return None
        frame = frame.apply(pd.to_numeric, errors="coerce")
        if not frame.tail(required).notna().all().all():
            return None
        if (frame.Close.tail(required) <= 0).any():
            return None
        if (frame.High.tail(required) < frame.Low.tail(required)).any():
            return None
        if (frame.Volume.tail(30) <= 0).any():
            return None
        if (_last_complete_day(asof) - frame.index[-1].date()).days > 7:
            return None
        return frame
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def evaluate_candidate(sym, df, profile, asof=None):
    """Return a bullish, liquid, risk-bounded candidate or None. No placeholders."""
    p = PROFILE.get(profile)
    if p is None:
        return None
    asof = asof or datetime.now(IST)
    f = _clean(df, asof, p["min_bars"])
    if f is None:
        return None
    try:
        c, h, l, v = (f[x] for x in ("Close", "High", "Low", "Volume"))
        price = float(c.iloc[-1])
        ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
        ema100 = float(c.ewm(span=100, adjust=False).mean().iloc[-1])
        change20 = (price / float(c.iloc[-21]) - 1) * 100
        change1 = (price / float(c.iloc[-2]) - 1) * 100
        dv = c.diff()
        gain = dv.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        loss = (-dv.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        avg_gain, avg_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
        rsi = 100 if avg_loss == 0 and avg_gain > 0 else (
            50 if avg_gain == 0 and avg_loss == 0 else 100 - 100 / (1 + avg_gain / max(avg_loss, 1e-12))
        )
        prev = c.shift(1)
        tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1])
        up, down = h.diff(), -l.diff()
        plus_dm = up.where((up > down) & (up > 0), 0)
        minus_dm = down.where((down > up) & (down > 0), 0)
        smooth = lambda a: a.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        smtr = smooth(tr).replace(0, float("nan"))
        dip = 100 * smooth(plus_dm) / smtr
        dim = 100 * smooth(minus_dm) / smtr
        dx = (100 * (dip - dim).abs() / (dip + dim).replace(0, float("nan")))
        adx = float(smooth(dx).iloc[-1])
        volx = float(v.iloc[-1] / v.iloc[-21:-1].mean())
        liquidity = float((c * v).tail(20).median())
        atrpct = 100 * atr / price
        is_breakout = price > float(h.iloc[-21:-1].max())
        measurements = [price, ema20, ema50, ema100, change20, change1, rsi,
                        atr, adx, volx, liquidity, atrpct]
        if not all(math.isfinite(n) for n in measurements) or price < p["min_price"]:
            return None
        if liquidity < p["min_turnover"] or not p["min_20d"] <= change20 <= p["max_20d"]:
            return None
        if not p["min_rsi"] <= rsi <= p["max_rsi"] or not p["min_adx"] <= adx:
            return None
        if not 0 < atrpct <= p["max_atr_pct"]:
            return None
        if profile == "moderate":
            if not (price > ema20 > ema50 > ema100) or volx < 0.7:
                return None
        else:
            if not (price > ema20 > ema50) or (volx < 1.0 and not is_breakout):
                return None

        # Use recent 20-session support, not arbitrary 'stop below entry'.
        support = float(l.tail(20).min())
        risk = max(atr * (1.6 if profile == "moderate" else 1.8),
                   price - support + 0.15 * atr)
        if not (0 < risk / price * 100 <= p["risk_cap_pct"]):
            return None
        stop = price - risk
        if stop <= 0:
            return None
        score = round(min(100, max(0,
            25 + min(20, max(0, change20 * 1.2))
            + min(18, max(0, (rsi - 50) * 1.1))
            + min(15, max(0, (adx - 15) * 0.8))
            + min(10, max(0, (volx - 0.7) * 10))
            + (12 if is_breakout else 5) - max(0, atrpct - 3) * 2
        )))
        return {
            "sym": sym, "ltp": round(price, 2), "chg": round(change1, 2),
            "score": score, "rsi": round(rsi, 1), "adx": round(adx, 1),
            "volx": round(volx, 2), "atr_pct": round(atrpct, 2),
            "entry": round(price, 2), "sl": round(stop, 2),
            "t1": round(price + p["target_r"] * risk, 2),
            "t2": round(price + (2.6 if profile == "moderate" else 3.2) * risk, 2),
            "rr1": p["target_r"], "is_breakout": is_breakout,
            "asof": f.index[-1].date().isoformat(),
        }
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError):
        return None


def format_shortlist(profile, candidates, completed, total, unfinished=0):
    """Use honest partial coverage and never fill the list with bearish stocks."""
    p = PROFILE[profile]
    sorted_rows = sorted(candidates, key=lambda x: (-x["score"], -x["volx"], x["sym"]))[:10]
    per_pick = min(100 / len(sorted_rows), p["alloc_cap"]) if sorted_rows else 0
    pct = round(per_pick, 1)
    cash = max(0, round(100 - len(sorted_rows) * pct, 1))
    lines = [f"📊 <b>{p['title']} | VERIFIED BULLISH WATCHLIST</b>",
             f"🗓 Last completed candles only | {p['horizon']}",
             f"✅ {len(sorted_rows)}/10 qualified | checks finished: {completed}/{total}",
             "Scores = technical strength /100, NOT win probability.",
             "━━━━━━━━━━━━━━━━━━━━"]
    if not sorted_rows:
        lines.append("⚪ No candidate met all trend, momentum, volume, liquidity and risk gates. NO BUY SIGNAL.")
    for i, row in enumerate(sorted_rows, 1):
        sym = html.escape(row["sym"], quote=False)
        lines += [
            f"🟢 <b>{i}. {sym}</b> | Technical score {row['score']}/100",
            f"₹{row['ltp']:,.2f} ({row['chg']:+.2f}%) · RSI {row['rsi']} · ADX {row['adx']}"
            f" · Vol {row['volx']:.1f}×",
            f"Entry (EOD reference) ₹{row['entry']:,.2f} | SL ₹{row['sl']:,.2f}",
            f"T1 ₹{row['t1']:,.2f} (1:{row['rr1']:.1f}) | T2 ₹{row['t2']:,.2f}",
            f"Allocation cap {pct:.1f}% | Candle {row['asof']}",
            "···",
        ]
    lines.append(f"💼 Illustrative maximum invested {100-cash:.1f}% | Unallocated cash ≥{cash:.1f}%.")
    if len(sorted_rows) < 10:
        lines.append("ℹ️ No forced recommendations. Bearish or incomplete stocks excluded.")
    if unfinished or completed < total:
        lines.append(f"⚠️ Partial coverage: {completed}/{total} data checks finished; {unfinished} timed out.")
    lines += ["⚠️ End-of-day rule-based screening, not intraday prices, suitability assessment or",
              "verified fundamentals. Validate financials/valuation and source freshness before investing.",
              "Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)
