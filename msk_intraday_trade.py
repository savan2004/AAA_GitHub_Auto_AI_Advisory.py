"""On-demand Nifty / Bank Nifty intraday decision aid.

Never publishes a trade using the prior session, unfinished 15-minute bars,
unconfirmed exchange data, nonexistent index volume or simulated prices.
Yahoo charts are best-effort public market data; delayed/missing data => NO TRADE.
The volume and VWAP confirmation uses a corresponding NSE-listed ETF, not
fictional volume from the spot index.
"""
from __future__ import annotations

import html
import csv
import io
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
LOG = logging.getLogger("msk_intraday")
MARKETS = (
    ("NIFTY 50", "^NSEI", "NIFTYBEES.NS"),
    ("BANK NIFTY", "^NSEBANK", "BANKBEES.NS"),
)
_MIN_ADX = 20
_CANDLE_MIN = 15
_MIN_HISTORY_BARS = 150
_LOOKBACK_BARS = 200
_MAX_BAR_AGE_MIN = 20
_MAX_RISK_PCT = 0.9
_FEED_TIMEOUT = 6


def _finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _as_ist(moment):
    if moment is None:
        return datetime.now(IST)
    if not isinstance(moment, datetime):
        raise TypeError("now must be a datetime")
    if moment.tzinfo is None:
        # A naive supplied test date is interpreted as IST, never as UTC.
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def market_session_open(now):
    """NSE regular weekday window; holiday/stale feeds fail separately."""
    now = _as_ist(now)
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 46 <= minutes <= 15 * 60 + 15


def _fetch_yahoo_15m(symbol, now):
    """Fetch directly, without the daily-price disk cache.

    Every candle carries its actual exchange timestamp. A weekend or
    Yahoo-delayed response never becomes a 'LIVE' trading signal.
    """
    # Yahoo's query1 and query2 are mirrors, not independent market sources.
    # A 429/5xx/network failure on query1 may recover through query2, but
    # never bypass timestamp, session, completeness or candle checks below.
    params = {"range": "30d", "interval": "15m", "includePrePost": "false"}
    headers = {"Accept": "application/json",
               "User-Agent": "Mozilla/5.0 (compatible; MSKBot/6.3; market-data verification)"}
    body = None
    failures = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = "https://" + host + "/v8/finance/chart/" + quote(symbol, safe="")
        try:
            response = requests.get(url, params=params, headers=headers,
                                    timeout=_FEED_TIMEOUT)
            response.raise_for_status()
            if len(response.content) > 3_000_000:
                raise ValueError("intraday_payload_oversized")
            body = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            failures.append(type(exc).__name__)
            LOG.warning("INTRADAY_FEED_RETRY symbol=%s host=%s kind=%s",
                        symbol, host, type(exc).__name__)
    if body is None:
        raise RuntimeError("intraday_yahoo_mirrors_unavailable: " + ",".join(failures))
    chart = body.get("chart") if isinstance(body, dict) else None
    nodes = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
        raise ValueError("intraday_yahoo_result_missing")
    block = nodes[0]
    meta = block.get("meta") or {}
    if (meta.get("exchangeTimezoneName") != "Asia/Kolkata" or
            not isinstance(block.get("timestamp"), list)):
        raise ValueError("intraday_exchange_time_unverified")
    stamps = block["timestamp"]
    indicators = block.get("indicators") or {}
    quotes = indicators.get("quote") or []
    if len(quotes) != 1 or not isinstance(quotes[0], dict):
        raise ValueError("intraday_ohlcv_missing")
    candle = quotes[0]
    cols = ("open", "high", "low", "close", "volume")
    if any(not isinstance(candle.get(k), list) or len(candle[k]) != len(stamps)
           for k in cols):
        raise ValueError("intraday_misaligned_ohlcv")
    rows = []
    for i, ts in enumerate(stamps):
        vals = [_finite(candle[k][i]) for k in cols]
        if not isinstance(ts, (int, float)) or any(x is None for x in vals):
            continue
        at = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
        minute = at.hour * 60 + at.minute
        if not (9 * 60 + 15 <= minute <= 15 * 60 + 15
                and at.minute in (0, 15, 30, 45)):
            continue
        op, hi, lo, cl, vol = vals
        if (min(op, hi, lo, cl) <= 0 or vol < 0 or hi < max(op, cl)
                or lo > min(op, cl) or lo > hi):
            continue
        rows.append((at, op, hi, lo, cl, vol))
    if not rows:
        raise ValueError("intraday_no_valid_bars")
    frame = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    frame = frame.drop_duplicates(subset="ts", keep="last").sort_values("ts")
    return frame.set_index(pd.DatetimeIndex(frame.pop("ts")))



def _fetch_yfinance_15m(symbol, now):
    """Use the project's already-installed yfinance client as a separate
    transport after both raw Yahoo HTTP endpoints fail. No price cache,
    synthetic candles or previous-session substitution is permitted.
    """
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    frame = ticker.history(period="30d", interval="15m",
                           auto_adjust=False, prepost=False,
                           raise_errors=True)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("intraday_yfinance_no_bars")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("intraday_yfinance_timezone_unverified")
    if any(col not in frame.columns for col in
           ("Open", "High", "Low", "Close", "Volume")):
        raise ValueError("intraday_yfinance_ohlcv_missing")
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    frame.index = frame.index.tz_convert(IST)
    # Apply exactly the same verified completed-bar rules as raw Yahoo.
    validated = _completed(frame, now)
    if validated.empty:
        raise ValueError("intraday_yfinance_no_completed_bars")
    return validated


def _fetch_intraday_15m(symbol, now):
    """Recover from raw Yahoo transport failures using existing yfinance.

    Keep hard failures for malformed exchange data; never convert a bad
    response into a trade-ready quote.
    """
    try:
        return _fetch_yahoo_15m(symbol, now)
    except (requests.RequestException, RuntimeError) as exc:
        LOG.warning("INTRADAY_PROVIDER_FALLBACK symbol=%s kind=%s",
                    symbol, type(exc).__name__)
        return _fetch_yfinance_15m(symbol, now)


def _completed(frame, now):
    """Exclude partial/future candles and validate completed 15-minute bars."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    required = ("Open", "High", "Low", "Close", "Volume")
    if any(c not in frame.columns for c in required):
        return pd.DataFrame()
    if not isinstance(frame.index, pd.DatetimeIndex):
        return pd.DataFrame()
    if frame.index.tz is None:
        # Never interpret timezone-free prices as a verified current session.
        return pd.DataFrame()
    now = _as_ist(now)
    x = frame.copy()
    x.index = x.index.tz_convert(IST)
    x = x[~x.index.duplicated(keep="last")].sort_index()
    for col in required:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.dropna(subset=required)
    # Exchange timestamp is candle START; allow 60s publication delay.
    x = x[x.index + pd.Timedelta(minutes=_CANDLE_MIN + 1) <= now]
    x = x[(x["Close"] > 0) & (x["High"] >= x[["Open", "Close", "Low"]].max(axis=1))
          & (x["Low"] <= x[["Open", "Close", "High"]].min(axis=1))
          & (x["Volume"] >= 0)]
    x = x[(x.index.hour * 60 + x.index.minute >= 555)
          & (x.index.hour * 60 + x.index.minute <= 915)
          & (x.index.minute % _CANDLE_MIN == 0)]
    return x


def _indicators(closes):
    """15-minute EMA/RSI/ATR/ADX calculated on 150–200 closed bars."""
    high = closes["High"].astype(float)
    low = closes["Low"].astype(float)
    close = closes["Close"].astype(float)
    if not _MIN_HISTORY_BARS <= len(close) <= _LOOKBACK_BARS:
        return None
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-diff).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    smoothed_tr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / smoothed_tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    adx = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    result = dict(ema20=_finite(ema20.iloc[-1]),
                  ema50=_finite(ema50.iloc[-1]),
                  rsi=_finite(rsi.iloc[-1]),
                  atr=_finite(atr.iloc[-1]),
                  adx=_finite(adx.iloc[-1]),
                  plus_di=_finite(plus_di.iloc[-1]),
                  minus_di=_finite(minus_di.iloc[-1]))
    return result if all(v is not None for v in result.values()) else None


def evaluate_instrument(name, spot, etf, now):
    """One index decision. Current-session spot AND ETF required; no mock BUY."""
    now = _as_ist(now)
    if not market_session_open(now):
        return {"name": name, "status": "CLOSED",
                "reason": "Market closed or outside 09:46–15:15 IST signal window."}
    # Bound every indicator and volume reference to exactly the latest 200
    # real, timestamp-verified completed bars; never fabricate older prices.
    price = _completed(spot, now).tail(_LOOKBACK_BARS)
    volume = _completed(etf, now).tail(_LOOKBACK_BARS)
    if len(price) < _MIN_HISTORY_BARS or len(volume) < _MIN_HISTORY_BARS:
        return {"name": name, "status": "NO TRADE",
                "reason": f"Need 150–200 verified completed 15-minute candles; "
                          f"received spot {len(price)}, ETF {len(volume)}."}
    today = price[price.index.date == now.date()]
    etf_today = volume[volume.index.date == now.date()]
    if len(today) < 2 or len(etf_today) < 2:
        return {"name": name, "status": "NO TRADE",
                "reason": "Current NSE session not fully available; no old-session signals."}
    price_at, etf_at = today.index[-1], etf_today.index[-1]
    price_age = (now - (price_at + timedelta(minutes=_CANDLE_MIN))).total_seconds() / 60
    etf_age = (now - (etf_at + timedelta(minutes=_CANDLE_MIN))).total_seconds() / 60
    if (price_age < 1 or etf_age < 1 or
            price_age > _MAX_BAR_AGE_MIN or etf_age > _MAX_BAR_AGE_MIN or
            price_at != etf_at):
        return {"name": name, "status": "NO TRADE",
                "reason": "Latest completed 15-minute spot/ETF candles delayed "
                          "or not aligned (20-minute freshness cap)."}
    # Opening range is the real first 09:15–09:30 15-minute candle.
    opening = today[(today.index.hour == 9) & (today.index.minute == 15)]
    if len(opening) != 1:
        return {"name": name, "status": "NO TRADE",
                "reason": "Opening 09:15–09:30 15-minute candle missing."}
    if (etf_today["Volume"].iloc[-1] <= 0
            or (volume["Volume"].tail(21) <= 0).any()):
        return {"name": name, "status": "NO TRADE",
                "reason": "Missing real ETF trading volume for VWAP confirmation."}
    ind = _indicators(price)
    if ind is None:
        return {"name": name, "status": "NO TRADE",
                "reason": "RSI/EMA/ADX/ATR need more completed historical candles."}
    orb_hi = float(opening["High"].max())
    orb_lo = float(opening["Low"].min())
    last = float(today["Close"].iloc[-1])
    op = float(today["Open"].iloc[-1])
    atr = ind["atr"]
    # ETF is only a risk/volume proxy: its price is never substituted for
    # Nifty/BankNifty index entry, stop or target levels.
    typical = (etf_today["High"] + etf_today["Low"] + etf_today["Close"]) / 3
    vol = etf_today["Volume"]
    vwap = float((typical * vol).sum() / vol.sum())
    mean_volume = float(volume["Volume"].iloc[-21:-1].mean())
    if not (mean_volume > 0 and math.isfinite(vwap) and vwap > 0):
        return {"name": name, "status": "NO TRADE",
                "reason": "ETF VWAP or 20-bar volume reference unavailable."}
    relvol = float(vol.iloc[-1]) / mean_volume
    etf_close = float(etf_today["Close"].iloc[-1])
    buy = (
        last > orb_hi * 1.0005 and last > op
        and last > ind["ema20"] > ind["ema50"]
        and ind["plus_di"] > ind["minus_di"]
        and 54 <= ind["rsi"] <= 72
        and etf_close > vwap
    )
    sell = (
        last < orb_lo * 0.9995 and last < op
        and last < ind["ema20"] < ind["ema50"]
        and ind["minus_di"] > ind["plus_di"]
        and 28 <= ind["rsi"] <= 46
        and etf_close < vwap
    )
    # An index can be directional while the volume proxy is flat; that is
    # NOT an executable trade setup.
    if not (buy or sell) or ind["adx"] < _MIN_ADX or relvol < 1.2:
        return {"name": name, "status": "NO TRADE",
                "reason": "ORB, EMA trend, ADX ≥20, RSI and ETF VWAP/volume not all confirmed.",
                "asof": price_at.strftime("%d %b %H:%M IST")}
    direction = "BUY" if buy else "SELL"
    sign = 1 if buy else -1
    # Structured stop: last candle extreme plus ATR cushion, limited to
    # 1.5 ATR from the observation price. Do not manufacture a tighter stop.
    structure = (float(today["Low"].iloc[-1]) - 0.1 * atr if buy
                 else float(today["High"].iloc[-1]) + 0.1 * atr)
    fixed = last - sign * 1.5 * atr
    stop = min(structure, fixed) if buy else max(structure, fixed)
    risk = (last - stop) * sign
    risk_pct = 100 * risk / last
    if (risk <= 0 or risk_pct > _MAX_RISK_PCT or risk_pct < 0.08):
        return {"name": name, "status": "NO TRADE",
                "asof": price_at.strftime("%d %b %H:%M IST"),
                "reason": "Stop-loss distance outside 0.08%–0.90% allowed range."}
    score = (2 + 2 + (2 if ind["adx"] >= 25 else 1)
             + 1 + 1 + (1 if relvol >= 1.5 else 0) + 1)
    if score < 8:
        return {"name": name, "status": "NO TRADE",
                "asof": price_at.strftime("%d %b %H:%M IST"),
                "reason": "Technical quality score below 8/10; setup withheld."}
    target1, target2 = last + sign * 2 * risk, last + sign * 3 * risk
    return {"name": name, "status": "WATCH", "direction": direction,
            "entry": last, "sl": stop, "t1": target1, "t2": target2,
            "risk_pct": risk_pct, "score": score, "adx": ind["adx"],
            "rsi": ind["rsi"], "volume_multiple": relvol,
            "asof": price_at.strftime("%d %b %H:%M IST"),
            "bars_used": len(price),
            "source": "Yahoo 15m spot; NSE-listed ETF VWAP/volume proxy; completed bars",
            "reason": "15m opening range + EMA20/50 + ADX + RSI + "
                      "ETF VWAP/volume aligned."}


def format_intraday(now, decisions):
    now = _as_ist(now)
    lines = [
        "⚡ <b>MSK INTRADAY TRADE | NIFTY &amp; BANK NIFTY</b>",
        f"📅 {now.strftime('%d %b %Y')} | {now.strftime('%H:%M')} IST",
        "⏱️ 15-minute completed candles | 150–200 bars | 09:46–15:15 IST",
        "",
    ]
    if not market_session_open(now):
        lines.extend(["🔒 <b>MARKET CLOSED — NO LIVE SIGNALS</b>",
                      "Last completed market data is not converted into a new trade.",
                      "Use this button during NSE trading hours."])
    else:
        for item in decisions:
            lines.extend([f"<b>{html.escape(item['name'])}</b>"])
            if item.get("status") == "WATCH":
                lines.extend([
                    f"🟡 <b>SETUP WATCH — {item['direction']}</b> "
                    f"(not an executed order)",
                    f"Reference entry: {item['entry']:,.2f} | SL: {item['sl']:,.2f}",
                    f"T1: {item['t1']:,.2f} | T2: {item['t2']:,.2f}",
                    "R:R = 1:2.0 / 1:3.0 | "
                    f"Stop risk: {item['risk_pct']:.2f}%",
                    f"Setup quality: {item['score']}/10 (NOT win-rate accuracy)",
                    f"ADX: {item['adx']:.1f} | RSI: {item['rsi']:.1f} | "
                    f"ETF volume: {item['volume_multiple']:.2f}×",
                    f"Lookback: {item.get('bars_used', 'n/a')} verified 15m candles",
                    f"Data: {html.escape(item['asof'])} | "
                    f"{html.escape(item['source'])}",
                    html.escape(item["reason"]),
                    "Entry needs fresh quote/slippage and instrument confirmation.",
                ])
            else:
                lines.extend(["⛔ <b>NO TRADE</b>",
                              html.escape(item.get("reason", "No validated setup."))])
                if item.get("asof"):
                    lines.append(f"Last checked bar: {html.escape(item['asof'])}")
            lines.append("")
    lines.extend(["🧠 <b>RISK RULES</b>",
                  "Index levels are not option premiums. Verify the actual "
                  "trade instrument, spread, lot size and capital risk.",
                  "No live order placed; never treat a quality score as a "
                  "historical accuracy claim.",
                  "",
                  "⛔ <b>NO AUTOMATIC BUY / SELL ORDERS</b>",
                  "Educational research only; not a personal recommendation."])
    result = "\n".join(lines)
    if len(result) > 3600:
        raise ValueError("intraday_telegram_message_too_long")
    return result


def build_intraday_trade_report(*, now=None, fetcher=None):
    """Triggered only by explicit Telegram button/command, never on startup."""
    now = _as_ist(now)
    if not market_session_open(now):
        return format_intraday(now, [])
    provider = fetcher or _fetch_intraday_15m
    results = []
    for name, spot_sym, etf_sym in MARKETS:
        try:
            spot = provider(spot_sym, now)
            etf = provider(etf_sym, now)
            results.append(evaluate_instrument(name, spot, etf, now))
        except Exception as exc:
            LOG.warning("INTRADAY_NO_TRADE instrument=%s provider_failure=%s",
                        name, type(exc).__name__)
            results.append({"name": name, "status": "NO TRADE",
                            "reason": "Fresh spot/ETF source data unavailable; signal withheld."})
    return format_intraday(now, results)


_MEMBERS_URL = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
_EQUITY_SCAN_LOCK = threading.Lock()


def nifty200_members():
    response = requests.get(_MEMBERS_URL, timeout=8, allow_redirects=False)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    symbols = []
    for row in rows:
        symbol = (row.get("Symbol") or "").strip()
        if row.get("Series") != "EQ" or not re.fullmatch(r"[A-Z0-9&.-]{1,30}", symbol):
            raise ValueError("Unverified constituent row")
        symbols.append(symbol)
    if len(symbols) != 200 or len(set(symbols)) != 200:
        raise ValueError("Expected 200 distinct equity constituents")
    return symbols


def equity_bars(symbols):
    import yfinance as yf
    return yf.download([s + ".NS" for s in symbols], period="1mo", interval="15m",
                       group_by="ticker", auto_adjust=False, prepost=False,
                       ignore_tz=False, threads=4, progress=False, timeout=6)


def build_nifty200_report(*, clock=lambda: datetime.now(IST), members=nifty200_members,
                          download=equity_bars):
    now = _as_ist(clock())
    header = "⚡ <b>Nifty 200 intraday watchlist</b>"
    if not market_session_open(now):
        return header + "\nOutside the 09:46–15:15 IST signal window. No live setups."
    if not _EQUITY_SCAN_LOCK.acquire(blocking=False):
        return header + "\nA scan is in progress. Please retry shortly."
    try:
        try:
            symbols = members()
        except (requests.RequestException, ValueError, csv.Error):
            return header + "\nOfficial constituent list unavailable. Scan withheld."
        frames = {}
        for start in range(0, len(symbols), 25):
            batch = symbols[start:start + 25]
            try:
                bars = download(batch)
                for symbol in batch:
                    ticker = symbol + ".NS"
                    if ticker in bars.columns:
                        frames[symbol] = bars[ticker].dropna(how="all")
            except Exception:
                LOG.exception("Nifty 200 candle batch unavailable")
        now = _as_ist(clock())
        if not market_session_open(now):
            return header + "\nSignal window ended during the scan. Setups withheld."
        watches = []
        assessed = 0
        for symbol, frame in frames.items():
            # For cash equities price and traded volume belong to the same security.
            try:
                decision = evaluate_instrument(symbol, frame, frame, now)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                LOG.warning("Invalid intraday candles for %s", symbol)
                continue
            if decision.get("asof"):
                assessed += 1
            if decision["status"] == "WATCH":
                watches.append(decision)
        lines = [header, now.strftime("%d %b %Y · %H:%M IST"),
                 f"Usable data: {assessed}/{len(symbols)} stocks · completed 15m bars"]
        watches.sort(key=lambda s: (-s["score"], -s["volume_multiple"], s["name"]))
        for item in watches[:3]:
            lines.extend(["", f"<b>{html.escape(item['name'])} · {item['direction']} watch</b>",
                          f"Reference ₹{item['entry']:,.2f} · Stop ₹{item['sl']:,.2f} · T1 ₹{item['t1']:,.2f}",
                          f"Bar start: {item['asof']}"])
        if not watches:
            lines.append("No qualifying setup among assessed stocks." if assessed else
                         "Insufficient fresh price and volume data to assess setups.")
        lines.extend(["", "Research watchlist · Win rate unverified."])
        return "\n".join(lines)
    finally:
        _EQUITY_SCAN_LOCK.release()
