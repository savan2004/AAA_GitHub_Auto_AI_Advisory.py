"""Privacy-safe, limited Nifty 500 industry-peer research for Portfolio Doctor.

Only inside the disposable report worker. Does not retain customer holdings,
fetch from a user portfolio store, create files or print holdings to logs.
An official index roster is required before calling any company a constituent.
Results are technical research observations, never personalised swaps.
"""
import csv
import io
import re
from datetime import datetime

from portfolio_doctor_live import IST

OFFICIAL_ROSTER = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
MIN_MEMBERS = 450
MAX_MEMBERS = 550
PEERS_PER_INDUSTRY = 8
MAX_SCREENED = 36
MAX_RESULTS = 8


def official_nifty500_industries(*, get=None):
    """Return official Symbol->Industry membership or empty on uncertain evidence."""
    import requests
    get = get or requests.get
    try:
        response = get(OFFICIAL_ROSTER, timeout=8,
                       headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"})
        if not getattr(response, "ok", False):
            return {}
        raw = response.content
        if not isinstance(raw, bytes) or not 10_000 <= len(raw) <= 400_000:
            return {}
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        cols = {str(col).strip().upper() for col in (reader.fieldnames or [])}
        if not {"SYMBOL", "INDUSTRY"}.issubset(cols):
            return {}
        members = {}
        for item in reader:
            normalized = {str(k).strip().upper(): str(v or "").strip()
                          for k, v in item.items() if k is not None}
            sym = normalized.get("SYMBOL", "").upper()
            industry = normalized.get("INDUSTRY", "")
            if not re.fullmatch(r"[A-Z0-9&-]{2,20}", sym) or not industry:
                return {}
            if sym in members:
                return {}
            members[sym] = industry
            if len(members) > MAX_MEMBERS:
                return {}
        return members if MIN_MEMBERS <= len(members) <= MAX_MEMBERS else {}
    except (requests.RequestException, UnicodeError, ValueError, TypeError,
            csv.Error, OverflowError):
        return {}


def research_industry_peers(holdings, *, members=None, history_fetcher=None,
                            evaluator=None, now=None):
    """Rank a DISCLOSED sample of same-industry constituents, not all 500.

    Fail closed on unknown membership, unavailable history or stale daily bars.
    All rows must qualify under the existing moderate technical screener.
    """
    now = now or datetime.now(IST)
    if members is None:
        members = official_nifty500_industries()
    out = {"status": "UNAVAILABLE", "universe": len(members or {}),
           "pool": 0, "screened": 0, "fresh": 0, "qualified": 0,
           "candidates": [], "scope": "same-industry sampled peers only"}
    if not isinstance(members, dict) or not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        return out
    held = {str(row.get("symbol", "")).upper() for row in holdings}
    industries = list(dict.fromkeys(members[sym] for sym in held if sym in members))
    if not industries:
        return out
    pool = [(sym, industry) for sym, industry in members.items()
            if sym not in held and industry in industries]
    out["pool"] = len(pool)
    sampled = []
    for industry in industries:
        for sym, label in pool:
            if label == industry:
                sampled.append((sym, label))
                if sum(x[1] == industry for x in sampled) >= PEERS_PER_INDUSTRY:
                    break
            if len(sampled) >= MAX_SCREENED:
                break
        if len(sampled) >= MAX_SCREENED:
            break
    if not sampled:
        out["status"] = "NO_SAME_INDUSTRY_PEERS"
        return out
    out["screened"] = len(sampled)
    if history_fetcher is None:
        from market_data import bulk_history
        history_fetcher = lambda symbols: bulk_history(
            symbols, "1y", chunk_size=36, retries=0, max_fallbacks=0,
            download_timeout=15)
    if evaluator is None:
        from investment_screener import evaluate_candidate
        evaluator = evaluate_candidate
    try:
        histories = history_fetcher([sym for sym, _ in sampled])
    except Exception:
        return out
    if not isinstance(histories, dict):
        return out
    from portfolio_doctor_live import verified_completed_daily_frame
    qualified = []
    for sym, industry in sampled:
        try:
            checked = verified_completed_daily_frame(histories.get(sym), now=now)
            out["fresh"] += 1
            scored = evaluator(sym, checked, "moderate", asof=now)
            if not isinstance(scored, dict) or scored.get("sym") != sym:
                continue
            if scored.get("asof") != checked.index[-1].date().isoformat():
                continue
            score = float(scored["score"])
            price = float(scored["ltp"])
            if not 0 <= score <= 100 or price <= 0:
                continue
            qualified.append({
                "symbol": sym, "industry": industry, "score": score,
                "close": price, "date": scored["asof"],
                "rsi": scored.get("rsi"), "adx": scored.get("adx"),
            })
        except (Exception):
            # Never emit customer tickers or provider details in worker logs.
            continue
    qualified.sort(key=lambda row: (-row["score"], row["symbol"]))
    chosen, counts = [], {}
    for row in qualified:
        label = row["industry"]
        if counts.get(label, 0) >= 2:
            continue
        chosen.append(row)
        counts[label] = counts.get(label, 0) + 1
        if len(chosen) >= MAX_RESULTS:
            break
    out["qualified"] = len(qualified)
    out["candidates"] = chosen
    out["status"] = "SCREENED"
    return out
