"""Completed-session breadth. Pure calculations are separate from data access."""
import csv
import io
import html
import logging
import os
import threading
import time
from datetime import datetime, time as daytime, timedelta, timezone

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
LOG = logging.getLogger("msk_market_breadth")
INDEX_LIST = 'https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv'
_lock = threading.Lock()
_cache = None
_cache_at = 0.0


def expected_completed_session(now=None):
    """Strict NSE weekday freshness gate; holidays conservatively fail closed.

    Daily OHLCV bars are usable only after 16:00 IST. Before the close, use
    the preceding weekday. Never treat a multi-day-old quote as today's quote.
    """
    stamp = now or datetime.now(IST)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    stamp = stamp.astimezone(IST)
    day = stamp.date()
    if stamp.time() < daytime(16):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def completed_prices(frame, now=None):
    """Remove intraday bars; 16:00 IST provides a post-close publication buffer."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_convert(IST).tz_localize(None)
    out.index = idx.normalize()
    out = out.loc[~out.index.duplicated(keep='last')].sort_index()
    limit = now.date() if now.time() >= daytime(16) else now.date() - timedelta(days=1)
    return out.loc[out.index.date <= limit]


def _daily(hist, symbols, session, previous, sectors):
    rows = []
    for sym in symbols:
        df = hist.get(sym)
        if df is None or 'Close' not in df:
            continue
        c = pd.to_numeric(df.loc[:session, 'Close'], errors='coerce')
        if session not in c.index or previous not in c.index:
            continue
        if not pd.notna(c.loc[session]) or not pd.notna(c.loc[previous]):
            continue
        last, prev = float(c.loc[session]), float(c.loc[previous])
        if last <= 0 or prev <= 0:
            continue
        row = {'symbol': sym, 'sector': sectors.get(sym) or 'Unclassified',
               'change': (last / prev - 1) * 100}
        for n in (20, 50, 200):
            window = c.iloc[-n:]
            row[f'above{n}'] = bool(last > window.mean()) if len(window) == n and window.notna().all() else None
        window = c.iloc[-252:]
        valid = len(window) == 252 and window.notna().all()
        row['high'] = bool(last >= window.max()) if valid else None
        row['low'] = bool(last <= window.min()) if valid else None
        rows.append(row)
    adv = sum(r['change'] > 0.005 for r in rows)
    dec = sum(r['change'] < -0.005 for r in rows)
    out = {'session': str(session.date()), 'valid': len(rows), 'advances': adv,
           'declines': dec, 'unchanged': len(rows)-adv-dec,
           'ad_ratio': round(adv/dec, 2) if dec else None, 'sectors': {}}
    for n in (20, 50, 200):
        vals = [r[f'above{n}'] for r in rows if r[f'above{n}'] is not None]
        out[f'ma{n}'] = {'count': len(vals), 'pct': round(sum(vals)/len(vals)*100, 1) if vals else None}
    vals = [r for r in rows if r['high'] is not None]
    out.update(highs=sum(r['high'] for r in vals), lows=sum(r['low'] for r in vals), high_low_count=len(vals))
    for sec in sorted(set(r['sector'] for r in rows)):
        part = [r for r in rows if r['sector'] == sec]
        out['sectors'][sec] = {'count': len(part), 'adv_pct': round(sum(r['change'] > .005 for r in part)/len(part)*100, 1)}
    return out, rows


def calculate_breadth(hist, symbols, benchmark, sectors=None, now=None):
    symbols = list(dict.fromkeys(symbols))
    bench = completed_prices(benchmark, now)
    if bench.empty or len(bench) < 2:
        raise ValueError('Completed benchmark sessions unavailable; breadth cannot be aligned.')
    hist = {s: completed_prices(f, now).reindex(bench.index) for s, f in hist.items() if f is not None}
    sessions = bench.index[-6:]
    latest, rows = _daily(hist, symbols, sessions[-1], sessions[-2], sectors or {})
    latest.update(requested=len(symbols), coverage=round(latest['valid']/len(symbols)*100, 1) if symbols else 0,
                  benchmark_change=None, trend_delta=None, trend_count=0)
    bc = pd.to_numeric(bench['Close'], errors='coerce')
    if pd.notna(bc.iloc[-1]) and pd.notna(bc.iloc[-2]) and bc.iloc[-2] > 0:
        latest['benchmark_change'] = round((bc.iloc[-1]/bc.iloc[-2]-1)*100, 2)
    # Fixed cohort avoids comparing percentages with changing stock coverage.
    if len(sessions) == 6:
        cohort = [s for s in symbols if s in hist and 'Close' in hist[s] and
                  hist[s]['Close'].reindex(sessions).notna().all() and
                  (hist[s]['Close'].reindex(sessions) > 0).all()]
        series = [_daily(hist, cohort, sessions[i], sessions[i-1], {})[0] for i in range(1, 6)]
        if cohort:
            vals = [x['advances']/x['valid']*100 for x in series]
            latest.update(trend_delta=round(vals[-1]-vals[0], 1), trend_count=len(cohort))
    expected_day = expected_completed_session(now)
    # Previous code allowed up to seven calendar days and could present a
    # Friday close as Monday's current market. Exchange holidays fail closed.
    stale = sessions[-1].date() != expected_day
    latest['stale'] = stale
    latest['expected_session'] = str(expected_day)
    latest['trend_context'] = None
    latest['interpretation'] = 'Stale session: no current market-wide conclusion' if stale else 'Insufficient coverage for a market-wide conclusion'
    if not stale and latest['coverage'] >= 80 and latest['valid']:
        ap = latest['advances']/latest['valid']*100
        idx = latest['benchmark_change']
        ma20 = latest['ma20']['pct']
        ma50 = latest['ma50']['pct']
        weak_sma = ma20 is not None and ma50 is not None and ma20 < 40 and ma50 < 40
        weak_highs = latest['high_low_count'] >= max(20, latest['valid'] * 0.8) and latest['lows'] > latest['highs'] * 2
        if idx is not None and idx > 0 and ap < 40:
            latest['interpretation'] = 'Index rising, but most covered stocks are not participating'
        elif ap >= 60:
            latest['interpretation'] = 'Broad positive DAILY participation in the covered universe'
        elif ap <= 40:
            latest['interpretation'] = 'Broad negative DAILY participation in the covered universe'
        else:
            latest['interpretation'] = 'Mixed daily participation; no broad directional agreement'
        if ap >= 60 and (weak_sma or weak_highs):
            evidence = []
            if weak_sma:
                evidence.append(f"only {ma20:.1f}% above 20D and {ma50:.1f}% above 50D SMA")
            if weak_highs:
                evidence.append(f"{latest['highs']} closing highs vs {latest['lows']} closing lows")
            latest['trend_context'] = ('Daily rebound in participation, but weak underlying trend: ' +
                                       '; '.join(evidence) +
                                       '. This is NOT a confirmed medium-term reversal.')
        elif ap <= 40 and not weak_sma and not weak_highs:
            latest['trend_context'] = ('Weak daily participation alone does not establish a persistent downtrend.')
    return latest


def load_universe():
    """Never mislabel a liquidity universe or built-in sample as Nifty 500."""
    try:
        r = requests.get(INDEX_LIST, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        mapping = {x.get('Symbol', '').strip(): x.get('Industry', '').strip() for x in rows}
        mapping = {s: sec for s, sec in mapping.items() if s and all(c.isalnum() or c in '&-' for c in s)}
        if 450 <= len(mapping) <= 550:
            return list(mapping), mapping, 'Nifty 500 constituent CSV (current membership)', False
    except (requests.RequestException, ValueError):
        pass
    from nifty500_collector import SECTOR_STOCKS
    mapping = {s: sec for sec, syms in SECTOR_STOCKS.items() for s in syms}
    return list(mapping), mapping, 'Built-in stock sample; NOT full Nifty 500', True


def _valid_daily_snapshot(report, expected):
    """Reject corrupted, incomplete or stale database rows before display."""
    if not isinstance(report, dict) or report.get("session") != expected:
        return False
    if report.get("stale") is not False or report.get("source") != "Nifty 500 constituent CSV (current membership)":
        return False
    required = ("requested", "valid", "coverage", "advances", "declines",
                "unchanged", "sectors", "ma20", "ma50", "ma200",
                "highs", "lows", "high_low_count", "interpretation", "fetched")
    if not all(key in report for key in required):
        return False
    try:
        requested = int(report["requested"])
        valid = int(report["valid"])
        coverage = float(report["coverage"])
        if not (450 <= requested <= 550 and 0 <= valid <= requested
                and coverage >= 80
                and abs(coverage - round(100 * valid / requested, 1)) < 0.11):
            return False
        return (sum(int(report[k]) for k in ("advances", "declines", "unchanged"))
                == valid and isinstance(report["sectors"], dict)
                and all(isinstance(report[k], dict) for k in ("ma20", "ma50", "ma200")))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return False


def _breadth_db_url():
    """Research DB only; never read or replace the separate portfolio storage."""
    value = os.environ.get("MSK_INDEX_DATABASE_URL", "")
    if value.startswith(("postgresql://", "postgres://")) and "sslmode=require" in value:
        return value
    return None


def _load_daily_snapshot(expected):
    url = _breadth_db_url()
    if url is None:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=3) as conn:
            # SET LOCAL works inside the transaction even through Neon pooler.
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            row = conn.execute(
                "SELECT body FROM public.msk_breadth_snapshots_v1 "
                "WHERE session_date = %s", (expected,)
            ).fetchone()
        report = row[0] if row else None
        if _valid_daily_snapshot(report, expected):
            LOG.info("MSK_BREADTH_DB_READY session=%s coverage=%s",
                     expected, report["coverage"])
            return report
        return None
    except Exception as exc:
        LOG.warning("MSK_BREADTH_DB_READ_FAILED type=%s", type(exc).__name__)
        return None


def persist_daily_snapshot(report):
    """Atomic independent Neon record: never modify index Top5 or portfolio tables."""
    expected = str(expected_completed_session())
    if not _valid_daily_snapshot(report, expected):
        raise ValueError("Only complete verified current-session breadth may be stored")
    url = _breadth_db_url()
    if url is None:
        raise RuntimeError("Dedicated research database connection unavailable")
    import psycopg
    from psycopg.types.json import Jsonb
    with psycopg.connect(url, connect_timeout=8) as conn:
        conn.execute("SET LOCAL statement_timeout = '12000ms'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.msk_breadth_snapshots_v1 ("
            "session_date DATE PRIMARY KEY, "
            "coverage NUMERIC(5,1) NOT NULL, "
            "body JSONB NOT NULL, "
            "stored_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        conn.execute(
            "INSERT INTO public.msk_breadth_snapshots_v1 "
            "(session_date,coverage,body) VALUES (%s,%s,%s) "
            "ON CONFLICT (session_date) DO UPDATE SET "
            "coverage=EXCLUDED.coverage,body=EXCLUDED.body,stored_at=NOW()",
            (expected, report["coverage"], Jsonb(report))
        )
    LOG.info("MSK_BREADTH_DB_SAVED session=%s coverage=%s valid=%s",
             expected, report["coverage"], report["valid"])


def _collect_breadth():
    """Collect a VERIFIED completed-session snapshot. Never run in request thread."""
    from market_data import bulk_history, index_history
    syms, sectors, source, degraded = load_universe()
    if degraded:
        LOG.error("MSK_BREADTH_FAILED reason=official_nifty500_unavailable source=%s",source)
        raise ValueError("Official Nifty 500 CSV unavailable; built-in sample blocked")
    if not 450 <= len(syms) <= 550:
        raise ValueError("Nifty 500 member count invalid; breadth withheld")
    # Bulk only: the old 120-symbol sequential NSE/Yahoo/Stooq fallback could
    # spend minutes on 401/invalid symbols and time out at 170 seconds.
    # Never create or substitute prices for unavailable symbols.
    hist = bulk_history(syms, '2y', chunk_size=250, cache_ttl=900,
                        adjusted=True, retries=0, max_fallbacks=0,
                        download_timeout=20)
    if len(hist) < len(syms) * 0.8:
        missing = [sym for sym in syms if sym not in hist]
        LOG.warning("MSK_BREADTH_RETRY missing=%d retrieved=%d total=%d",
                    len(missing),len(hist),len(syms))
        # Exactly one bounded retry for missing symbols, without individual
        # provider fallbacks that held the old single-threaded scan open.
        recovered = bulk_history(missing, '2y', chunk_size=250, cache_ttl=900,
                                 adjusted=True, retries=0, max_fallbacks=0,
                                 download_timeout=20)
        hist.update(recovered)
    benchmark = index_history('^NSEI', '2y', retries=0, timeout=20)
    if benchmark is None or benchmark.empty:
        LOG.warning("MSK_BREADTH_RETRY benchmark=NSEI")
        benchmark = index_history('^NSEI', '2y', retries=0, timeout=20)
    if benchmark is None or benchmark.empty:
        raise ValueError("Nifty benchmark price provider unavailable after retry")
    LOG.info("MSK_BREADTH_FETCH fetched=%d requested=%d benchmark_latest=%s",
             len(hist),len(syms),str(benchmark.index[-1]))
    result = calculate_breadth(hist, syms, benchmark, sectors)
    if result['stale']:
        raise ValueError(f"Latest verified NSE session {result['session']} is not expected "
                         f"session {result['expected_session']}; no stale breadth report.")
    if result['coverage'] < 80:
        raise ValueError(f"Insufficient Nifty basket quote coverage "
                         f"({result['coverage']}%); report withheld.")
    result.update(source=source, degraded=degraded, fetched=datetime.now(IST).strftime('%d-%b %H:%M IST'))
    if degraded:
        result['interpretation'] = 'Sample only: ' + result['interpretation']
    return result


# This dedicated worker is the ONLY owner of long network downloads. A Telegram
# request never joins it or holds the cache lock during a provider call.
_refresh_thread = None
_last_error = None
_next_retry_at = 0.0


def _refresh_breadth():
    global _cache, _cache_at, _refresh_thread, _last_error, _next_retry_at
    try:
        report = _collect_breadth()
        with _lock:
            _cache, _cache_at = report, time.time()
            _last_error, _next_retry_at = None, 0.0
        try:
            if _breadth_db_url():
                persist_daily_snapshot(report)
        except Exception as exc:
            LOG.warning("MSK_BREADTH_DB_WRITE_FAILED type=%s", type(exc).__name__)
        LOG.info("MSK_BREADTH_READY session=%s coverage=%s valid=%s",
                 report["session"], report["coverage"], report["valid"])
    except Exception as exc:
        # Failed scans are never cached as valid reports.
        with _lock:
            _last_error = type(exc).__name__
            _next_retry_at = time.monotonic() + 180
        LOG.exception("MSK_BREADTH_REFRESH_FAILED type=%s", type(exc).__name__)
    finally:
        with _lock:
            _refresh_thread = None


def get_breadth():
    """Get a verified completed-session report without waiting on 500 symbols."""
    global _refresh_thread, _cache, _cache_at
    expected = str(expected_completed_session())
    with _lock:
        report = _cache
        if _valid_daily_snapshot(report, expected):
            return report

    # Reuse the completed report across Render restarts. Database access has a
    # strict time budget and never holds the worker lock.
    persisted = _load_daily_snapshot(expected)
    if persisted is not None:
        with _lock:
            _cache, _cache_at = persisted, time.time()
        return persisted

    with _lock:
        if _refresh_thread is None and time.monotonic() >= _next_retry_at:
            _refresh_thread = threading.Thread(
                target=_refresh_breadth, name="msk-breadth-collector", daemon=True)
            _refresh_thread.start()
        if _refresh_thread is not None:
            return {"state": "warming", "expected_session": expected}
        return {"state": "unavailable", "expected_session": expected,
                "error": _last_error or "Provider unavailable"}


def format_breadth(r):
    """Explain Nifty 500 participation as a decision aid, not a trade signal.

    Uses only stored completed-session figures. The sector list requires a
    meaningful sample and is never described as a stock recommendation.
    """
    if r.get("state") == "warming":
        return ("⏳ <b>Market Health preparing</b>\n"
                "The latest completed-session figures are not available yet. "
                "No unverified market reading is shown.")
    if r.get("state") == "unavailable":
        return ("⚠️ <b>Market Health unavailable</b>\n"
                "Latest completed-session coverage cannot be verified. "
                "No stale reading is shown.")

    def number(v):
        try:
            f = float(v)
            return f if f == f and abs(f) != float("inf") else None
        except (ValueError, TypeError, OverflowError):
            return None

    def pct(v):
        n = number(v)
        return f"{n:.1f}%" if n is not None else "not available"

    valid = number(r.get("valid"))
    adv = number(r.get("advances"))
    dec = number(r.get("declines"))
    unchanged = number(r.get("unchanged"))
    if (not valid or valid <= 0 or adv is None or dec is None
            or unchanged is None or adv < 0 or dec < 0
            or adv + dec + unchanged != valid):
        return "⚠️ <b>Market Health unavailable</b>\nIncomplete participation data."

    rising = 100 * adv / valid
    falling = 100 * dec / valid
    ma20, ma50, ma200 = (r.get(f"ma{n}") or {} for n in (20, 50, 200))
    share20 = number(ma20.get("pct"))
    share50 = number(ma50.get("pct"))
    share200 = number(ma200.get("pct"))
    trend_delta = number(r.get("trend_delta"))
    benchmark = number(r.get("benchmark_change"))
    weak_day, strong_day = rising < 40, rising > 60
    short_breadth_ok = share20 is not None and share20 >= 50

    if weak_day and short_breadth_ok:
        stance = "🟠 <b>CAUTIOUS — daily selling; short-term trend holding</b>"
        meaning = ("Most stocks fell in the last session, but most remain "
                   "above their 20-day average. Selling pressure is real; "
                   "a lasting downtrend is NOT confirmed.")
        new_money = ("Prefer selective or staggered entries. Avoid buying "
                     "simply because prices fell.")
    elif weak_day:
        stance = "🔴 <b>DEFENSIVE — broad selling and weak short-term breadth</b>"
        meaning = ("Most stocks fell and fewer than half of measured stocks "
                   "remain above their 20-day average. Risk is elevated.")
        new_money = ("Limit new risk until broader participation improves; "
                     "assess each company's chart and valuation.")
    elif strong_day and not short_breadth_ok:
        stance = "🟡 <b>REBOUND WATCH — strong day, weak underlying trend</b>"
        meaning = ("Many stocks rose in this session, but the 20-day breadth "
                   "has not recovered. This is NOT a confirmed trend reversal.")
        new_money = ("Look for repeat strength before increasing exposure; "
                     "one rebound day is not confirmation.")
    elif strong_day:
        stance = "🟢 <b>CONSTRUCTIVE — broad daily and short-term strength</b>"
        meaning = ("Most stocks rose and most remain above their 20-day "
                   "average. This supports participation, not a guaranteed rally.")
        new_money = ("Prioritize individual earnings, valuations and risk "
                     "before acting on any breakout.")
    else:
        stance = "🟡 <b>SELECTIVE — mixed market participation</b>"
        meaning = ("The session lacks a clear majority of advancing stocks. "
                   "Daily breadth alone cannot establish market direction.")
        new_money = ("Favor company-specific setups and controlled position "
                     "sizes over blanket market bets.")

    lines = [
        "📊 <b>MSK MARKET HEALTH | NIFTY 500</b>",
        f"📅 Session: {html.escape(str(r.get('session', 'unknown')))} | Completed daily close",
        stance,
        html.escape(meaning),
        "",
        "📌 <b>MARKET PULSE</b>",
        f"🔴 Falling: {int(dec)}/{int(valid)} ({falling:.1f}%)",
        f"🟢 Rising: {int(adv)}/{int(valid)} ({rising:.1f}%)",
        f"⚪ Unchanged: {int(unchanged)} | A/D ratio: {adv / dec:.2f}" if dec else
        f"⚪ Unchanged: {int(unchanged)} | A/D ratio: n/a",
    ]
    if benchmark is not None:
        lines.append(f"Nifty 50: {benchmark:+.2f}%")

    lines.extend([
        "",
        "📈 <b>TREND CHECK</b>",
        f"20-day: {pct(share20)} above average ({int(ma20.get('count') or 0)} stocks)",
        f"50-day: {pct(share50)} above average ({int(ma50.get('count') or 0)} stocks)",
        f"200-day: {pct(share200)} above average ({int(ma200.get('count') or 0)} stocks)",
    ])
    if trend_delta is not None:
        lines.append(
            f"5-session participation: {trend_delta:+.1f} percentage points "
            f"(same {int(r.get('trend_count') or 0)} stocks).")
    high, low, high_count = (number(r.get(k)) for k in
                             ("highs", "lows", "high_low_count"))
    if high is not None and low is not None and high_count and high_count > 0:
        lines.append(f"252-session closing highs/lows: "
                     f"{int(high)}/{int(low)} ({int(high_count)} stocks).")

    # Industry comparisons need >=15 covered symbols. Never elevate a
    # five-stock industry to "best sector" or infer BUY from daily breadth.
    eligible = []
    sectors = r.get("sectors") or {}
    if isinstance(sectors, dict):
        for name, item in sectors.items():
            if not isinstance(item, dict):
                continue
            count = number(item.get("count"))
            rate = number(item.get("adv_pct"))
            if count is not None and count >= 15 and rate is not None and 0 <= rate <= 100:
                eligible.append((str(name), int(count), rate))
    eligible.sort(key=lambda row: (-row[2], row[0]))
    if eligible:
        top = eligible[:2]
        bottom = sorted(eligible, key=lambda row: (row[2], row[0]))[:2]
        lines.extend(["", "🏭 <b>INDUSTRY PARTICIPATION</b>",
                      "<b>Relatively resilient (not buy calls)</b>"])
        for name, count, rate in top:
            lines.append(f"• {html.escape(name)}: {rate:.1f}% rising ({count})")
        lines.append("<b>Most pressured</b>")
        for name, count, rate in bottom:
            lines.append(f"• {html.escape(name)}: {rate:.1f}% rising ({count})")
        financials = next((item for item in eligible
                           if item[0] == "Financial Services"), None)
        if financials and financials not in top and financials not in bottom:
            lines.append(
                f"Banking/financial sector context: "
                f"{financials[2]:.1f}% rising ({financials[1]} covered).")
        lines.append("One-day industry breadth, not an entry or sector BUY signal.")

    lines.extend([
        "",
        "🎯 <b>INVESTOR DECISION</b>",
        "<b>New positions:</b> " + html.escape(new_money),
        "<b>Holdings:</b> Check risk limits and sector concentration; "
        "daily breadth alone is not a sell instruction.",
        "",
        "🔎 <b>NEXT-SESSION CONFIRMATION</b>",
        "🟢 Improvement: 50%+ stocks advancing AND five-session breadth improving.",
        "🔴 Caution: another <40% advancing session alongside "
        "falling 20-day participation.",
        "These are observation triggers, not next-day forecasts.",
        "",
        f"✅ Coverage: {int(valid)}/{int(r.get('requested') or valid)} "
        f"({pct(r.get('coverage'))})",
        f"🕒 Fetched: {html.escape(str(r.get('fetched', 'not recorded')))}",
        "Adjusted daily closes; current index members. Educational market context.",
    ])
    if r.get("trend_context"):
        lines.append("⚠️ " + html.escape(str(r["trend_context"])))
    report = "\n".join(lines)
    # Telegram messages have a 4096-character post-HTML cap; do not silently
    # trim the conclusion or suppress the last sector when the text expands.
    if len(report) > 3800:
        raise ValueError("market_health_telegram_report_too_long")
    return report
