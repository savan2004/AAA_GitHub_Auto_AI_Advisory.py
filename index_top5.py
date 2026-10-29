"""Send index-specific Top 5 investment watchlists automatically when scan completes.

One background pool handles at most one queued scan per profile; repeated taps
join the same work. Official NSE index membership is mandatory; weak or partial
data never becomes a fabricated investment recommendation.
"""
import html
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from conservative_screener import evaluate_conservative
from index_constituents import get_index_universe, INDEXES
from investment_screener import IST, evaluate_candidate
from top5_fundamental_gate import MAX_CHECKS, fetch_and_assess

LOG = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="msk-index-top5")
_LOCK = threading.Lock()
_PENDING = {}
_WAITERS = {}
_RESULTS = {}
_RESULT_TTL = 15 * 60
_MIN_COVERAGE = .65
_CAPS = {"conservative": 20., "moderate": 15., "aggressive": 12.}
_HORIZONS = {"conservative": "6–18 months (illustrative)",
             "moderate": "2–6 months (illustrative)",
             "aggressive": "2–12 weeks (illustrative)"}


def _select_top_five(rows, cap_per_sector=2):
    """Strongest qualified setups, with maximum two stocks from one industry."""
    selected, sectors = [], Counter()
    for row in sorted(rows, key=lambda r: (-r.get("combined_score", r["score"]),
                                            -r.get("fund_score", 0),
                                            -r.get("volx", 0), r["sym"])):
        industry = row.get("sector") or "Unknown"
        if sectors[industry] >= cap_per_sector:
            continue
        sectors[industry] += 1
        selected.append(row)
        if len(selected) >= 5:
            break
    return selected


def _conservative_levels(row):
    """Illustrative 2x ATR stop, 1.8R/3R targets; not an executable price quote."""
    entry = row["ltp"]
    atr = entry * row["atr_pct"] / 100.
    risk = 2. * atr
    if atr <= 0 or risk / entry > .08:
        return None
    return dict(row, entry=round(entry, 2), sl=round(entry-risk, 2),
                t1=round(entry+1.8*risk, 2), t2=round(entry+3*risk, 2),
                rr1=1.8, volx=0.0)


def _format_index_result(profile, universe, rows, received, fund_checked=0, fund_failed=0):
    total = len(universe["symbols"])
    coverage = received / total if total else 0
    title = f"{universe['cap'].upper()} — TOP 5" 
    lines = [f"📊 <b>{title} | {html.escape(universe['name'])}</b>",
             f"🏛 Source: {universe['source']} | membership checked {universe['asof']}",
             f"🕒 Horizon: {_HORIZONS[profile]}",
             f"📚 Valid price histories: {received}/{total} ({coverage:.0%})",
             "━━━━━━━━━━━━━━━━━━━━"]
    if universe.get("stale"):
        lines.append("⚠️ Verified index membership is a cached copy; check current NSE changes.")
    if coverage < _MIN_COVERAGE:
        lines += ["⚠️ Insufficient index coverage to rank its best stocks.",
                  "No securities recommended; incomplete data is not a bearish signal."]
        return "\n".join(lines)
    picks = _select_top_five([r for r in rows if isinstance(r.get("fund_score"), (int, float))])
    lines.append(f"🧾 Financial checks attempted: {fund_checked}; rejected/missing: {fund_failed}")
    lines.append(f"✅ <b>{len(picks)}/5 qualified technical + fundamental setups</b>")
    lines.append("Ranking combines technical trend and reported financial strength (not predicted returns).")
    if not picks:
        lines.append("⚪ No stock passed BOTH the bullish and validated-fundamentals checks; no forced selection.")
    cap = _CAPS[profile]
    for i, row in enumerate(picks, 1):
        sector = html.escape(row.get("sector") or "Unclassified", quote=False)
        sym = html.escape(row["sym"], quote=False)
        leverage = (f"P/B {row['pb']:.1f} (financial sector)" if row.get("fin")
                    else f"D/E {row['de']:.2f}")
        lines.extend([
            f"🟢 <b>{i}. {sym}</b> | {sector} | Combined {row['combined_score']}/100",
            f"Close ₹{row['ltp']:,.2f} ({row['chg']:+.2f}%) | RSI {row['rsi']}",
            f"📘 ROE {row['roe']:.1f}% | P/E {row['pe']:.1f} | {leverage}",
            f"3Y profit CAGR {row['profit_growth']:+.1f}%"
            + (f" | Sales {row['sales_growth']:+.1f}%" if row.get("sales_growth") is not None else ""),
            f"Entry ref ₹{row['entry']:,.2f} | SL ₹{row['sl']:,.2f}",
            f"T1 ₹{row['t1']:,.2f} | T2 ₹{row['t2']:,.2f} | R:R 1:{row['rr1']:.1f}",
            f"Max illustrative allocation {cap:.0f}% | Price {row['asof']} | Results {row['quarter']}",
            "···",
        ])
    invested = len(picks) * cap
    lines.append(f"💼 Allocation cap total {invested:.0f}% | Reserve ≥{100-invested:.0f}% cash.")
    if received < total:
        lines.append(f"⚠️ {total-received} index members lack valid recent data; ranking covers only the available universe.")
    lines.append("⚠️ Fundamental data from public aggregators (Screener/Yahoo); not independently reconciled with NSE filings. "
                 "Scores are heuristic, not success rates. Educational; not SEBI-registered advice.")
    return "\n".join(lines)


def _build_index_top5(profile):
    """Full-index scan, not a convenient 20-stock preselected sample."""
    from market_data import bulk_history

    universe = get_index_universe(profile)
    symbols = universe["symbols"]
    now = datetime.now(IST)
    data = bulk_history(symbols, "6mo", chunk_size=50,
                        cache_ttl=30 * 60, adjusted=False, retries=1)
    if not isinstance(data, dict):
        data = {}
    received, rows = 0, []
    for symbol in symbols:
        frame = data.get(symbol)
        if frame is None or frame.empty:
            continue
        received += 1
        sector = universe["industries"].get(symbol) or "Unclassified"
        if profile == "conservative":
            row = evaluate_conservative(symbol, frame, now.date(), sector_override=sector)
            if row:
                row = _conservative_levels(row)
        else:
            row = evaluate_candidate(symbol, frame, profile, now)
            if row:
                row["sector"] = sector
        if row is not None:
            rows.append(row)
    # Stage two: validate statements only for the strongest TECHNICAL shortlist,
    # never request several hundred fundamentals from the Telegram button.
    # A stock without complete, recent evidence cannot enter the final five.
    from collections import Counter
    reasons = Counter()
    validated = []
    inspected = 0
    if received >= int(len(symbols) * _MIN_COVERAGE):
        candidates = sorted(rows, key=lambda r: (-r["score"], -r.get("volx", 0), r["sym"]))
        for row in candidates[:MAX_CHECKS]:
            inspected += 1
            info, reason = fetch_and_assess(row["sym"], row.get("sector"), profile, now.date())
            if not info:
                reasons[reason] += 1
                continue
            r = dict(row, **info)
            r["combined_score"] = round(row["score"] * .45 + info["fund_score"] * .55, 1)
            validated.append(r)
    else:
        LOG.warning("MSK Top5 %s: price coverage too low for financial checks", profile)
    LOG.info("MSK Top5 %s: index=%d price_valid=%d technical=%d financial_checked=%d "
             "qualified=%d missing_or_rejected=%s",
             profile, len(symbols), received, len(rows), inspected, len(validated), dict(reasons))
    return _format_index_result(profile, universe, validated, received,
                                fund_checked=inspected, fund_failed=sum(reasons.values()))


def _finish(profile, future):
    try:
        result = future.result()
    except Exception as exc:
        LOG.warning("MSK Top5 %s failed (%s)", profile, type(exc).__name__)
        result = ("⚠️ Official NSE index or market-data fetch unavailable. "
                  "No stock recommendations issued. Retry later.")
    with _LOCK:
        waiters = _WAITERS.pop(profile, {})
        _PENDING.pop(profile, None)
        if result.startswith("📊"):
            _RESULTS[profile] = (time.monotonic(), result)
    for chat_id, send in waiters.items():
        try:
            send(chat_id, result)
        except Exception as exc:
            LOG.warning("MSK Top5 response delivery failed (%s)", type(exc).__name__)


def request_index_top5(profile, chat_id, send):
    """Enqueue and return a short acknowledgement; completion pushes once to chat."""
    if profile not in INDEXES:
        return "❌ Unknown index profile."
    with _LOCK:
        cached = _RESULTS.get(profile)
        if cached and time.monotonic() - cached[0] < _RESULT_TTL:
            return "📌 Cached recent index scan:\n" + cached[1]
        if profile in _PENDING:
            _WAITERS[profile][chat_id] = send
            return f"⏳ {INDEXES[profile]['name']} scan already running; result will arrive here."
        _WAITERS[profile] = {chat_id: send}
        future = _POOL.submit(_build_index_top5, profile)
        _PENDING[profile] = future
    future.add_done_callback(lambda f, p=profile: _finish(p, f))
    return (f"🔎 <b>{INDEXES[profile]['cap']} Top 5</b>: scanning full "
            f"{INDEXES[profile]['name']} membership. "
            "Qualified results will be posted automatically once the scan finishes.")
