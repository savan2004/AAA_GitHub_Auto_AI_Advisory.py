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
    """Investor-first summary of VERIFIED breadth data (no trading prediction).

    The single-session direction is deliberately separate from the share of
    stocks above moving averages. Sampled industries do not become buy calls.
    """
    if r.get("state") == "warming":
        return ("⏳ <b>Market Breadth preparing</b>\n"
                "Collecting genuine Nifty 500 completed-session prices. "
                "No unverified breadth statistics are published. "
                "Please try again after the collection completes.")
    if r.get("state") == "unavailable":
        return ("⚠️ <b>Market Breadth unavailable</b>\n"
                "No verified current-session snapshot. The provider failed; "
                "the next bounded refresh will retry. No stale data shown.")

    def pct(value):
        return 'unavailable' if value is None else f'{value:.1f}%'

    # Rates below are calculated from the same verified stock universe.
    valid = r['valid']
    advances, declines = r['advances'], r['declines']
    rising = 100.0 * advances / valid if valid else None
    falling = 100.0 * declines / valid if valid else None
    ma20, ma50, ma200 = (r[f'ma{n}'] for n in (20, 50, 200))
    weak_day = rising is not None and rising < 40
    strong_day = rising is not None and rising > 60
    short_trend_positive = ma20['pct'] is not None and ma20['pct'] >= 50

    if weak_day and short_trend_positive:
        headline = "⚠️ <b>Weak trading day; short-term trend not yet broadly broken</b>"
        plain = ("Fewer than 4 in 10 stocks rose today, but a majority of "
                 "eligible stocks remain above their 20-day average. "
                 "This is near-term selling pressure, not proof of a lasting downtrend.")
        action = ("<b>Fresh investments:</b> Consider staggered entries rather than "
                  "treating one weak day as an all-clear or a crash signal.")
    elif weak_day:
        headline = "🔴 <b>Weak participation and soft short-term trend</b>"
        plain = ("Most covered stocks fell today, and fewer than half are above "
                 "their 20-day average. Market participation is weak.")
        action = ("<b>Fresh investments:</b> Consider smaller position sizes; "
                  "wait for broader participation before increasing risk.")
    elif strong_day and short_trend_positive:
        headline = "🟢 <b>Broad daily strength with supportive short-term trend</b>"
        plain = ("Most covered stocks rose and a majority remain above their "
                 "20-day average. This supports participation, not a guaranteed rally.")
        action = ("<b>Fresh investments:</b> Confirm stock-specific valuations, "
                  "earnings and risk before taking a new position.")
    else:
        headline = "🟡 <b>Mixed market participation</b>"
        plain = ("Daily breadth is mixed; these figures alone do not confirm "
                 "a sustained market direction.")
        action = ("<b>Fresh investments:</b> Prefer company fundamentals and "
                  "planned risk limits over the day's market-wide reading.")

    lines = [
        "📊 <b>MSK MARKET HEALTH | NIFTY 500</b>",
        f"📅 Last completed session: {html.escape(str(r['session']))}",
        headline,
        html.escape(plain),
        "",
        "<b>1. What happened?</b>",
        f"📉 {declines}/{valid} stocks fell ({pct(falling)})",
        f"📈 {advances}/{valid} stocks rose ({pct(rising)})",
    ]
    if r['benchmark_change'] is not None:
        lines.append(f"Nifty 50 daily move: {r['benchmark_change']:+.2f}%")

    lines.extend([
        "",
        "<b>2. Is the wider trend still holding?</b>",
        f"Above 20-day average: {pct(ma20['pct'])} ({ma20['count']} measured)",
        f"Above 50-day average: {pct(ma50['pct'])} ({ma50['count']} measured)",
        f"Above 200-day average: {pct(ma200['pct'])} ({ma200['count']} measured)",
        "A stock can fall today yet remain above its average; these measure different things.",
    ])
    if r.get('trend_delta') is not None:
        change = r['trend_delta']
        direction = "weakened" if change < 0 else ("improved" if change > 0 else "was unchanged")
        lines.append(
            f"5-session participation {direction}: {change:+.1f} percentage points "
            f"(same {r['trend_count']} stocks; first vs last session).")

    lines.extend(["", "<b>3. What this means for investors</b>", action,
                  "<b>Existing holdings:</b> Review position concentration and "
                  "your planned risk limits; a breadth reading is not a sell signal."])

    eligible_sectors = [(name, data) for name, data in r.get('sectors', {}).items()
                        if data.get('count', 0) >= 5]
    ranked = sorted(eligible_sectors,
                    key=lambda pair: pair[1]['adv_pct'], reverse=True)
    chosen = ranked[:3] + [item for item in ranked[-3:] if item not in ranked[:3]]
    if chosen:
        lines.extend(["", "<b>4. Industry snapshot — daily participation only</b>"])
        for name, data in chosen:
            rate = data['adv_pct']
            n = data['count']
            mark = "⚠️" if n < 15 else "•"
            lines.append(f"{mark} {html.escape(str(name))}: {rate:.0f}% rising ({n} covered)")
        lines.append("Small groups can swing sharply; this is NOT a sector buy/sell ranking.")

    lines.extend([
        "",
        f"✅ Verified coverage: {valid}/{r['requested']} ({r['coverage']:.1f}%)",
        f"Data: {html.escape(str(r['source']))}; fetched {html.escape(str(r['fetched']))}.",
        "Adjusted completed daily closes. Current members, not a backtest.",
        "Educational market context; no individual stock recommendation.",
    ])
    if r.get('trend_context'):
        lines.append("⚠️ " + html.escape(r['trend_context']))
    return "\n".join(lines)
