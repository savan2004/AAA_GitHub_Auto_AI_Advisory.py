"""
stocks_buzz.py — 🔥 Stocks Buzz: Breakout Radar over the top-1000 NSE stocks
────────────────────────────────────────────────────────────────────────────────
What it does
  1. Loads the top-1000 universe (universe.py)
  2. Bulk-downloads 1y daily candles (market_data.py)
  3. Scores every stock for a *fresh, confirmed* breakout
  4. Returns the best breakouts + a "about to break" watchlist, each with a
     rule-based trade plan (entry / stop / T1 / T2)

What a breakout means here
  A daily CLOSE above the prior 20-day, 55-day or 52-week high, seen today or
  within the last 2 sessions (fresh), then graded on whether the move is
  *confirmed*:

    Breakout class        30  52W high > 55D high > 20D high   (× freshness)
    Volume surge          20  today's volume vs 20-day average
    Trend & momentum      20  EMA 20>50>200 stack, MACD, RSI sweet-spot
    Candle strength       10  close near the high of the day (no big wick)
    Base quality          10  Bollinger squeeze / tight 10-day range
    Relative strength     10  3-month return vs Nifty 50
    Delivery %            +5  high delivery = real buying (if NSE data present)
    Penalties            -x   overextended vs EMA20, RSI > 80, chasing > 3 ATR

  These weights are practical heuristics, NOT a backtested edge. The bot says so.

Liquidity gate: price ≥ ₹20 and 20-day average traded value ≥ ₹2 Cr, so the
list is tradeable and not dominated by illiquid operator-driven names.
"""

import time
import html
import logging
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

import pandas as pd

from market_data import bulk_history, index_history
from universe import get_universe
from technical_indicators import rsi_series, calc_atr

logger = logging.getLogger(__name__)

MIN_PRICE = 20.0
MIN_TRADED_VALUE = 2e7          # ₹2 Cr average daily traded value
MIN_BARS = 130
MAX_AGE = 2                     # breakout may be up to 2 sessions old
CACHE_TTL = 30 * 60

_state: Dict = {"result": None, "ts": 0.0}
_state_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════
# Core analysis (pure function — fully unit-testable)
# ══════════════════════════════════════════════════════════════════════════
def _find_breakout(h: pd.Series, c: pd.Series, v: pd.Series) -> Optional[dict]:
    """Strongest close-above-prior-high within the last MAX_AGE+1 sessions."""
    n = len(c)
    for k in range(MAX_AGE + 1):
        close_k = float(c.iloc[-(1 + k)])
        for lookback, label in ((252, "52W High"), (55, "55-Day High"), (20, "20-Day High")):
            lb = min(lookback, n - 2 - k)
            if lb < 20 or (label == "52W High" and lb < 200):
                continue
            level = float(h.iloc[-(lb + 1 + k):-(1 + k)].max())
            if close_k > level:
                vbase = float(v.iloc[-(21 + k):-(1 + k)].mean())
                vol_ratio = float(v.iloc[-(1 + k)]) / vbase if vbase > 0 else 0.0
                return {"label": label, "level": level, "age": k, "vol_ratio": vol_ratio}
    return None


def _nearest_overhead_level(h: pd.Series, ltp: float, max_dist_pct: float = 2.0) -> Optional[dict]:
    """For the watchlist: strongest prior-high level sitting 0–2% above price."""
    n = len(h)
    best = None
    for lookback, label in ((252, "52W High"), (55, "55-Day High"), (20, "20-Day High")):
        lb = min(lookback, n - 1)
        if lb < 20 or (label == "52W High" and lb < 200):
            continue
        level = float(h.iloc[-(lb + 1):-1].max())
        dist = (level / ltp - 1) * 100
        if 0 < dist <= max_dist_pct:
            best = {"label": label, "level": level, "dist": dist}
            break                       # first (strongest) class wins
    return best


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def analyze_stock(sym: str, df: pd.DataFrame, nifty_close: Optional[pd.Series] = None,
                  delivery_pct: Optional[float] = None) -> Optional[dict]:
    """
    Returns None if the stock fails the data/liquidity gate, otherwise a dict
    with kind in {"BREAKOUT", "WATCH", "EXTENDED", "NONE"} plus all the numbers.
    """
    if df is None or len(df) < MIN_BARS:
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close", "High", "Low"])
    if len(df) < MIN_BARS:
        return None

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ltp, prev = float(c.iloc[-1]), float(c.iloc[-2])
    if ltp < MIN_PRICE or prev <= 0:
        return None

    try:                                         # stale / suspended stock
        if (pd.Timestamp.now() - pd.Timestamp(df.index[-1])).days > 7:
            return None
    except Exception:
        pass

    traded_value = float((c.iloc[-21:-1] * v.iloc[-21:-1]).mean())
    if not traded_value >= MIN_TRADED_VALUE:
        return None

    chg = (ltp / prev - 1) * 100
    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1]) if len(c) >= 200 else None
    rsi = float(rsi_series(c).iloc[-1])
    ml = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_hist = float((ml - ml.ewm(span=9, adjust=False).mean()).iloc[-1])
    atr = float(calc_atr(df))
    if not atr > 0:
        return None

    # relative strength vs Nifty over 63 sessions (percentage points)
    rs3m = None
    if len(c) > 64:
        r_s = ltp / float(c.iloc[-64]) - 1
        if nifty_close is not None and len(nifty_close) > 64:
            r_n = float(nifty_close.iloc[-1]) / float(nifty_close.iloc[-64]) - 1
            rs3m = (r_s - r_n) * 100
        else:
            rs3m = r_s * 100            # no benchmark → absolute 3M return

    # Bollinger bandwidth squeeze + tight base (measured just before the move)
    bw = (4 * c.rolling(20).std()) / c.rolling(20).mean()
    brk = _find_breakout(h, c, v)
    age = brk["age"] if brk else 0
    ref_i = -(age + 2)
    bw_hist = bw.iloc[-(120 + age + 1):-(age + 1)].dropna()
    squeeze = bool(len(bw_hist) > 30 and pd.notna(bw.iloc[ref_i])
                   and (bw_hist <= bw.iloc[ref_i]).mean() <= 0.25)
    rng10 = (float(h.iloc[-(11 + age):-(1 + age)].max()) - float(l.iloc[-(11 + age):-(1 + age)].min()))
    tight = bool(float(c.iloc[ref_i]) > 0 and rng10 / float(c.iloc[ref_i]) <= 0.08)

    ext_pct = (ltp / ema20 - 1) * 100
    stack = ema20 > ema50 and ltp > ema20 and (ema200 is None or ema50 > ema200)
    base = {
        "sym": sym, "ltp": ltp, "chg": chg, "rsi": rsi, "rs3m": rs3m,
        "squeeze": squeeze, "tight": tight, "ext_pct": ext_pct, "atr": atr,
        "atr_pct": atr / ltp * 100, "tv_cr": traded_value / 1e7,
        "delivery": delivery_pct, "kind": "NONE", "score": 0.0,
        "reasons": [], "flags": [],
    }

    # ───────────────────────── confirmed breakout ─────────────────────────
    if brk and ltp > ema50:
        k = brk["age"]
        hi_k, lo_k, cl_k = float(h.iloc[-(1 + k)]), float(l.iloc[-(1 + k)]), float(c.iloc[-(1 + k)])
        clv = (cl_k - lo_k) / (hi_k - lo_k) if hi_k > lo_k else 0.5

        cls_pts = {"52W High": 30, "55-Day High": 24, "20-Day High": 16}[brk["label"]]
        age_mult = {0: 1.0, 1: 0.7, 2: 0.4}[k]
        s_break = cls_pts * age_mult
        s_vol = _clamp((brk["vol_ratio"] - 1.0) / 2.0 * 20, 0, 20)
        s_trend = (8 if stack else (4 if ltp > ema20 > ema50 else 0))
        s_trend += 4 if (ema200 is not None and ltp > ema200 and ema50 > ema200) else 0
        s_trend += 4 if macd_hist > 0 else 0
        s_trend += 4 if 55 <= rsi <= 75 else (2 if 50 <= rsi < 55 else 0)
        s_candle = 10 if clv >= 0.8 else 6 if clv >= 0.6 else 3 if clv >= 0.5 else 0
        s_base = (6 if squeeze else 0) + (4 if tight else 0)
        s_rs = _clamp((rs3m or 0) / 2.0, -5, 10)
        s_del = 0.0
        if delivery_pct is not None:
            s_del = 5 if delivery_pct >= 60 else 3 if delivery_pct >= 45 else 0

        penalty = 0.0
        flags = []
        if ext_pct > 12:
            penalty += 8
            flags.append(f"extended {ext_pct:.0f}% above EMA20")
        elif ext_pct > 8:
            penalty += 4
            flags.append(f"stretched {ext_pct:.0f}% above EMA20")
        if rsi > 80:
            penalty += 5
            flags.append(f"RSI {rsi:.0f} very hot")
        if (ltp - brk["level"]) / atr > 3:
            penalty += 5
            flags.append("price already >3 ATR past the breakout level")
        if brk["vol_ratio"] < 1.2:
            flags.append("weak volume confirmation")
        if clv < 0.5:
            flags.append("weak close (long upper wick)")

        score = _clamp(s_break + s_vol + s_trend + s_candle + s_base + s_rs + s_del - penalty, 0, 100)

        reasons = [f"closed above {brk['label']} ₹{brk['level']:,.2f}"
                   + (" today" if k == 0 else f" ({k}d ago)")]
        reasons.append(f"volume {brk['vol_ratio']:.1f}× 20D avg")
        if squeeze:
            reasons.append("Bollinger squeeze before the move")
        elif tight:
            reasons.append("tight 10-day base")
        if clv >= 0.8:
            reasons.append("closed near the day's high")
        if stack:
            reasons.append("EMA 20>50>200 aligned" if ema200 is not None else "EMA 20>50 aligned")
        if rs3m is not None and rs3m >= 5:
            reasons.append(f"beating Nifty by {rs3m:.0f} pts (3M)")
        if delivery_pct is not None and delivery_pct >= 60:
            reasons.append(f"delivery {delivery_pct:.0f}%")

        # Blow-off moves are not "picks": too far from the base to define a sane stop.
        too_hot = ext_pct > 15 or rsi > 85
        if too_hot and brk["vol_ratio"] >= 1.2:
            base.update({"kind": "EXTENDED", "label": brk["label"], "score": round(score, 1)})
            return base
        passes = (brk["vol_ratio"] >= 1.2 and score >= 50)
        if passes:
            swing_low = float(l.iloc[-5:].min())
            sl = max(ltp - 2.2 * atr, min(swing_low, brk["level"]) - 0.25 * atr)
            if sl >= ltp - 0.5 * atr:
                sl = ltp - 1.0 * atr
            risk = ltp - sl
            base.update({
                "kind": "BREAKOUT", "label": brk["label"], "level": brk["level"],
                "age": k, "vol_ratio": brk["vol_ratio"], "clv": clv,
                "score": round(score, 1), "reasons": reasons, "flags": flags,
                "entry": ltp, "sl": sl, "risk_pct": risk / ltp * 100,
                "t1": ltp + 1.5 * risk, "t2": ltp + 3.0 * risk,
            })
        return base

    # ─────────────────────────── near-breakout watch ──────────────────────
    near = _nearest_overhead_level(h, ltp)
    if near and ltp > ema50 and ema20 > ema50 and 48 <= rsi <= 72 and ext_pct < 8:
        vol5 = float(v.iloc[-5:].mean())
        vol20 = float(v.iloc[-21:-1].mean())
        dryup = vol20 > 0 and vol5 / vol20 < 0.9
        w = (35 * (1 - near["dist"] / 2.0)
             + (20 if squeeze else 0) + (15 if tight else 0)
             + (15 if ltp > ema20 else 0)
             + 15 * _clamp((rs3m or 0) / 15.0, 0, 1))
        trigger = near["level"] * 1.002
        swing_low = float(l.iloc[-5:].min())
        sl = max(trigger - 2.2 * atr, swing_low - 0.25 * atr)
        if sl >= trigger - 0.5 * atr:
            sl = trigger - 1.0 * atr
        risk = trigger - sl
        reasons = [f"{near['dist']:.1f}% below {near['label']} ₹{near['level']:,.2f}"]
        if squeeze:
            reasons.append("Bollinger squeeze (coiling)")
        elif tight:
            reasons.append("tight 10-day base")
        if dryup:
            reasons.append("volume drying up (classic pre-breakout)")
        if stack:
            reasons.append("trend stack intact")
        base.update({
            "kind": "WATCH", "label": near["label"], "level": near["level"],
            "dist": near["dist"], "score": round(_clamp(w, 0, 100), 1),
            "reasons": reasons, "entry": trigger, "sl": sl,
            "risk_pct": risk / trigger * 100,
            "t1": trigger + 1.5 * risk, "t2": trigger + 3.0 * risk,
        })
    return base


# ══════════════════════════════════════════════════════════════════════════
# Market regime — do breakouts have a tailwind?
# ══════════════════════════════════════════════════════════════════════════
def market_regime(nifty_df: Optional[pd.DataFrame]) -> dict:
    if nifty_df is None or len(nifty_df) < 60:
        return {"text": "Nifty trend unavailable", "supportive": None}
    c = nifty_df["Close"]
    ltp = float(c.iloc[-1])
    e50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    e200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1]) if len(c) >= 200 else None
    r1m = (ltp / float(c.iloc[-22]) - 1) * 100 if len(c) > 22 else 0.0
    if ltp > e50 and (e200 is None or ltp > e200):
        txt, sup = f"Nifty {ltp:,.0f} above 50 & 200 EMA ({r1m:+.1f}% 1M) — breakouts have a tailwind", True
    elif ltp > e50:
        txt, sup = f"Nifty {ltp:,.0f} above 50 EMA but below 200 EMA ({r1m:+.1f}% 1M) — selective", None
    else:
        txt, sup = f"Nifty {ltp:,.0f} below 50 EMA ({r1m:+.1f}% 1M) — breakouts fail more often, size down", False
    return {"text": txt, "supportive": sup}


# ══════════════════════════════════════════════════════════════════════════
# Scan orchestration
# ══════════════════════════════════════════════════════════════════════════
def get_cached() -> Optional[dict]:
    with _state_lock:
        if _state["result"] and time.time() - _state["ts"] < CACHE_TTL:
            return _state["result"]
    return None


def run_scan(progress_cb: Optional[Callable[[int, int], None]] = None) -> dict:
    t0 = time.time()
    uni = get_universe()
    symbols: List[str] = uni["symbols"]
    delivery: Dict[str, float] = uni.get("delivery", {}) or {}

    hist = bulk_history(symbols, "1y", chunk_size=100, progress_cb=progress_cb)
    nifty = index_history("^NSEI", "1y")
    nifty_close = nifty["Close"] if nifty is not None and not nifty.empty else None

    breakouts, watch, liquid, extended = [], [], 0, 0
    for sym, df in hist.items():
        try:
            r = analyze_stock(sym, df, nifty_close, delivery.get(sym))
        except Exception as e:
            logger.debug(f"[buzz] analyze {sym} failed: {e}")
            continue
        if r is None:
            continue
        liquid += 1
        if r["kind"] == "BREAKOUT":
            breakouts.append(r)
        elif r["kind"] == "WATCH":
            watch.append(r)
        elif r["kind"] == "EXTENDED":
            extended += 1

    breakouts.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    watch.sort(key=lambda x: x["score"], reverse=True)

    result = {
        "ts": datetime.now(), "elapsed": time.time() - t0,
        "universe_size": len(symbols), "downloaded": len(hist), "liquid": liquid,
        "source": uni.get("source", "?"), "degraded": uni.get("degraded", False),
        "breakouts": breakouts, "watch": watch, "extended": extended,
        "regime": market_regime(nifty),
    }
    with _state_lock:
        _state["result"] = result
        _state["ts"] = time.time()
    logger.info(f"[buzz] scan done in {result['elapsed']:.0f}s: {len(hist)}/{len(symbols)} "
                f"downloaded, {liquid} liquid, {len(breakouts)} breakouts, {len(watch)} watch")
    return result


# ══════════════════════════════════════════════════════════════════════════
# Telegram formatting
# ══════════════════════════════════════════════════════════════════════════
def _e(s) -> str:
    return html.escape(str(s), quote=False)


def _stars(score: float) -> str:
    return "🔥🔥🔥" if score >= 80 else "🔥🔥" if score >= 65 else "🔥"


def _breakout_card(i: int, r: dict) -> str:
    icon = "🟢" if r["chg"] >= 0 else "🔴"
    lines = [
        f"<b>{i}. {_e(r['sym'])}</b>  ₹{r['ltp']:,.2f} {icon}{r['chg']:+.2f}%  "
        f"{_stars(r['score'])} <b>{r['score']:.0f}</b>/100",
        f"   📌 " + " · ".join(r["reasons"][:4]),
        f"   RSI {r['rsi']:.0f}  |  ATR {r['atr_pct']:.1f}%  |  Turnover ₹{r['tv_cr']:.0f} Cr/day",
        f"   🎯 Entry ₹{r['entry']:,.2f}  SL ₹{r['sl']:,.2f} (-{r['risk_pct']:.1f}%)",
        f"       T1 ₹{r['t1']:,.2f}  ·  T2 ₹{r['t2']:,.2f}",
    ]
    if r["flags"]:
        lines.append("   ⚠️ " + "; ".join(r["flags"][:2]))
    return "\n".join(lines)


def _watch_card(i: int, r: dict) -> str:
    return (
        f"<b>{i}. {_e(r['sym'])}</b>  ₹{r['ltp']:,.2f}  ({r['chg']:+.2f}%)  score {r['score']:.0f}\n"
        f"   👀 " + " · ".join(r["reasons"][:3]) + "\n"
        f"   Trigger: close above ₹{r['entry']:,.2f}  |  SL ₹{r['sl']:,.2f}  |  T1 ₹{r['t1']:,.2f}"
    )


def top_symbols(result: dict, n: int = 5) -> List[str]:
    return [r["sym"] for r in result.get("breakouts", [])[:n]]


def format_buzz(result: dict, top_n: int = 10, watch_n: int = 5) -> List[str]:
    """Returns a list of Telegram-HTML messages, each safely under the 4096 limit."""
    sep = "━━━━━━━━━━━━━━━━━━━━"
    ts = result["ts"].strftime("%d-%b-%Y %H:%M")
    head = [
        "🔥 <b>STOCKS BUZZ — Breakout Radar</b>",
        f"📅 {ts}",
        sep,
        f"🔎 Scanned <b>{result['downloaded']}</b> of {result['universe_size']} stocks "
        f"→ <b>{result['liquid']}</b> passed liquidity filter",
        f"🚀 <b>{len(result['breakouts'])}</b> confirmed breakouts · "
        f"👀 {len(result['watch'])} near-breakout"
        + (f" · 🚫 {result['extended']} extended (skipped — don't chase)" if result.get("extended") else ""),
        f"🧭 {_e(result['regime']['text'])}",
    ]
    if result.get("degraded"):
        head.append(f"⚠️ Universe degraded: {_e(result['source'])}. "
                    f"Add a full list in <code>universe_override.txt</code> for complete coverage.")
    head.append(sep)

    blocks: List[str] = ["\n".join(head)]
    br = result["breakouts"][:top_n]
    if br:
        blocks.append("🚀 <b>TOP BREAKOUTS</b> (ranked by score)")
        blocks += [_breakout_card(i, r) for i, r in enumerate(br, 1)]
    else:
        blocks.append("😴 No stock cleared the breakout + volume + trend bar right now. "
                      "That is a valid answer — forcing picks in a quiet tape loses money.")

    wl = result["watch"][:watch_n]
    if wl:
        blocks.append(sep + "\n👀 <b>WATCHLIST — coiling just under resistance</b>")
        blocks += [_watch_card(i, r) for i, r in enumerate(wl, 1)]

    blocks.append(
        sep + "\n"
        "ℹ️ <i>Scores are rule-based heuristics (not backtested). Volume during market hours is "
        "partial — re-run after 3:30 PM for confirmed closes. Use the stop-loss; breakouts fail "
        "often.</i>\n⚠️ <i>Educational only. Not SEBI-registered advice.</i>"
    )

    # pack blocks into messages ≤ 3800 chars
    msgs, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > 3800:
            msgs.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        msgs.append(cur)
    return msgs
