"""Bounded, cache-first research screening. No AI calls; no scheduler.
Scores and limits are heuristic, not backtested investment advice.
"""
import html
import math
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

_LOCK = threading.Lock()
_SNAPSHOT = None
_PROFILES = {
    "conservative": dict(roe=12, growth=0, debt=0.7, vol=35, cap=20000, pe=50),
    "moderate": dict(roe=10, growth=0, debt=1.2, vol=50, cap=5000, pe=70),
    "aggressive": dict(roe=10, growth=10, debt=1.5, vol=70, cap=1000, pe=90),
}

def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None

def evaluate(symbol, info, frame, now=None):
    """Only accept explicit normalized units and dated provider snapshots."""
    now = time.time() if now is None else now
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol):
        return None, "invalid symbol"
    stamp = number(info.get("fetched_at"))
    if stamp is None or not 0 <= now-stamp <= 86400:
        return None, "fundamental snapshot missing/stale"
    sector = str(info.get("sector") or "").strip()
    if not sector:
        return None, "sector unavailable"
    if any(x in sector.lower() for x in ("financial", "bank", "insurance")):
        return None, "financial-sector model not implemented; safely excluded"
    fields = {
        "pe": info.get("pe"), "roe": info.get("roe_pct"),
        "growth": info.get("revenue_growth_pct"), "debt": info.get("debt_equity_ratio"),
        "cap": info.get("market_cap"),
    }
    values = {k: number(v) for k,v in fields.items()}
    if any(v is None for v in values.values()):
        return None, "required fundamentals missing"
    if values["pe"] <= 0 or values["cap"] <= 0 or values["debt"] < 0:
        return None, "invalid or loss-making valuation"
    if frame is None or frame.empty or not {"Close", "Volume"}.issubset(frame.columns):
        return None, "price/volume history unavailable"
    import pandas as pd
    c = pd.to_numeric(frame["Close"], errors="coerce")
    v = pd.to_numeric(frame["Volume"], errors="coerce")
    d = pd.DataFrame({"Close": c, "Volume": v}).dropna().sort_index()
    d = d.loc[~d.index.duplicated(keep="last")]
    d = d.loc[(d.Close > 0) & (d.Volume >= 0)]
    if len(d) < 100:
        return None, "less than 100 valid sessions"
    asof = pd.Timestamp(d.index[-1])
    age = now - asof.timestamp()
    if not -86400 <= age <= 5*86400:
        return None, "price history stale/future"
    c = d.Close
    vol = float(c.pct_change().dropna().std()*math.sqrt(252)*100)
    turnover = float((c*d.Volume).tail(20).mean()/1e7)
    if not math.isfinite(vol) or turnover < 5:
        return None, "insufficient liquidity/risk data"
    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    timing = "Constructive" if c.iloc[-1] > ema20 > ema50 else "Weak trend" if c.iloc[-1] < ema20 < ema50 else "Wait"
    return dict(symbol=symbol, name=str(info.get("name") or symbol)[:80], sector=sector,
                price=float(c.iloc[-1]), asof=asof.strftime("%d-%b-%Y"),
                fetched_at=stamp, vol=vol, turnover=turnover, timing=timing,
                **{**values, "cap":values["cap"]/1e7}), None

def rank(rows, profile, size=10):
    if profile not in _PROFILES:
        raise ValueError("Unknown profile")
    p = _PROFILES[profile]
    eligible = []
    seen = set()
    for r in rows:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        if not (r["roe"] >= p["roe"] and r["growth"] >= p["growth"] and
                r["debt"] <= p["debt"] and r["vol"] <= p["vol"] and
                r["cap"] >= p["cap"] and r["pe"] <= p["pe"]):
            continue
        quality = min(100, max(0, r["roe"]*3))
        growth = min(100, max(0, r["growth"]*2))
        safety = max(0, 100-r["vol"])
        value = max(0, 100-r["pe"])
        weights = (.4,.1,.35,.15) if profile == "conservative" else (.35,.25,.2,.2) if profile == "moderate" else (.3,.4,.15,.15)
        score = round(sum(a*b for a,b in zip(weights,(quality,growth,safety,value))),1)
        eligible.append(dict(r, score=score))
    eligible.sort(key=lambda r:(-r["score"], r["symbol"]))
    selected, sectors = [], {}
    for r in eligible:
        if sectors.get(r["sector"],0) >= 3:
            continue
        selected.append(r)
        sectors[r["sector"]] = sectors.get(r["sector"],0)+1
        if len(selected) == size:
            break
    return selected

def snapshot(universe, cached_get, get_info, bulk_history, priority=None):
    global _SNAPSHOT
    with _LOCK:
        now = time.time()
        if _SNAPSHOT and now-_SNAPSHOT["created"] < _SNAPSHOT.get("ttl", 900):
            return _SNAPSHOT
        symbols = sorted({str(s).upper().removesuffix(".NS") for s in universe})[:1000]
        infos, missing = {}, []
        for s in symbols:
            info = cached_get(f"info_v2_{s}.NS",86400)
            if info and number(info.get("fetched_at")) is not None and 0 <= now-info["fetched_at"] <= 86400:
                infos[s] = info
            else:
                missing.append(s)
        budget = min(8,max(0,int(os.getenv("RESEARCH_REFRESH_BUDGET","8"))))
        # Only the curated priority list is ever refreshed on click (<=30 get_info per 24h).
        prio = {str(s).upper().removesuffix(".NS") for s in (priority or [])}
        refresh = [s for s in missing if s in prio]
        for s in refresh[:budget]:
            try:
                info = get_info(s)
                if info:
                    infos[s] = info
            except Exception:
                pass
        # Fair profile-independent shortlist; maximum 60 history series.
        candidates = []
        for s,i in infos.items():
            roe = number(i.get("roe_pct"))
            if roe is not None and roe >= 10:
                candidates.append(s)
        candidates.sort(key=lambda s:(-(number(infos[s].get("market_cap")) or 0),s))
        candidates = candidates[:60]
        history = (bulk_history(candidates,"6mo",chunk_size=60) or {}) if candidates else {}
        rows, reasons = [], {}
        for s in candidates:
            row, reason = evaluate(s,infos[s],history.get(s))
            if row:
                rows.append(row)
            else:
                reasons[reason] = reasons.get(reason,0)+1
        pending = max(0, len(refresh) - budget)
        _SNAPSHOT = dict(created=now, universe=len(symbols), cached=len(infos),
                         checked=len(candidates), rows=rows, rejected=reasons,
                         ttl=120 if pending else 900, pending=pending)
        return _SNAPSHOT

def render(profile, snap):
    rows = rank(snap["rows"],profile)
    esc = lambda x: html.escape(str(x),quote=False)
    stamp = datetime.fromtimestamp(snap["created"],timezone(timedelta(hours=5,minutes=30))).strftime("%d-%b %H:%M IST")
    header = f"<b>{esc(profile.title())} | Fundamental shortlist</b>\nSnapshot: {stamp}\nUniverse {snap['universe']} • cached fundamentals {snap['cached']} • checked {snap['checked']}\n"
    if len(rows) < 10:
        warm = snap.get("pending", 0)
        warm_note = f"\nCache warming: {warm} more symbols load on later presses; retry in ~2 min.\n" if warm else ""
        reasons = "; ".join(f"{k}: {v}" for k,v in sorted(snap["rejected"].items()))
        return [header + f"\nOnly {len(rows)}/10 qualified. No complete portfolio published.\n"
                "No quality rules were relaxed. Cached coverage may be incomplete.\n" + warm_note +
                f"Validation: {esc(reasons or 'insufficient eligible cached candidates')}\n"
                "Financial-sector companies are excluded in this version."], []
    messages = [header+"Heuristic ranking—not a return forecast. Financial-sector companies excluded.\nPrices: latest available candles, not live.\nGrowth = provider revenue-growth metric, not 3Y CAGR."]
    for start in (0,5):
        cards=[]
        for idx,r in enumerate(rows[start:start+5],start+1):
            risk = "Elevated volatility" if r['vol'] > 35 else "Valuation risk" if r['pe'] > 35 else "Earnings and market risk"
            cards.append(f"<b>{idx}. {esc(r['name'][:45])} ({esc(r['symbol'])})</b>\n"
                         f"{esc(r['sector'][:35])} | ₹{r['price']:,.2f} | {r['asof']}\n"
                         f"Score {r['score']}/100 | Timing: {r['timing']}\n"
                         f"ROE {r['roe']:.1f}% | Revenue growth {r['growth']:.1f}%\n"
                         f"P/E {r['pe']:.1f} | D/E {r['debt']:.2f} | Vol {r['vol']:.1f}%\n"
                         f"Why: meets {profile} quality/risk thresholds.\nRisk: {risk}.")
        messages.append("\n\n".join(cards))
    messages.append("Tap a symbol for Technical / Fundamental / Chart / PDF.\nEducational automated screening; not personalized advice. Scores are not backtested. Financial results may lag the fetch timestamp.")
    return messages, [r["symbol"] for r in rows]
