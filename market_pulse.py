"""Concise, evidence-gated Market Breadth view for the MSK Telegram button.

No static news, no assumed live quotes, no paid APIs and no false breadth totals.
Long Nifty-500 scans run in one shared background worker, never on the reply path.
"""
import html
import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from market_breadth import IST, completed_prices, expected_completed_session, get_breadth

_LOG = logging.getLogger(__name__)
_FEEDS = (
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="msk-breadth-refresh")
_LOCK = threading.Lock()
_FUTURE = None
_RETRY_AFTER = 0.0


def index_context(frame, now=None):
    """Use only the expected completed Nifty daily bar, never an unclosed candle."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    if frame is None or frame.empty:
        return None
    prices = completed_prices(frame, now)
    required = ("Close", "High", "Low")
    if prices.empty or any(key not in prices for key in required):
        return None
    bars = prices.loc[:, list(required)].apply(pd.to_numeric, errors="coerce").dropna()
    bars = bars.loc[(bars["Low"] > 0) & (bars["High"] >= bars["Low"]) &
                    (bars["Close"] >= bars["Low"]) & (bars["Close"] <= bars["High"])]
    if len(bars) < 2 or bars.index[-1].date() != expected_completed_session(now):
        return None
    last, previous = bars.iloc[-1], bars.iloc[-2]
    if previous["Close"] <= 0:
        return None
    change = (float(last["Close"]) / float(previous["Close"]) - 1.0) * 100
    result = {
        "session": str(bars.index[-1].date()),
        "close": float(last["Close"]),
        "change": round(change, 2),
        "day_range": round(float(last["High"] - last["Low"]), 1),
        "atr": None,
        "atr_pct": None,
        "atr_ratio": None,
        "volatility": None,
    }
    if len(bars) < 40:
        return result
    prev_close = bars["Close"].shift(1)
    true_range = pd.concat([
        bars["High"] - bars["Low"],
        (bars["High"] - prev_close).abs(),
        (bars["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    past = atr.shift(1).tail(20).dropna()
    current = float(atr.iloc[-1])
    baseline = float(past.median()) if len(past) >= 15 else 0.0
    if not (math.isfinite(current) and math.isfinite(baseline) and baseline > 0):
        return result
    ratio = current / baseline
    result.update(
        atr=round(current, 1),
        atr_pct=round(current / float(last["Close"]) * 100.0, 2),
        atr_ratio=round(ratio, 2),
        volatility=("ELEVATED" if ratio >= 1.25 else
                    "CALMER" if ratio <= 0.80 else "NORMAL"),
    )
    return result


def _breadth_result():
    """Keep at most one 500-stock scan active and never block the user response."""
    global _FUTURE, _RETRY_AFTER
    with _LOCK:
        if _FUTURE is not None and _FUTURE.done():
            try:
                result = _FUTURE.result()
            except Exception as exc:
                _LOG.warning("Market breadth unavailable: %s", type(exc).__name__)
                _FUTURE = None
                _RETRY_AFTER = time.monotonic() + 600
                return None, "unavailable (source coverage or freshness)"
            if (result.get("session") == str(expected_completed_session())
                    and result.get("coverage", 0) >= 80 and not result.get("stale")):
                return result, None
            _FUTURE = None
        if _FUTURE is None and time.monotonic() >= _RETRY_AFTER:
            _FUTURE = _POOL.submit(get_breadth)
        if _FUTURE is not None:
            return None, "updating; tap again after the scan finishes"
    return None, "unavailable; data source retry pending"


def _news_score(title):
    s = title.lower()
    if "jio" in s and ("list" in s or "ipo" in s or "debut" in s):
        return 6
    if ("rbi" in s and any(k in s for k in ("repo", "rate", "policy"))) or (
            "fed" in s and any(k in s for k in ("rate", "policy", "decision"))):
        return 5
    if any(k in s for k in ("crude oil", "oil price", "geopolitical", "sebi", "ipo listing")):
        return 4
    if any(k in s for k in ("nifty", "sensex", "market crash", "market rally")):
        return 2
    return 0


def verified_market_event(now=None, fetch=None):
    """Date-check publisher RSS entries. Never use static or undated headlines."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    fetch = fetch or requests.get
    candidates = []
    for source, url in _FEEDS:
        try:
            response = fetch(url, headers={"User-Agent": "MSK-Market-Pulse/1.0"}, timeout=4)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item")[:35]:
                title = " ".join((item.findtext("title") or "").split())
                stamp = item.findtext("pubDate")
                if not title or not stamp or len(title) > 240:
                    continue
                try:
                    posted = parsedate_to_datetime(stamp)
                    if posted.tzinfo is None:
                        continue
                    posted = posted.astimezone(IST)
                except (TypeError, ValueError, OverflowError):
                    continue
                age = now - posted
                if not timedelta(minutes=-10) <= age <= timedelta(hours=30):
                    continue
                score = _news_score(title)
                if score:
                    candidates.append((score, posted, title, source))
        except (requests.RequestException, ET.ParseError, ValueError, TypeError) as exc:
            _LOG.debug("Market event RSS %s unavailable: %s", source, type(exc).__name__)
    if not candidates:
        return None
    _, posted, title, source = max(candidates, key=lambda x: (x[0], x[1]))
    return {"title": title, "source": source, "published": posted.strftime("%d %b %H:%M IST")}


def format_market_pulse(index=None, breadth=None, event=None, breadth_status=None, now=None):
    """Short Telegram HTML view; every source/date and missing metric is disclosed."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    lines = ["📊 <b>MARKET BREADTH &amp; PULSE</b>"]
    if index:
        arrow = "🟢" if index["change"] > 0.1 else ("🔴" if index["change"] < -0.1 else "⚪")
        lines += [
            f"📅 Nifty session: {index['session']} (completed daily candle)",
            f"{arrow} <b>Nifty 50</b> {index['close']:,.0f} ({index['change']:+.2f}%)"
            f" | Range {index['day_range']:,.0f} pts",
        ]
        if index["atr"] is not None:
            lines.append(
                f"⚡ <b>ATR(14)</b> {index['atr']:,.0f} pts ({index['atr_pct']:.2f}% of index)"
                f" | {index['atr_ratio']:.2f}× usual: <b>{index['volatility']}</b>"
            )
        else:
            lines.append("⚡ ATR comparison unavailable (insufficient verified bars).")
    else:
        lines.append("⚠️ Nifty close/ATR: verified completed-session quote unavailable.")

    if breadth is not None:
        valid = breadth["valid"]
        advances = breadth["advances"]
        declines = breadth["declines"]
        lines.append(
            f"🌐 Nifty basket ({breadth['session']}): {advances}↑ / {declines}↓"
            f" | coverage {breadth['coverage']:.1f}%"
        )
        ma20 = breadth.get("ma20", {}).get("pct")
        if ma20 is not None:
            lines.append(f"📐 Above 20D SMA: {ma20:.0f}% of eligible stocks")
        if valid:
            participation = advances / valid
            breadth_tone = ("mostly advancing" if participation >= 0.6 else
                            "mostly declining" if participation <= 0.4 else "mixed")
        else:
            breadth_tone = "unavailable"
    else:
        lines.append("🌐 Nifty basket: " + html.escape(breadth_status or "unavailable"))

    if index and breadth:
        if index["change"] < -0.1 and breadth_tone == "mostly advancing":
            reading = "Nifty down, but wider participation positive; index/breadth divergence."
        elif index["change"] > 0.1 and breadth_tone == "mostly declining":
            reading = "Nifty up, but wider participation weak; rally not broad."
        elif index["change"] < -0.1 and breadth_tone == "mostly declining":
            reading = "Index and wider participation both weak."
        elif index["change"] > 0.1 and breadth_tone == "mostly advancing":
            reading = "Index and wider participation both positive."
        else:
            reading = "Mixed direction; no strong market-wide agreement."
    elif index:
        reading = ("Nifty fell" if index["change"] < -0.1 else
                   "Nifty rose" if index["change"] > 0.1 else "Nifty broadly flat")
        reading += "; market-wide confirmation pending."
    else:
        reading = "Direction unverified; avoid relying on unsupported market claims."
    if index and index.get("volatility") == "ELEVATED":
        reading += " ATR signals wider recent daily swings."
    lines.append("🧭 <b>View:</b> " + html.escape(reading))

    if event:
        lines.append("📰 <b>Key event:</b> " + html.escape(event["title"][:130]))
        lines.append("    " + html.escape(event["source"] + " • " + event["published"]))
    else:
        lines.append("📰 Key event: no timely source-verified headline available.")

    lines.append("ℹ️ Nifty: Yahoo daily OHLC (not live). Basket: verified completed bars only.")
    lines.append("Educational market context; not a trade signal.")
    return "\n".join(lines)


def build_market_pulse():
    """One short answer even when Nifty-500 API coverage is insufficient."""
    from market_data import index_history
    index = None
    try:
        index = index_context(index_history("^NSEI", "3mo"))
    except Exception as exc:
        _LOG.warning("Nifty pulse unavailable: %s", type(exc).__name__)
    breadth, status = _breadth_result()
    try:
        event = verified_market_event()
    except Exception as exc:
        _LOG.warning("Market event unavailable: %s", type(exc).__name__)
        event = None
    return format_market_pulse(index, breadth, event, status)
