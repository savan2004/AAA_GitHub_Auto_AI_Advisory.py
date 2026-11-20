"""Complete Indian-equity market health: flows, positioning, options, global risk.

External evidence is optional and *never* substituted with guessed zeroes.
One NSE session per position/PCR, no cash-vs-futures unit mixing.
The base breadth snapshot is already validated by market_breadth.
"""
from __future__ import annotations

import csv
import html
import io
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

IST = timezone(timedelta(hours=5, minutes=30))
LOG = logging.getLogger("msk_market_health")
NSE_ROOT = "https://www.nseindia.com"
NSE_ARCHIVE = "https://nsearchives.nseindia.com/content/nsccl/"
NSE_FLOW_URL = NSE_ROOT + "/api/fiidiiTradeReact"
NSE_DERIVATIVES = "https://www.nseindia.com/all-reports-derivatives"
RBI_URL = "https://www.rbi.org.in/"
FED_URL = "https://www.federalreserve.gov/economy-at-a-glance-policy-rate.htm"
_MAX_AGE_CALENDAR_DAYS = 6
_SOURCE_TIMEOUT = 5
_POOL = ThreadPoolExecutor(max_workers=5, thread_name_prefix="msk-health")
_LOCK = threading.Lock()
_CACHED = None
_CACHE_AT = 0.0
_CACHE_TTL = 600


def _number(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        result = float(str(value).replace(",", "").strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _nse_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y",
                "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _read_json(url, *, referer=NSE_ROOT + "/reports/fii-dii"):
    """One ordinary public request. Never attempt to bypass access controls."""
    import requests
    response = requests.get(url, timeout=_SOURCE_TIMEOUT,
                            headers={"Accept": "application/json",
                                     "User-Agent": "MSKMarketHealth/1.0",
                                     "Referer": referer})
    response.raise_for_status()
    if len(response.content) > 2000000:
        raise ValueError("unexpected_market_payload_size")
    return response.json()


def _read_text(url):
    import requests
    response = requests.get(url, timeout=_SOURCE_TIMEOUT,
                            headers={"User-Agent": "MSKMarketHealth/1.0"})
    response.raise_for_status()
    if len(response.content) > 2000000:
        raise ValueError("unexpected_market_payload_size")
    return response.text


def parse_fii_cash(payload, session):
    """Validate NSE provisional cash buy/sell/net for both groups, same date."""
    if not isinstance(payload, list):
        return None
    matches = {}
    for row in payload:
        if not isinstance(row, dict) or _nse_day(row.get("date")) != session:
            continue
        category = str(row.get("category", "")).strip().upper()
        key = ("FII" if category in ("FII/FPI", "FII", "FPI") else
               "DII" if category == "DII" else None)
        if not key or key in matches:
            continue
        buy = _number(row.get("buyValue"))
        sell = _number(row.get("sellValue"))
        net = _number(row.get("netValue"))
        if any(n is None for n in (buy, sell, net)):
            continue
        # A zero net is valid ONLY if the source actually reports a matched
        # balanced market. Missing buy/sell is never replaced by zero.
        if buy <= 0 or sell <= 0 or abs(buy - sell - net) > 0.11:
            continue
        matches[key] = {"buy_cr": buy, "sell_cr": sell, "net_cr": net}
    if set(matches) != {"FII", "DII"}:
        return None
    return {"session": session.isoformat(), "fii": matches["FII"],
            "dii": matches["DII"], "source": "NSE provisional cash trades"}


def fetch_fii_cash(session):
    try:
        return parse_fii_cash(_read_json(NSE_FLOW_URL), session)
    except Exception as exc:
        LOG.warning("MSK_HEALTH_CASH_UNAVAILABLE reason=%s", type(exc).__name__)
        return None


def parse_participant_oi(raw, session):
    """Extract FII *index futures* longs/shorts, never combine options OI."""
    if not isinstance(raw, str) or len(raw) > 200000:
        return None
    rows = list(csv.reader(io.StringIO(raw.lstrip("\ufeff"))))
    header = None
    index = None
    for i, row in enumerate(rows):
        titles = [str(col).strip().lower().replace("  ", " ") for col in row]
        if all(name in titles for name in (
                "client type", "future index long", "future index short")):
            header, index = titles, i
            break
    if header is None:
        return None
    selected = []
    for row in rows[index + 1:]:
        if len(row) != len(header):
            continue
        values = dict(zip(header, row))
        if values["client type"].strip().upper() == "FII":
            selected.append(values)
    if len(selected) != 1:
        return None
    row = selected[0]
    long = _number(row["future index long"])
    short = _number(row["future index short"])
    if long is None or short is None or long < 0 or short < 0 or long + short <= 0:
        return None
    return {"session": session.isoformat(), "long": int(long),
            "short": int(short), "net": int(long - short),
            "long_share": round(100 * long / (long + short), 1),
            "ls_ratio": round(long / short, 2) if short else None,
            "source": "NSE F&O participant-wise open interest",
            "scope": "All index futures combined (not Nifty-only)"}


def fetch_participant_oi(session):
    url = NSE_ARCHIVE + f"fao_participant_oi_{session:%d%m%Y}.csv"
    try:
        return parse_participant_oi(_read_text(url), session)
    except Exception as exc:
        LOG.warning("MSK_HEALTH_OI_UNAVAILABLE reason=%s", type(exc).__name__)
        return None


def parse_option_pcr(payload, session, expiry):
    """Nifty selected-expiry OI PCR; no mixed expiries, no 0/0 fabricated PCR."""
    if not isinstance(payload, dict) or not isinstance(expiry, str):
        return None
    record = payload.get("records") or {}
    if not isinstance(record, dict):
        return None
    raw_ts = record.get("timestamp")
    obs = _nse_day(raw_ts)
    if obs != session:
        return None
    rows = record.get("data") or payload.get("data")
    if isinstance(rows, dict):
        rows = rows.get("data")
    if not isinstance(rows, list):
        return None
    put = call = 0
    strikes = set()
    for item in rows:
        if not isinstance(item, dict) or str(item.get("expiryDate", "")) != expiry:
            continue
        ce, pe = item.get("CE"), item.get("PE")
        if not isinstance(ce, dict) or not isinstance(pe, dict):
            continue
        coi, poi = _number(ce.get("openInterest")), _number(pe.get("openInterest"))
        if coi is None or poi is None or coi < 0 or poi < 0:
            continue
        strike = _number(item.get("strikePrice"))
        if strike is None or strike <= 0:
            continue
        if strike in strikes:
            return None
        strikes.add(strike)
        call += coi
        put += poi
    if len(strikes) < 10 or call <= 0 or put <= 0:
        return None
    return {"session": session.isoformat(), "expiry": expiry, "pcr": round(put / call, 2),
            "put_oi": int(put), "call_oi": int(call), "strikes": len(strikes),
            "source": "NSE NIFTY option OI (selected expiry)"}


def fetch_option_pcr(session):
    """Use public NSE endpoints only if directly available; 401/403 = unavailable.

    During Indian cash-market hours the PCR can be newer than last complete
    *daily breadth*, so label its own intraday observation date.
    """
    now = datetime.now(IST)
    obs_day = session
    if (now.weekday() < 5 and
            (9, 15) <= (now.hour, now.minute) <= (15, 30) and
            now.date() > session):
        obs_day = now.date()
    try:
        info = _read_json(
            NSE_ROOT + "/api/option-chain-contract-info?symbol=NIFTY",
            referer=NSE_ROOT + "/option-chain")
        if not isinstance(info, dict):
            return None
        options = (info.get("expiryDates") or
                   (info.get("records") or {}).get("expiryDates") or
                   (info.get("data") or {}).get("expiryDates") or [])
        valid_expiries = sorted(((_nse_day(s), s) for s in options
                                 if _nse_day(s) is not None and _nse_day(s) >= obs_day))
        if not valid_expiries:
            return None
        expiry = valid_expiries[0][1]
        url = NSE_ROOT + "/api/option-chain-v3?" + urlencode(
            {"type": "Indices", "symbol": "NIFTY", "expiry": expiry})
        return parse_option_pcr(_read_json(url, referer=NSE_ROOT + "/option-chain"),
                                obs_day, expiry)
    except Exception as exc:
        LOG.warning("MSK_HEALTH_PCR_UNAVAILABLE reason=%s", type(exc).__name__)
        return None


def parse_official_rate(page, label):
    """Conservative source-specific text extraction; no fallback 'last known' rate."""
    if not isinstance(page, str) or len(page) < 300:
        return None
    text = html.unescape(re.sub(r"<[^>]*>", " ", page))
    text = re.sub(r"\s+", " ", text)
    if label == "rbi":
        m = re.search(r"Policy\s+Repo\s+Rate\s*:?\s*(\d{1,2}(?:\.\d+)?)\s*%",
                      text, re.IGNORECASE)
        if not m:
            return None
        rate = _number(m.group(1))
        if rate is None or not 0 < rate < 30:
            return None
        return {"rate": rate, "source": "RBI current policy rates"}
    if label == "fed":
        # Fed's current policy-rate page includes a dedicated range field.
        at = text.lower().find("federal funds rate target range")
        if at < 0:
            at = text.lower().find("target range for the federal funds rate")
        if at < 0:
            return None
        excerpt = text[at:at + 700]
        m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:to|–|-)\s*"
                      r"(\d{1,2}(?:\.\d+)?)\s*%", excerpt, re.IGNORECASE)
        if not m:
            return None
        lo, hi = _number(m.group(1)), _number(m.group(2))
        if lo is None or hi is None or not 0 <= lo < hi < 30:
            return None
        return {"lower": lo, "upper": hi, "source": "US Federal Reserve"}
    return None


def fetch_policy_rates(_session):
    output = {}
    for key, url in (("rbi", RBI_URL), ("fed", FED_URL)):
        try:
            rate = parse_official_rate(_read_text(url), key)
            if rate:
                output[key] = rate
        except Exception as exc:
            LOG.warning("MSK_HEALTH_RATE_UNAVAILABLE source=%s reason=%s",
                        key, type(exc).__name__)
    return output or None


def parse_commodity_history(dataframe, session, *, ticker="BZ=F"):
    """Last completed Brent futures close + 5-session change, in USD/bbl."""
    if dataframe is None or getattr(dataframe, "empty", True):
        return None
    try:
        # Direct rows only; no filled/estimated closes or future observations.
        rows = []
        for at, row in dataframe.iterrows():
            day = at.date() if hasattr(at, "date") else _nse_day(at)
            close = _number(row["Close"])
            if day and day <= session and close and close > 0:
                rows.append((day, close))
        rows.sort()
        if not rows or (session - rows[-1][0]).days > _MAX_AGE_CALENDAR_DAYS:
            return None
        recent = rows[-1]
        older = rows[-6] if len(rows) >= 6 else None
        return {"session": recent[0].isoformat(), "price": round(recent[1], 2),
                "change_5d": round((recent[1] / older[1] - 1) * 100, 2) if older else None,
                "source": "Yahoo Finance Brent front-month futures (BZ=F)",
                "ticker": ticker}
    except (KeyError, ValueError, TypeError, OverflowError, IndexError):
        return None


def _fetch_yahoo_series(ticker, session):
    import yfinance as yf
    # Non-index symbols are never routed through the .NS equity suffix mapper.
    frame = yf.Ticker(ticker).history(period="1mo", interval="1d",
                                     auto_adjust=False, timeout=5)
    return parse_commodity_history(frame, session, ticker=ticker)


def fetch_brent(session):
    try:
        return _fetch_yahoo_series("BZ=F", session)
    except Exception as exc:
        LOG.warning("MSK_HEALTH_BRENT_UNAVAILABLE reason=%s", type(exc).__name__)
        return None


def fetch_usdinr(session):
    try:
        result = _fetch_yahoo_series("INR=X", session)
        if result and 20 < result["price"] < 200:
            result["source"] = "Yahoo Finance USD/INR (INR=X)"
            return result
    except Exception as exc:
        LOG.warning("MSK_HEALTH_FX_UNAVAILABLE reason=%s", type(exc).__name__)
    return None


def get_factors(session):
    """A bounded best-effort refresh, with one 10-minute cache shared by clients."""
    global _CACHED, _CACHE_AT
    if not isinstance(session, date) or isinstance(session, datetime):
        raise ValueError("invalid_market_session")
    now = time.monotonic()
    with _LOCK:
        if _CACHED and _CACHED.get("session") == session.isoformat() and now - _CACHE_AT < _CACHE_TTL:
            return dict(_CACHED)
    jobs = {
        "cash": _POOL.submit(fetch_fii_cash, session),
        "futures": _POOL.submit(fetch_participant_oi, session),
        "pcr": _POOL.submit(fetch_option_pcr, session),
        "rates": _POOL.submit(fetch_policy_rates, session),
        "brent": _POOL.submit(fetch_brent, session),
        "fx": _POOL.submit(fetch_usdinr, session),
    }
    done, pending = wait(jobs.values(), timeout=9)
    values = {"session": session.isoformat()}
    for name, job in jobs.items():
        if job in done:
            try:
                result = job.result()
                if result:
                    values[name] = result
            except Exception as exc:
                LOG.warning("MSK_HEALTH_FACTOR_FAILED factor=%s reason=%s",
                            name, type(exc).__name__)
        else:
            job.cancel()
            LOG.warning("MSK_HEALTH_FACTOR_TIMEOUT factor=%s", name)
    with _LOCK:
        _CACHED, _CACHE_AT = dict(values), time.monotonic()
    return values


def _crore(value):
    v = _number(value)
    return f"{v:+,.0f}" if v is not None else "unavailable"


def format_market_health(breadth, factors, *, now=None):
    """Investor-first diagnosis with unambiguous dated figures and no invented data."""
    if not isinstance(breadth, dict) or breadth.get("state") in ("warming", "unavailable"):
        from market_breadth import format_breadth
        return format_breadth(breadth if isinstance(breadth, dict) else
                              {"state": "unavailable"})
    day = _nse_day(breadth.get("session"))
    if day is None or factors.get("session") != day.isoformat():
        return "⚠️ <b>Market Health unavailable</b>\nMarket sessions do not match."
    valid = _number(breadth.get("valid"))
    fallen = _number(breadth.get("declines"))
    rising = _number(breadth.get("advances"))
    ma20 = _number((breadth.get("ma20") or {}).get("pct"))
    benchmark = _number(breadth.get("benchmark_change"))
    unchanged = _number(breadth.get("unchanged"))
    requested = _number(breadth.get("requested"))
    if (valid is None or valid <= 0 or fallen is None or rising is None
            or fallen < 0 or rising < 0 or fallen + rising > valid
            or (unchanged is not None and (unchanged < 0 or fallen + rising + unchanged != valid))
            or (requested is not None and requested < valid)
            or ma20 is None or not 0 <= ma20 <= 100):
        return "⚠️ <b>Market Health unavailable</b>\nMarket participation incomplete or inconsistent."
    falling_share = fallen / valid * 100
    # Breadth is the only source of global posture when derivatives are absent.
    posture = ("🟠 CAUTIOUS" if falling_share >= 60 and ma20 >= 50 else
               "🔴 DEFENSIVE" if falling_share >= 60 and ma20 < 50 else
               "🟢 CONSTRUCTIVE" if rising / valid >= 0.6 and ma20 >= 50 else
               "🟡 MIXED")
    lines = [
        "🩺 <b>MSK TOTAL MARKET HEALTH | INDIA</b>",
        f"📅 {day.strftime('%d %b %Y')} closing session",
        f"📌 <b>Verdict: {posture}</b>",
        "Verdict based on Nifty 500 participation and its 20-day trend.",
        "",
        "💰 <b>INSTITUTIONAL CASH FLOW (₹ crore)</b>",
    ]
    cash = factors.get("cash")
    if cash and cash.get("session") == day.isoformat():
        lines.extend([
            f"FII/FPI net: ₹{_crore(cash['fii']['net_cr'])} Cr",
            f"DII net: ₹{_crore(cash['dii']['net_cr'])} Cr",
            "NSE provisional cash trades. Cash flow ≠ futures direction.",
        ])
    else:
        lines.append("FII/DII: Unavailable — no valid same-session figures.")
    lines.extend(["", "🧭 <b>FII FUTURES & OPTIONS</b>"])
    fut = factors.get("futures")
    if fut and fut.get("session") == day.isoformat():
        lines.append(f"FII index futures: {fut['long']:,} long / "
                     f"{fut['short']:,} short")
        lines.append(f"Net: {fut['net']:+,} contracts | "
                     f"Long share: {fut['long_share']:.1f}%")
        lines.append("All index futures combined, not Nifty-only.")
    else:
        lines.append("FII index-futures positioning: Unavailable.")
    pcr = factors.get("pcr")
    pcr_day = _nse_day(pcr.get("session")) if isinstance(pcr, dict) else None
    today = (now or datetime.now(IST)).astimezone(IST).date()
    pcr_value = _number(pcr.get("pcr")) if isinstance(pcr, dict) else None
    if (pcr_day is not None and (pcr_day == day or day < pcr_day == today)
            and pcr_day <= today and pcr_value is not None and pcr_value > 0):
        lines.append(f"Nifty OI PCR: {pcr_value:.2f} "
                     f"(as of {pcr_day.isoformat()}, "
                     f"expiry {html.escape(pcr['expiry'])}; "
                     f"{pcr['strikes']} paired strikes)")
        if pcr_day > day:
            lines.append("Today's option-chain observation is newer than the breadth close.")
        lines.append("PCR = put OI / call OI; not a directional guarantee.")
    else:
        lines.append("Nifty put-call OI ratio: Unavailable (not treated as zero).")
    lines.extend(["", "🌍 <b>MACRO & GLOBAL PRESSURE</b>"])
    rates = factors.get("rates") or {}
    if rates.get("fed"):
        lines.append(f"US Fed target range: {rates['fed']['lower']:.2f}–"
                     f"{rates['fed']['upper']:.2f}% (current posted policy).")
    else:
        lines.append("US Fed rate: Latest source update unavailable.")
    if rates.get("rbi"):
        lines.append(f"RBI repo rate: {rates['rbi']['rate']:.2f}% "
                     "(current posted policy).")
    else:
        lines.append("RBI repo rate: Latest source update unavailable.")
    brent = factors.get("brent")
    if brent:
        move = (_number(brent.get("change_5d")))
        week = f" | 5 sessions: {move:+.1f}%" if move is not None else ""
        lines.append(f"Brent futures: ${brent['price']:.2f}/bbl "
                     f"({brent['session']}){week}.")
        lines.append("Higher sustained oil prices can pressure India’s import costs.")
    else:
        lines.append("Brent crude futures: Recent dated quote unavailable.")
    fx = factors.get("fx")
    if fx:
        move = _number(fx.get("change_5d"))
        week = f" | 5 sessions: {move:+.1f}%" if move is not None else ""
        lines.append(f"USD/INR: ₹{fx['price']:.2f}/USD ({fx['session']}){week}.")
        lines.append("Rising USD/INR generally signals a weaker rupee.")
    else:
        lines.append("USD/INR: Recent dated quote unavailable.")

    lines.extend(["", "📊 <b>DOMESTIC PARTICIPATION</b>"])
    if benchmark is not None:
        lines.append(f"Nifty 50: {benchmark:+.2f}%")
    lines.append(f"Nifty 500: {falling_share:.1f}% declining | "
                 f"{100*rising/valid:.1f}% advancing")
    lines.append(f"20-day trend breadth: {ma20:.1f}% above average")
    delta = _number(breadth.get("trend_delta"))
    if delta is not None:
        lines.append(f"5-session change in advance share: {delta:+.1f} pp")
    lines.extend(["", "🎯 <b>WHAT TO WATCH NEXT</b>"])
    if falling_share >= 60:
        lines.append("Market recovery needs broader advancing stocks; one "
                     "strong index candle is not sufficient.")
    else:
        lines.append("Look for repeated breadth improvement and agreement "
                     "between cash flows and futures positioning.")
    lines.append("Track FII futures net position, Nifty PCR, Brent and "
                 "USD/INR before increasing market-wide risk.")
    lines.append(f"✅ Breadth coverage: {int(valid)}/{int(breadth.get('requested') or valid)} "
                 f"({breadth.get('coverage')}%).")
    lines.append("Dated source figures only • Educational market context.")
    output = "\n".join(lines)
    if len(output) > 3900:
        raise ValueError("market_health_over_telegram_limit")
    return output


def build_market_health():
    from market_breadth import get_breadth
    breadth = get_breadth()
    if breadth.get("state") in ("warming", "unavailable"):
        return format_market_health(breadth, {})
    day = _nse_day(breadth.get("session"))
    if day is None:
        raise ValueError("invalid_breadth_session")
    return format_market_health(breadth, get_factors(day))
