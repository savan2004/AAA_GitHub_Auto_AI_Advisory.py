"""Completed-session breadth. Pure calculations are separate from data access."""
import csv
import io
import html
import threading
import time
from datetime import datetime, time as daytime, timedelta, timezone

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
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


def get_breadth():
    global _cache, _cache_at
    # One scanner at a time; callers share a completed result for 15 minutes.
    with _lock:
        expected = str(expected_completed_session())
        if (_cache is not None and time.time()-_cache_at < 900
                and _cache.get('session') == expected and not _cache.get('stale')
                and _cache.get('coverage', 0) >= 80):
            return _cache
        from market_data import bulk_history, index_history
        syms, sectors, source, degraded = load_universe()
        hist = bulk_history(syms, '2y', chunk_size=100, cache_ttl=900, adjusted=True)
        result = calculate_breadth(hist, syms, index_history('^NSEI', '2y'), sectors)
        if result['stale']:
            raise ValueError(f"Latest verified NSE session {result['session']} is not expected "
                             f"session {result['expected_session']}; no stale breadth report.")
        if result['coverage'] < 80:
            raise ValueError(f"Insufficient Nifty basket quote coverage "
                             f"({result['coverage']}%); report withheld.")
        result.update(source=source, degraded=degraded, fetched=datetime.now(IST).strftime('%d-%b %H:%M IST'))
        if degraded:
            result['interpretation'] = 'Sample only: ' + result['interpretation']
        _cache, _cache_at = result, time.time()
        return result


def format_breadth(r):
    pct = lambda x: 'unavailable' if x is None else f'{x:.1f}%'
    lines = ['📊 <b>MARKET PARTICIPATION</b>', f"Session: {r['session']} • completed daily bars",
             html.escape(r['source']), f"Coverage: {r['valid']}/{r['requested']} ({r['coverage']:.1f}%)",
             f"Advances {r['advances']} | Declines {r['declines']} | Unchanged {r['unchanged']}",
             'A/D ratio: ' + (str(r['ad_ratio']) if r['ad_ratio'] is not None else 'N/A (no declines)'), '']
    for n in (20,50,200):
        m = r[f'ma{n}']; lines.append(f"Above {n}-day SMA: {pct(m['pct'])} ({m['count']} eligible)")
    lines += [f"252-session closing highs/lows: {r['highs']}/{r['lows']} ({r['high_low_count']} eligible)"]
    if r['trend_delta'] is not None:
        lines.append(f"Advance share, first→last of 5 sessions: {r['trend_delta']:+.1f} pp (same {r['trend_count']} stocks)")
    if r['benchmark_change'] is not None:
        lines.append(f"Nifty 50: {r['benchmark_change']:+.2f}%")
    lines += ['', '<b>' + html.escape(r['interpretation']) + '</b>']
    if r.get('trend_context'):
        lines.append('⚠️ ' + html.escape(r['trend_context']))
    lines += ['', '<b>Industry participation (≥5 covered stocks)</b>']
    sectors = [(s,v) for s,v in r['sectors'].items() if v['count'] >= 5]
    ranked = sorted(sectors, key=lambda x: x[1]['adv_pct'], reverse=True)
    chosen = ranked[:3] + [x for x in ranked[-3:] if x not in ranked[:3]]
    for s,v in chosen:
        lines.append(f"{html.escape(s)}: {v['adv_pct']:.0f}% advancing ({v['count']})")
    lines += ['', f"Fetched {r['fetched']}. Adjusted prices; highs/lows use closes, not intraday extremes.",
              'Missing/stale sessions excluded. Current constituents, not a historical backtest. Educational indicators.']
    return '\n'.join(lines)
