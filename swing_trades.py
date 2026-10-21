"""
swing_trades.py — Swing Trade Scanner v7
────────────────────────────────────────────────────────────────────────────
What changed vs v6 (accuracy)
  • Score is /10 with NON-overlapping factors (v6 double-counted trend, MACD,
    RSI, supertrend and "near highs", and its "/13" label was wrong: max was 14).
  • Every candidate is tagged Breakout / Pullback (or Breakdown / Rally-fade);
    v6 rewarded "near 20d high" and "lower-mid Bollinger" at the same time.
  • Hard gates (not points): liquidity, fresh data, not extended from EMA20,
    no RSI extreme, weekly trend must not oppose, R:R(T1) >= 1.2.
  • Volume must confirm DIRECTION (up-day volume for longs, down-day for shorts).
  • Relative strength vs Nifty (60d) and a Nifty regime filter.
  • Weekly trend is derived from daily bars (no look-ahead, zero API calls).
  • Stop-loss is structure-based (recent swing low/high, clamped 1.0–2.0 ATR).
  • Wilder RSI / ATR / ADX; correct Supertrend dropped (it duplicated EMA/MACD).
  • Built-in walk-forward backtest of the SAME rules on the same download, so
    each scan shows how this exact rule performed on the last ~5 months.

What changed (API cost)
  v6: ~60 get_hist (throttled 8/min → ~8 min) + up to 60 weekly downloads
      + watch-list re-fetch.  v7: ONE bulk_history call (<=100 symbols) + ONE
      Nifty call, cached 30 min and shared by both swing buttons.
"""
import html
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from data_engine import cached_get, cached_set
from market_data import bulk_history, index_history

# ── CANDIDATE UNIVERSE (sector round-robin, up to 100 = one bulk chunk) ──────
CAND_LIMIT = 100
try:
    from nifty500_collector import SECTOR_STOCKS as _SC
    _seen = set()
    CANDIDATES = []
    CANDIDATE_SECTORS = {}
    _sector_queues = {sector: list(syms) for sector, syms in _SC.items()}
    while len(CANDIDATES) < CAND_LIMIT and any(_sector_queues.values()):
        for _sector, _queue in _sector_queues.items():
            if len(CANDIDATES) >= CAND_LIMIT:
                break
            while _queue:
                _s = _queue.pop(0)
                if _s not in _seen:
                    CANDIDATES.append(f"{_s}.NS")
                    CANDIDATE_SECTORS[f"{_s}.NS"] = _sector
                    _seen.add(_s)
                    break
    logger.info(f"Swing v7: {len(CANDIDATES)} candidates, {len(set(CANDIDATE_SECTORS.values()))} sectors")
except Exception as _e:
    logger.warning(f"Swing: nifty500_collector unavailable: {_e}")
    CANDIDATES = [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS",
        "WIPRO.NS", "HCLTECH.NS", "ASIANPAINT.NS", "MARUTI.NS", "TATAPOWER.NS",
        "TITAN.NS", "SUNPHARMA.NS", "ONGC.NS", "NTPC.NS", "M&M.NS",
        "BAJFINANCE.NS", "AXISBANK.NS", "TECHM.NS", "DRREDDY.NS", "DIVISLAB.NS",
        "HINDALCO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "BPCL.NS", "EICHERMOT.NS",
    ]
    CANDIDATE_SECTORS = {}

# ── TUNABLES ─────────────────────────────────────────────────────────────────
THRESHOLDS = {                       # minimum score /10
    "conservative": {"LONG": 7, "SHORT": 8},
    "aggressive":   {"LONG": 6, "SHORT": 7},
}
MAX_LONG, MAX_SHORT, MAX_PER_SECTOR = 4, 2, 2
HOLD_BARS = 10          # max holding period, also used by the backtest
MIN_TURNOVER = 5e7      # ₹5 Cr average daily traded value (20d)
MIN_RR1 = 1.2           # reject setups whose T1 reward < 1.2 × risk
CACHE_KEY, CACHE_TTL = "swing_v7_calc", 1800
_LOCK = threading.Lock()


UNIVERSE_LIMIT = 200    # liquid universe = 2 bulk chunks (only used when NSE turnover ranking is available)


# ── ACCURACY GUARDS ──────────────────────────────────────────────────────────
def _now_ist():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _session_open(now=None):
    """True 09:15-15:45 IST on weekdays: today's daily candle is still forming (partial price AND volume)."""
    n = now or _now_ist()
    m = n.hour * 60 + n.minute
    return n.weekday() < 5 and 9 * 60 + 15 <= m < 15 * 60 + 45


def _drop_forming(d, now=None):
    """Drop today's incomplete candle so LIVE signals use the same completed bars the backtest uses.
    (A half-day volume bar can never confirm a breakout, and its 'close' is just the live price.)"""
    n = now or _now_ist()
    try:
        if len(d) and _session_open(n) and d.index[-1].date() == n.date():
            return d.iloc[:-1]
    except Exception:
        pass
    return d


def _ckey():
    return f"{CACHE_KEY}_{'live' if _session_open() else 'final'}"     # never serve pre-close data after close


def _sector_of(sym):
    s = CANDIDATE_SECTORS.get(f"{sym}.NS")
    if not s:
        try:
            from nifty500_collector import get_stock_sector
            s = get_stock_sector(sym)
        except Exception:
            s = ""
    s = (s or "").strip()
    return sym if s in ("", "Others", "Other") else s                  # unknown sector -> own bucket (no false cap)


def _scan_universe():
    """Top-liquidity stocks (NSE bhavcopy ranking) when available, else the built-in sector round-robin list."""
    try:
        from universe import get_universe
        u = get_universe()
        if str(u.get("source", "")).startswith("NSE bhavcopy") and len(u.get("symbols") or []) >= 100:
            return list(u["symbols"])[:UNIVERSE_LIMIT]
    except Exception as e:
        logger.info(f"[Swing] universe unavailable, using built-in list: {e}")
    return [s[:-3] if s.endswith(".NS") else s for s in CANDIDATES]


# ── INDICATORS (one pass per symbol) ─────────────────────────────────────────
def _wilder(s, n=14):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _naive(idx):
    idx = pd.DatetimeIndex(idx)
    try:
        return idx.tz_localize(None)
    except (TypeError, AttributeError):
        return idx.tz_convert(None)


def _prep(df, nifty_close):
    """OHLCV DataFrame -> dict of numpy arrays, or (None, reason)."""
    try:
        d = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    except Exception:
        return None, "bad columns"
    d.index = _naive(d.index)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d[(d.Close > 0) & (d.High >= d.Low) & (d.Volume >= 0)]
    d = _drop_forming(d)
    if len(d) < 130:
        return None, "short history (<130 bars)"
    o, h, l, c, v = d.Open, d.High, d.Low, d.Close, d.Volume

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    delta = c.diff()
    gain, loss = _wilder(delta.clip(lower=0)), _wilder((-delta).clip(lower=0))
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.where(loss != 0, 100.0)

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _wilder(tr)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)
    pdi = 100 * _wilder(pdm) / atr
    mdi = 100 * _wilder(mdm) / atr
    adx = _wilder(100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan))

    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()

    # Weekly trend from completed weekly bars only (ffill => no look-ahead)
    wc = c.resample("W-FRI").last().dropna()
    we9, we21 = wc.ewm(span=9, adjust=False).mean(), wc.ewm(span=21, adjust=False).mean()
    wt = pd.Series(np.where((wc > we9) & (we9 > we21), 1, np.where((wc < we9) & (we9 < we21), -1, 0)),
                   index=wc.index)
    wt.iloc[:21] = 0
    wt_d = wt.reindex(d.index, method="ffill").fillna(0)

    ret60 = c.pct_change(60)
    if nifty_close is not None and len(nifty_close) > 70:
        nret = nifty_close.pct_change(60).reindex(d.index, method="ffill")
    else:
        nret = pd.Series(np.nan, index=d.index)

    f = lambda s: s.to_numpy(dtype=float)
    A = dict(idx=d.index, o=f(o), h=f(h), l=f(l), c=f(c), v=f(v),
             ema20=f(ema20), ema50=f(ema50), ema200=f(ema200), rsi=f(rsi), atr=f(atr),
             adx=f(adx), pdi=f(pdi), mdi=f(mdi), hist=f(hist),
             vavg=f(v.rolling(20).mean().shift(1)),
             hi20=f(h.rolling(20).max().shift(1)), lo20=f(l.rolling(20).min().shift(1)),
             turn=f((c * v).rolling(20).mean()), wt=f(wt_d), ret60=f(ret60), nret=f(nret))
    return A, None


# ── SIGNAL + LEVELS (same function for live bar and backtest) ────────────────
def _levels(A, i, side):
    c, atr = A["c"][i], A["atr"][i]
    if side == "LONG":
        dist = c - (A["l"][max(0, i - 6):i + 1].min() - 0.2 * atr)
        sgn = 1
    else:
        dist = (A["h"][max(0, i - 6):i + 1].max() + 0.2 * atr) - c
        sgn = -1
    dist = min(max(dist, 1.0 * atr), 2.0 * atr)
    return dict(sl=c - sgn * dist, t1=c + sgn * 2.0 * atr, t2=c + sgn * 3.5 * atr,
                rr1=2.0 * atr / dist, rr2=3.5 * atr / dist, atr=atr)


def _signal(A, i, side):
    """-> (result_dict, None) or (None, reason).  Score is 0..10."""
    sgn = 1 if side == "LONG" else -1
    c, o = A["c"][i], A["o"][i]
    atr, rsi, adx = A["atr"][i], A["rsi"][i], A["adx"][i]
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(rsi) and np.isfinite(adx)):
        return None, "indicators not ready"
    if not A["turn"][i] >= MIN_TURNOVER:
        return None, "illiquid (<₹5Cr/day)"
    wt = A["wt"][i]
    if wt == -sgn:
        return None, "weekly trend opposes"
    if (c - A["ema20"][i]) * sgn > 2.5 * atr:
        return None, "extended from EMA20"
    if (side == "LONG" and rsi > 75) or (side == "SHORT" and rsi < 25):
        return None, "RSI extreme"

    e20, e50, e200 = A["ema20"][i], A["ema50"][i], A["ema200"][i]
    trend = int(c * sgn > e50 * sgn) + int(e20 * sgn > e50 * sgn > e200 * sgn)

    hist, rsi3 = A["hist"], A["rsi"][i - 3]
    mom = int(hist[i] * sgn > 0 and (hist[i] - hist[i - 1]) * sgn > 0)
    rz = (45 <= rsi <= 68) if side == "LONG" else (32 <= rsi <= 55)
    mom += int(rz and (rsi - rsi3) * sgn > 0)

    adx_pt = int(adx > 20 and (A["pdi"][i] - A["mdi"][i]) * sgn > 0)

    if side == "LONG":
        brk = c >= 0.98 * A["hi20"][i] and c > e20
        pb = (e20 > e50 and c > e50 and abs(c - e20) <= 0.8 * atr and c > o and rsi <= 58)
        names = ("Breakout", "Pullback")
    else:
        brk = c <= 1.02 * A["lo20"][i] and c < e20
        pb = (e20 < e50 and c < e50 and abs(c - e20) <= 0.8 * atr and c < o and rsi >= 42)
        names = ("Breakdown", "Rally-fade")
    setup = names[0] if brk else names[1] if pb else "Trend"
    setup_pt = int(setup != "Trend")

    vavg = A["vavg"][i]
    volx = A["v"][i] / vavg if vavg and vavg > 0 else 0.0
    dir_day = (c - A["c"][i - 1]) * sgn > 0
    dry = float(np.mean(A["v"][i - 2:i + 1])) < vavg if vavg and vavg > 0 else False
    vol_pt = int((dir_day and volx >= 1.3) or (setup == names[1] and dry))

    rs = A["ret60"][i] - A["nret"][i]
    rs_pt = int(np.isfinite(rs) and rs * sgn > 0)

    lv = _levels(A, i, side)
    if lv["rr1"] < MIN_RR1:
        return None, "R:R below 1.2 (stop too wide)"

    score = trend + (2 if wt == sgn else 0) + mom + adx_pt + vol_pt + rs_pt + setup_pt
    return dict(score=score, setup=setup, lv=lv, ltp=c, rsi=rsi, adx=adx, volx=volx,
                rs=(rs * 100 if np.isfinite(rs) else None),
                parts=dict(trend=trend, weekly=int(wt == sgn), mom=mom, adx=adx_pt,
                           vol=vol_pt, rs=rs_pt, setup=setup_pt)), None


def _backtest(A):
    """Walk-forward on the same arrays. Returns [(side, score, R, hit_t1, stopped)]."""
    n, out = len(A["c"]), []
    for side in ("LONG", "SHORT"):
        sgn = 1 if side == "LONG" else -1
        i = 100
        while i < n - 1 - HOLD_BARS:
            res, _ = _signal(A, i, side)
            if res is None or res["score"] < 5:
                i += 1
                continue
            lv, entry = res["lv"], A["o"][i + 1]
            sl, t1 = lv["sl"], lv["t1"]
            risk = (entry - sl) * sgn
            if risk <= 0 or (entry - t1) * sgn >= 0:        # gapped through stop/target
                i += 1
                continue
            R, hit, stop, end = None, False, False, i + HOLD_BARS
            for j in range(i + 1, i + HOLD_BARS + 1):
                lo, hi, op = A["l"][j], A["h"][j], A["o"][j]
                s_hit = lo <= sl if sgn == 1 else hi >= sl
                t_hit = hi >= t1 if sgn == 1 else lo <= t1
                if s_hit:                                   # stop first when both touch (conservative)
                    px = min(sl, op) if sgn == 1 else max(sl, op)
                    R, stop, end = (px - entry) * sgn / risk, True, j
                    break
                if t_hit:
                    R, hit, end = (t1 - entry) * sgn / risk, True, j
                    break
            if R is None:
                R = (A["c"][i + HOLD_BARS] - entry) * sgn / risk
            out.append((side, int(res["score"]), round(float(R), 2), hit, stop))
            i = end + 1
    return out


# ── MARKET REGIME ────────────────────────────────────────────────────────────
def _regime(nifty):
    try:
        c = pd.to_numeric(nifty["Close"], errors="coerce").dropna()
        e = c.ewm(span=50, adjust=False).mean()
        if c.iloc[-1] > e.iloc[-1] and e.iloc[-1] > e.iloc[-6]:
            return "bull"
        if c.iloc[-1] < e.iloc[-1] and e.iloc[-1] < e.iloc[-6]:
            return "bear"
        return "neutral"
    except Exception:
        return "unknown"


# ── SCAN (cached 30 min, shared by both swing buttons) ───────────────────────
def _scan():
    t0 = time.time()
    syms = _scan_universe()
    hist = bulk_history(syms, "1y", chunk_size=100) or {}
    nifty = index_history("^NSEI", "1y")
    if nifty is not None and not nifty.empty:
        nifty = _drop_forming(nifty)
    nclose = None
    if nifty is not None and not nifty.empty:
        try:
            nclose = pd.to_numeric(nifty["Close"], errors="coerce").dropna()
            nclose.index = _naive(nclose.index)
        except Exception:
            nclose = None

    rows, trades, asofs, scanned = [], [], [], 0
    for s in syms:
        df = hist.get(s)
        if df is None or len(df) == 0:
            logger.info(f"[Swing] skip {s}: no data")
            continue
        A, why = _prep(df, nclose)
        if A is None:
            logger.info(f"[Swing] skip {s}: {why}")
            continue
        last = A["idx"][-1]
        if (pd.Timestamp.now().normalize() - last).days > 6:
            logger.info(f"[Swing] skip {s}: stale data (last bar {last.date()})")
            continue
        scanned += 1
        asofs.append(last)
        n, fails = len(A["c"]), []
        for side in ("LONG", "SHORT"):
            res, reason = _signal(A, n - 1, side)
            if res:
                lv = res["lv"]
                rows.append(dict(sym=s, side=side, score=int(res["score"]), setup=res["setup"],
                                 ltp=float(res["ltp"]), rsi=float(res["rsi"]), adx=float(res["adx"]),
                                 volx=float(res["volx"]), rs=res["rs"], parts=res["parts"],
                                 sector=_sector_of(s),
                                 sl=float(lv["sl"]), t1=float(lv["t1"]), t2=float(lv["t2"]),
                                 rr1=float(lv["rr1"]), rr2=float(lv["rr2"]), atr=float(lv["atr"])))
            else:
                fails.append(f"{side}:{reason}")
        if len(fails) == 2:
            logger.info(f"[Swing] skip {s}: {'; '.join(fails)}")
        try:
            trades.extend(_backtest(A))
        except Exception as e:
            logger.warning(f"[Swing] backtest {s}: {e}")

    data = dict(asof=(max(asofs).strftime("%d-%b-%Y") if asofs else "n/a"), rows=rows, trades=trades,
                regime=_regime(nifty) if nifty is not None and not nifty.empty else "unknown",
                scanned=scanned, total=len(syms), secs=round(time.time() - t0, 1))
    logger.info(f"[Swing] scanned {scanned}/{len(syms)}, signals {len(rows)}, backtest trades {len(trades)} "
                f"in {data['secs']} sec (regime {data['regime']})")
    if scanned >= int(len(syms) * 0.7):
        cached_set(_ckey(), data, CACHE_TTL)
    return data


def _load():
    hit = cached_get(_ckey(), CACHE_TTL)
    if hit:
        return hit
    with _LOCK:                                   # one scan even if both buttons are pressed together
        hit = cached_get(_ckey(), CACHE_TTL)
        return hit or _scan()


# ── OUTPUT ───────────────────────────────────────────────────────────────────
esc = lambda x: html.escape(str(x), quote=False)


def _bt_line(trades, side, thr):
    t = [x for x in trades if x[0] == side and x[1] >= thr]
    if not t:
        return f"{side} ≥{thr}: no historical signals"
    n = len(t)
    hit = sum(1 for x in t if x[3]) / n * 100
    stp = sum(1 for x in t if x[4]) / n * 100
    avg = sum(x[2] for x in t) / n
    small = " (small sample)" if n < 15 else ""
    return f"{side} ≥{thr}: {n} trades · T1 hit {hit:.0f}% · stopped {stp:.0f}% · avg {avg:+.2f}R{small}"


def _card(p):
    icon = "🟢" if p["side"] == "LONG" else "🔴"
    c, sgn = p["ltp"], (1 if p["side"] == "LONG" else -1)
    lo, hi = c - 0.25 * p["atr"], c + 0.25 * p["atr"]
    pc = lambda x: f"{(x - c) / c * 100:+.1f}%"
    pt = p["parts"]
    rs = f"RS {p['rs']:+.1f}%" if p["rs"] is not None else "RS n/a"
    lines = [
        f"{icon} <b>{esc(p['sym'])}</b> · {p['side']} · {p['setup']} · <b>{p['score']}/10</b>",
        f"{esc(p['sector'] or '—')} · ₹{c:,.2f} · RSI {p['rsi']:.0f} · ADX {p['adx']:.0f} · Vol {p['volx']:.1f}×",
        f"📥 Entry ₹{min(lo, hi):,.2f}–₹{max(lo, hi):,.2f}",
        f"🛑 SL ₹{p['sl']:,.2f} ({pc(p['sl'])})",
        f"🎯 T1 ₹{p['t1']:,.2f} ({pc(p['t1'])}) 1:{p['rr1']:.1f} · T2 ₹{p['t2']:,.2f} ({pc(p['t2'])}) 1:{p['rr2']:.1f}",
        f"✓ Trend {pt['trend']}/2 · Weekly {'✓' if pt['weekly'] else '–'} · Mom {pt['mom']}/2 · "
        f"ADX {'✓' if pt['adx'] else '–'} · Vol {'✓' if pt['vol'] else '–'} · {rs}",
    ]
    if p["side"] == "SHORT":
        lines.append("⚠ Short = intraday / F&O only (no delivery shorting)")
    return "\n".join(lines)


def _pick(rows, side, thr, limit):
    rs = sorted((r for r in rows if r["side"] == side and r["score"] >= thr),
                key=lambda r: (-r["score"], -r["rr1"], r["sym"]))
    out, per = [], Counter()
    for r in rs:
        if per[r["sector"]] >= MAX_PER_SECTOR:
            continue
        per[r["sector"]] += 1
        out.append(r)
        if len(out) == limit:
            break
    return out


def get_swing_trades(mode="conservative"):
    """Returns ONE string; blocks are separated by blank lines so main.py can split safely."""
    mode = mode if mode in THRESHOLDS else "conservative"
    thr = dict(THRESHOLDS[mode])
    data = _load()
    if not data["scanned"]:
        return "❌ Market data unavailable right now. Try again in a minute."

    reg = data["regime"]
    if reg == "bear":
        thr["LONG"] += 1
    elif reg == "bull":
        thr["SHORT"] += 1
    reg_txt = {"bull": "🟢 Nifty uptrend (shorts need +1 score)",
               "bear": "🔴 Nifty downtrend (longs need +1 score)",
               "neutral": "🟡 Nifty neutral", "unknown": "⚪ Nifty regime unknown"}[reg]

    rows = data["rows"]
    longs = _pick(rows, "LONG", thr["LONG"], MAX_LONG)
    shorts = _pick(rows, "SHORT", thr["SHORT"], MAX_SHORT)

    head = (f"📈 <b>Swing Trades — {mode.title()}</b>\n"
            f"📅 Last completed candle {data['asof']} · scanned {data['scanned']}/{data['total']} · "
            f"hold ≤{HOLD_BARS} sessions\n{reg_txt}"
            + ("\n⏱ Market is open: signals use the last COMPLETED daily candle (today's is still forming)." if _session_open() else ""))
    bt = (f"🧪 <b>Same rules, last ~6M (backtest)</b>\n{_bt_line(data['trades'], 'LONG', thr['LONG'])}\n"
          f"{_bt_line(data['trades'], 'SHORT', thr['SHORT'])}\n"
          f"<i>Entry next open, stop-first if both touched, no costs. Past results don't guarantee future.</i>")
    foot = "⚠️ Educational only. Not SEBI-registered advice. Size so a stop-out costs ≤1% of capital."

    blocks = [head, bt]
    if longs:
        blocks.append("🟢 <b>LONG SETUPS</b>")
        blocks += [_card(p) for p in longs]
    if shorts:
        blocks.append("🔴 <b>SHORT SETUPS</b>")
        blocks += [_card(p) for p in shorts]
    if not longs and not shorts:
        near = sorted(rows, key=lambda r: (-r["score"], r["sym"]))[:6]
        w = ["⚠️ <b>No setup met the threshold today.</b>",
             f"Need LONG ≥{thr['LONG']} / SHORT ≥{thr['SHORT']}. Closest:"]
        for r in near:
            w.append(f"• <b>{esc(r['sym'])}</b> {r['side']} {r['score']}/10 · {r['setup']} · ₹{r['ltp']:,.2f}")
        if not near:
            w.append("• none passed the liquidity / trend / R:R gates")
        blocks.append("\n".join(w))
    blocks.append(foot)
    return "\n\n".join(blocks)
