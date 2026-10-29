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
from top5_technical_ratings import technical_candidate
from top5_company_ratings import fetch_company_evidence

LOG = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="msk-index-top5")
_LOCK = threading.Lock()
_PENDING = {}
_WAITERS = {}
_RESULTS = {}
_RESULT_TTL = 15 * 60
_MAX_FINANCIAL_CHECKS = 28
_MIN_COVERAGE = .65
_CAPS = {"conservative": 20., "moderate": 15., "aggressive": 12.}
_HORIZONS = {"conservative": "6–18 months (illustrative)",
             "moderate": "2–6 months (illustrative)",
             "aggressive": "2–12 weeks (illustrative)"}


def _select_top_five(rows, cap_per_sector=2):
    """Strongest qualified setups, with maximum two stocks from one industry."""
    selected, sectors = [], Counter()
    def rank(row):
        quality = {"VALIDATED": 2, "PRELIMINARY": 1, "UNVERIFIED": 0}.get(
            row.get("fund_status"), 0)
        overall = row.get("overall_score")
        if overall is None:
            overall = row.get("tech_score", row.get("score", 0))
        return (-quality, -overall,
                -row.get("tech_score", row.get("score", 0)), row["sym"])
    for row in sorted(rows, key=rank):
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


def _fmt(value, decimals=1, suffix=""):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (ValueError, TypeError):
        return "N/A"


def _format_index_result(profile, universe, rows, received, fund_checked=0, fund_failed=0,
                         total_rated=0):
    total = len(universe["symbols"])
    coverage = received / total if total else 0
    title = f"{universe['cap'].upper()} — TOP 5 COMPANY WATCHLIST"
    lines = [f"📊 <b>{title} | {html.escape(universe['name'])}</b>",
             f"🏛 Index source: {html.escape(universe['source'])} | {html.escape(universe['asof'])}",
             f"🕒 Horizon: {_HORIZONS[profile]}",
             f"📚 Index price coverage: {received}/{total} ({coverage:.0%})",
             f"🧾 Fundamental reviews: {fund_checked}/{total} | complete evidence: {fund_checked-fund_failed}",
             "━━━━━━━━━━━━━━━━━━━━"]
    if universe.get("stale"):
        lines.append("⚠️ Index membership is a recent cached copy; verify current NSE changes.")
    if coverage < _MIN_COVERAGE:
        lines.extend(["⚠️ Insufficient price coverage for meaningful index ranking.",
                      "No stock suggestions; missing histories are not bearish signals."])
        return "\n".join(lines)
    picks = _select_top_five(rows)
    verified = sum(r.get("fund_status") == "VALIDATED" for r in picks)
    lines += [f"✅ <b>{len(picks)}/5 ranked candidates | {verified} fully evidence-validated</b>",
              "Not a full fundamental ranking of every index company; only reviewed stocks are compared."]
    if not picks:
        lines.append("⚪ No company had usable risk/price levels. No forced recommendation.")
    cap = _CAPS[profile]
    for i, r in enumerate(picks, 1):
        sym = html.escape(r["sym"], quote=False)
        sector = html.escape(r.get("sector") or "Unknown", quote=False)
        status = r.get("fund_status", "UNVERIFIED")
        overall = _fmt(r.get("overall_score"),0,"/100")
        fund_rating = _fmt(r.get("fund_score"),0,"/100")
        tech = _fmt(r.get("tech_score", r.get("score")),0,"/100")
        signal = r.get("signal","WAIT")
        # Unverified financials cannot produce an investment BUY label.
        if status != "VALIDATED":
            signal = "WAIT — RESEARCH ONLY"
        loss = r.get("risk_pct") or 0
        risk = "HIGH (unverified financials)" if status!="VALIDATED" else r.get("risk_label","HIGH")
        ratio = "P/B " + _fmt(r.get("pb"),2) if r.get("fin") else "D/E " + _fmt(r.get("de"),2)
        lines += [
            f"<b>{i}. {sym}</b> | {sector}",
            f"⭐ Overall: {overall} | Fundamental: {fund_rating} ({status}) | Technical: {tech}",
            f"🧭 {signal} | Risk: {risk} | Stop distance {loss:.1f}%",
            f"₹{r['ltp']:,.2f} ({r['chg']:+.2f}%) | RSI {_fmt(r.get('rsi'))} | ADX {_fmt(r.get('adx'))}",
            f"📘 ROE {_fmt(r.get('roe'),1,'%')} | P/E {_fmt(r.get('pe'))} | {ratio}",
            f"📈 Profit 3Y {_fmt(r.get('profit_growth'),1,'%')} | Sales 3Y {_fmt(r.get('sales_growth'),1,'%')}",
            f"🎯 Trigger ₹{r['entry']:,.2f} | SL ₹{r['sl']:,.2f}",
            f"T1 ₹{r['t1']:,.2f} (R:R 1:2) | T2 ₹{r['t2']:,.2f} (1:3)",
            f"📆 Price {r['asof']} | Results {html.escape(str(r.get('quarter') or 'unverified'))}",
            "···"
        ]
    if picks:
        lines.append(f"💼 Illustrative maximum per stock {cap:.0f}%; not a suitability-based allocation.")
    if received < total:
        lines.append(f"⚠️ {total-received} index stocks lack usable recent prices.")
    lines += [
        "⚠️ This is a WATCHLIST, not five guaranteed best investments. "
        f"Fundamentals assessed for {fund_checked}/{total}; incomplete sources remain N/A.",
        "🛑 WAIT means no current buy; trigger/SL/targets are hypothetical "
        "risk-plan levels, NOT return forecasts. R:R is geometric, not win probability.",
        "Data: public aggregators, not issuer-filings audited. Educational only; not SEBI-registered advice."
    ]
    return "\n".join(lines)


def _build_index_top5(profile):
    """Rank entire NSE index by technical and risk metrics; examine broad financial pool."""
    from market_data import bulk_history

    universe = get_index_universe(profile)
    symbols = universe["symbols"]
    now = datetime.now(IST)
    data = bulk_history(symbols, "6mo", chunk_size=50,
                        cache_ttl=30 * 60, adjusted=False, retries=1)
    if not isinstance(data, dict):
        data = {}
    received, rows = 0, []
    for sym in symbols:
        frame = data.get(sym)
        if frame is None or frame.empty:
            continue
        received += 1
        row = technical_candidate(sym, frame, profile, now)
        if row is not None:
            row["sector"] = universe["industries"].get(sym) or "Unclassified"
            rows.append(row)

    reviewed, rejected, enriched = 0, 0, []
    if received / max(1, len(symbols)) >= _MIN_COVERAGE:
        # The historical index universe is entirely scanned for chart/risk strength.
        # Financial checks of all 247 are not feasible on a synchronous Telegram click.
        # Review diverse, strong candidates without excluding sideways companies.
        sectors = Counter()
        pool = []
        for row in sorted(rows,key=lambda x:(-x["tech_score"],x["sym"])):
            sector = row["sector"]
            if sectors[sector] >= 5:
                continue
            sectors[sector] += 1
            pool.append(row)
            if len(pool) >= _MAX_FINANCIAL_CHECKS:
                break
        for row in pool:
            reviewed += 1
            evidence = fetch_company_evidence(row["sym"], row["sector"], profile, now.date())
            item = dict(row, **evidence)
            fscore = item.get("fund_score")
            if fscore is not None:
                item["overall_score"] = round(.60*fscore + .40*row["tech_score"],1)
            else:
                item["overall_score"] = None
            if item.get("fund_status") != "VALIDATED":
                rejected += 1
            enriched.append(item)
    else:
        LOG.warning("MSK Top5 %s: too few valid price histories",profile)
    LOG.info("MSK Top5 %s: index=%d valid_prices=%d technical_rated=%d "
             "fund_reviewed=%d complete=%d provisional=%d",
             profile,len(symbols),received,len(rows),reviewed,reviewed-rejected,
             sum(1 for r in enriched if r.get("fund_status")=="PRELIMINARY"))
    return _format_index_result(profile,universe,enriched,received,
                                fund_checked=reviewed,fund_failed=rejected,total_rated=len(rows))


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
