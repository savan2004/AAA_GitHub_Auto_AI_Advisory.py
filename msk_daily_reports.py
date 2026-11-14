"""Investor-facing, reproducible Nifty top-five research from durable NSE evidence.

The source is the latest available completed candle and saved financial facts.
No invented quotes, no stale-day BUY claims, and no per-user/daily send lock.
"""
from __future__ import annotations

import html
import math
import re
from datetime import date, datetime, timedelta, timezone, time as dtime
from concurrent.futures import ThreadPoolExecutor, wait

# Keep command routing dependency-free: the original menu smoke suite runs
# without third-party networking packages installed.
IST = timezone(timedelta(hours=5, minutes=30))

NAMES = {
    "conservative": ("LARGE CAP", "Nifty 100", 8.0),
    "moderate": ("MID CAP", "Nifty Midcap 150", 11.0),
    "aggressive": ("SMALL CAP", "Nifty Smallcap 250", 15.0),
}
WEIGHTS = (0.30, 0.25, 0.20, 0.15, 0.10)
VERDICT_FIVE = "SCREENING COMPLETE — 5 RESEARCH CANDIDATES"
VERDICT_NONE = "SCREENING COMPLETE — NO QUALIFYING SETUP TODAY"


def parse_request(message):
    """Recognize market report requests, never swallow /report SYMBOL PDFs."""
    if not isinstance(message, str):
        return ()
    raw = re.sub(r"\s+", " ", message.strip()).lower()
    raw = re.sub(r"^/(?:top5|top_5|fullreport|report)(?:@\w+)?", lambda m: m.group().split("@")[0], raw)
    if not raw:
        return ()
    if raw in ("all", "/fullreport", "full report", "/all", "top5 all",
               "top 5 all", "all reports", "all top 5", "full market report"):
        return tuple(NAMES)
    if raw in ("top5 mid", "top 5 mid", "mid cap", "mid cap report", "mc",
               "/mc", "/top5 mid", "top5 midcap", "mid cap top 5",
               "⚖️ mid cap top 5", "⚖️ moderate"):
        return ("moderate",)
    if raw in ("top5 small", "top 5 small", "small cap", "small cap report",
               "sc", "/sc", "/top5 small", "top5 smallcap",
               "small cap top 5", "🚀 small cap top 5", "🚀 aggressive"):
        return ("aggressive",)
    if raw in ("top5", "top 5", "/top5", "report", "/report", "lc",
               "/lc", "large cap", "large cap report", "large cap top 5",
               "🏦 large cap top 5", "🏦 conservative", "market report",
               "top picks", "top five", "nifty 100 top 5"):
        return ("conservative",)
    # Natural requests must name the segment or a clear top-picks intent.
    if any(s in raw for s in ("top picks", "market report", "top 5",
                              "top5", "research watchlist")) and not raw.startswith("/report "):
        if "small" in raw:
            return ("aggressive",)
        if "mid" in raw:
            return ("moderate",)
        if "all" in raw or "full" in raw:
            return tuple(NAMES)
        return ("conservative",)
    return ()


def _num(value):
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def _bound(v):
    return min(100.0, max(0.0, v))


def _score(f, t):
    """Five explicit non-predictive components with agreed 30/25/20/15/10 weights.

    Source OHLCV screening already requires median 20D daily turnover >= Rs 2cr.
    Legacy records do not persist the turnover; risk score therefore uses stop
    distance, and no claim of verified turnover beyond that screening gate.
    """
    adx, rsi, relvol, pe, roe, growth, risk = (
        _num(t.get("adx")), _num(t.get("rsi")), _num(t.get("volx")),
        _num(f.get("pe")), _num(f.get("roe")), _num(f.get("profit_growth")),
        _num(t.get("risk_pct")),
    )
    if any(v is None for v in (adx, rsi, relvol, pe, roe, growth, risk)):
        return None
    if not (0 < pe <= 250 and -100 <= growth <= 1000 and
            0 <= roe <= 200 and 0 <= rsi <= 100 and 0 < relvol <= 100 and
            0 <= adx <= 100 and 0 < risk <= 100):
        return None
    trend = _bound(45 + (24 if t.get("trend_confirmed") is True else 0)
                   + (5 if t.get("breakout_confirmed") is True else 0)
                   + 0.5 * (adx - 18))
    momentum = _bound(0.7 * _bound(100 - abs(rsi - 62) * 2.5)
                      + 0.3 * _bound(relvol * 50))
    valuation = _bound(100 - (pe - 10) * 1.7)
    earnings = _bound(45 + roe * 0.9 + growth * 0.8)
    # Risk and actual 20-session turnover share the final 10%. Historic
    # screened rows without persisted turnover receive a neutral 50/100
    # liquidity score, never an invented traded-value number.
    turnover = _num(t.get("turnover_20d"))
    liquidity = (_bound(50 + 25 * math.log10(turnover / 2e7))
                 if turnover is not None and turnover >= 2e7 else 50.0)
    liquidity_risk = _bound(0.7 * _bound(100 - risk * 4) + 0.3 * liquidity)
    return round(sum(v * w for v, w in zip(
        (trend, momentum, valuation, earnings, liquidity_risk), WEIGHTS)), 1)


def _ranked(profile, records, stats, session):
    hash_value = stats.get("membership_hash")
    if not isinstance(hash_value, str) or len(hash_value) != 16:
        return []
    candidates = []
    for sym, r in records.items():
        if not isinstance(r, dict) or r.get("membership_hash") != hash_value:
            continue
        if r.get("fund_status") != "VALIDATED" or r.get("price_reason") != "ok":
            continue
        t, f = r.get("technical"), r.get("fundamental")
        if not isinstance(t, dict) or not isinstance(f, dict) or t.get("asof") != str(session):
            continue
        if t.get("signal") not in ("WAIT", "CONDITIONAL BUY"):
            continue
        entry, sl, t1, t2, ltp = (_num(t.get(key)) for key in
                                   ("entry", "sl", "t1", "t2", "ltp"))
        if any(v is None for v in (entry, sl, t1, t2, ltp)):
            continue
        if not (0 < sl < entry < t1 < t2 and ltp > 0):
            continue
        risk = _num(t.get("risk_pct"))
        if risk is None or abs(100 * (entry - sl) / entry - risk) > 0.15:
            continue
        score = _score(f, t)
        if score is None:
            continue
        # Real liquidity was already checked inside the technical collector.
        # If a newer record exposes turnover explicitly, recheck it here.
        turnover = _num(t.get("turnover_20d"))
        if "turnover_20d" in t and (turnover is None or turnover < 2e7):
            continue
        candidates.append((score, str(sym), str(r.get("industry") or "Unclassified"), t))
    return sorted(candidates, key=lambda a: (-a[0], a[1]))[:5]


def _context(breadth, session, segment, risk_limit):
    lines = []
    if isinstance(breadth, dict) and str(breadth.get("session")) == str(session):
        change = _num(breadth.get("benchmark_change"))
        up, down = (_num(breadth.get("advances")), _num(breadth.get("declines")))
        valid = _num(breadth.get("valid"))
        if change is not None:
            lines.append(f"Nifty 50: {change:+.2f}% (last completed session).")
        if valid and up is not None and down is not None:
            lines.append(f"Nifty 500: {up/valid*100:.1f}% advancing; "
                         f"{down/valid*100:.1f}% declining.")
    if not lines:
        lines.append(f"Market-wide breadth not available for {html.escape(str(session))}; "
                     "stock-specific levels are from that close.")
    lines.append(f"{segment} risk ceiling: stop distance ≤{risk_limit:.0f}%; "
                 "price breakout alone is not an entry.")
    return lines[:3]


# Shared bounded quote pool; incomplete provider requests must never hold reports.
_QUOTE_POOL = ThreadPoolExecutor(max_workers=5, thread_name_prefix="msk-top5-quote")


def _intraday_quotes(symbols, now):
    """Optional timestamp-verified LTPs; no price-only cache becomes a BUY signal."""
    if (now.weekday() > 4 or
            not dtime(9, 15) <= now.timetz().replace(tzinfo=None) < dtime(15, 30)):
        return {}
    try:
        from data_engine import get_verified_quote
    except ImportError:
        return {}
    tasks = {_QUOTE_POOL.submit(get_verified_quote, sym): sym for sym in symbols}
    done, _unfinished = wait(tasks, timeout=5)
    result = {}
    for future in done:
        try:
            quote = future.result()
            if not isinstance(quote, dict) or quote.get("quote_status") != "recent_observation":
                continue
            stamp = datetime.fromisoformat(quote.get("quote_asof_ist", ""))
            price = _num(quote.get("price"))
            source = quote.get("quote_source")
            seconds = (now - stamp.astimezone(IST)).total_seconds()
            if (price is not None and price > 0 and stamp.tzinfo is not None
                    and stamp.astimezone(IST).date() == now.date()
                    and -30 <= seconds <= 900
                    and source in ("NSE India", "Yahoo Finance v8")):
                result[tasks[future]] = (price, stamp.astimezone(IST))
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue
    return result


def format_report(profile, records, stats, session, *, now=None, breadth=None, quotes=None):
    """Fresh rendering for each request, no fabricated price levels or winners."""
    if profile not in NAMES:
        raise ValueError("unknown_profile")
    now = now or datetime.now(IST)
    segment, index, cap = NAMES[profile]
    day = date.fromisoformat(str(session))
    if day > now.date():
        raise ValueError("future_candle_is_not_valid")
    ranked = _ranked(profile, records, stats, day)
    quotes = quotes or {}
    label = (f"As of {day.isoformat()} close" if day == now.date()
             else f"As of {day.isoformat()} — next update after market close")
    if quotes:
        latest = max(stamp for _price, stamp in quotes.values())
        label = (f"As of {latest.strftime('%d %b %H:%M')} IST (verified quotes); "
                 f"trade levels: {day.isoformat()} close")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>MSK MARKET UPDATE</b>",
        f"<b>{segment} | {index}</b>",
        f"📅 {now.strftime('%d %b %Y')} | {label}",
        "",
        "🎯 <b>TOP 5 RANKED CANDIDATES</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if len(ranked) < 5:
        lines.append(f"NO QUALIFYING SETUP TODAY: {len(ranked)}/5 companies "
                     "have complete financial, price, risk and liquidity evidence.")
    else:
        for i, (score, sym, sector, t) in enumerate(ranked, 1):
            entry, sl, t1, t2 = (_num(t[k]) for k in ("entry", "sl", "t1", "t2"))
            risk = _num(t.get("risk_pct"))
            notes = list(t.get("signal_reasons") or [])
            confirmed = (t.get("signal") == "CONDITIONAL BUY" and day == now.date()
                         and risk <= cap and t.get("breakout_confirmed") is True
                         and t.get("trend_confirmed") is True
                         and _num(t.get("volx")) is not None
                         and _num(t.get("volx")) >= 1)
            status = "BUY" if confirmed else "WAIT"
            if risk > cap and "stop_risk_above_profile_limit" not in notes:
                notes.append("stop_risk_above_profile_limit")
            labels = {
                "breakout_not_confirmed": "breakout unconfirmed",
                "trend_not_confirmed": "trend unconfirmed",
                "stop_risk_above_profile_limit": f"stop risk {risk:.1f}% > {cap:.0f}% limit",
                "rsi_below_54": "RSI below 54",
                "adx_below_18": "ADX below 18",
            }
            reason = ("breakout, trend and volume confirmed at the last close"
                      if confirmed else "; ".join(labels.get(k, str(k).replace("_", " "))
                                                   for k in notes[:3]))
            if status == "WAIT" and not reason:
                reason = "requires fresh completed-session breakout confirmation"
            # The range is purely an observation band, never a BUY order.
            high = round(entry * 1.005, 2)
            rr = (t1 - entry) / (entry - sl)
            if sym in quotes:
                live_price, stamp = quotes[sym]
                reason += (f"; LTP ₹{live_price:,.2f} at "
                           f"{stamp.strftime('%H:%M')} IST (not a breakout confirmation)")
            lines.extend([
                "",
                f"<b>{i}. {html.escape(sym)} | Score {score:.1f}/100</b>",
                f"Sector: {html.escape(sector)}",
                f"Entry: ₹{entry:,.2f}–{high:,.2f} | SL: ₹{sl:,.2f}",
                f"T1: ₹{t1:,.2f} | T2: ₹{t2:,.2f}",
                f"R:R = 1:{rr:.1f} at trigger | Status: <b>{status}</b>",
                f"Reason: {html.escape(reason)}",
            ])
    lines.extend(["", "━━━━━━━━━━━━━━━━━━━━━━━",
                  "🧠 <b>MARKET CONTEXT</b>"])
    lines.extend(_context(breadth, day, segment, cap))
    lines.extend(["", "✅ <b>ACTION SUMMARY</b>"])
    if len(ranked) == 5:
        watch = ", ".join(f"{sym} >₹{_num(t['entry']):,.2f}" for _, sym, _, t in ranked)
        lines.append("Watch: " + watch)
        lines.append("Enter only after confirmed breakout, supporting volume and stop risk within limit.")
    else:
        lines.append("Keep a WAIT stance until five data-complete, liquid candidates emerge.")
        lines.append("No BUY signal from missing or stale evidence.")
    lines.extend(["", "⛔ STATUS: " + (VERDICT_FIVE if len(ranked) == 5 else VERDICT_NONE),
                  f"📍 Data as of {day.isoformat()}. Educational purposes only.",
                  "━━━━━━━━━━━━━━━━━━━━━━━"])
    out = "\n".join(lines)
    if len(out) > 4096:
        raise ValueError("report_exceeds_telegram_limit")
    return out


def render_latest_profile(profile, *, now=None):
    """Always re-read saved per-company evidence; never replay a 'sent today' flag."""
    if profile not in NAMES:
        raise ValueError("unknown_profile")
    from overnight_index_reports import EvidenceStore
    with EvidenceStore() as store:
        report = store.get_report(profile)
        if not report:
            raise ValueError("no_completed_market_session")
        rows = store.rows(report["day"], profile)
    breadth = None
    try:
        from market_breadth import _load_daily_snapshot
        breadth = _load_daily_snapshot(str(report["day"]))
    except (ValueError, RuntimeError, OSError, ImportError, KeyError, TypeError):
        pass
    now = now or datetime.now(IST)
    candidates = _ranked(profile, rows, report["stats"], report["day"])
    quotes = (_intraday_quotes([sym for _score, sym, _sector, _tech in candidates], now)
              if len(candidates) == 5 else {})
    return format_report(profile, rows, report["stats"], report["day"],
                         now=now, breadth=breadth, quotes=quotes)
