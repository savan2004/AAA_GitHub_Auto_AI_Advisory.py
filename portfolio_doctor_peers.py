"""Privacy-safe, limited Nifty 500 industry-peer research for Portfolio Doctor.

Only inside the disposable report worker. Does not retain customer holdings,
fetch from a user portfolio store, create files or print holdings to logs.
An official index roster is required before calling any company a constituent.
Results are technical research observations, never personalised swaps.
"""
import csv
import io
import math
import re
from datetime import datetime

from portfolio_doctor_live import IST

OFFICIAL_ROSTER = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
MIN_MEMBERS = 450
MAX_MEMBERS = 550
PEERS_PER_INDUSTRY = 8
MAX_SCREENED = 36
MAX_RESULTS = 12
MAX_PER_INDUSTRY = 3


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
           "candidates": [], "comparisons": [], "held_industries": {},
           "scope": "same-industry sampled peers only"}
    if not isinstance(members, dict) or not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        return out
    held = {str(row.get("symbol", "")).upper() for row in holdings}
    out["held_industries"] = {sym: members[sym] for sym in held if sym in members}
    industries = list(dict.fromkeys(members[sym] for row in holdings
                                    if (sym := str(row.get("symbol", "")).upper()) in members))
    if not industries:
        return out
    pool = [(sym, industry) for sym, industry in members.items()
            if sym not in held and industry in industries]
    out["pool"] = len(pool)
    # Spread the bounded sample across each represented industry rather than
    # always choosing the first members of the official CSV. Each industry
    # receives a fair quota even when five holdings span five industries.
    sampled = []
    base, extra = divmod(MAX_SCREENED, len(industries))
    for i, industry in enumerate(industries):
        matches = [(sym, label) for sym, label in pool if label == industry]
        quota = min(PEERS_PER_INDUSTRY, base + (1 if i < extra else 0), len(matches))
        if quota <= 0:
            continue
        if quota == 1:
            positions = [len(matches) // 2]
        else:
            positions = [round(j * (len(matches) - 1) / (quota - 1)) for j in range(quota)]
        sampled.extend(matches[j] for j in positions)
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
    comparisons = []
    for sym, industry in sampled:
        try:
            checked = verified_completed_daily_frame(histories.get(sym), now=now)
            out["fresh"] += 1
            # Show independently verified industry peers even when strict
            # moderate entry/volume/risk gates don't produce a candidate.
            # These are comparison observations, NOT qualified replacements.
            closes = [float(x) for x in checked["Close"].tolist()]
            if len(closes) >= 51 and all(math.isfinite(x) and x > 0 for x in closes):
                from portfolio_doctor_calculations import _rsi
                day20 = 100 * (closes[-1] / closes[-21] - 1)
                rsi = _rsi(closes)
                if rsi is not None and all(math.isfinite(x) for x in (day20, float(rsi))):
                    comparisons.append({
                        "symbol": sym, "industry": industry,
                        "close": closes[-1], "date": checked.index[-1].date().isoformat(),
                        "return20": round(day20, 2), "rsi14": round(float(rsi), 1),
                    })
            scored = evaluator(sym, checked, "moderate", asof=now)
            if not isinstance(scored, dict) or scored.get("sym") != sym:
                continue
            if scored.get("asof") != checked.index[-1].date().isoformat():
                continue
            score = float(scored["score"])
            price = float(scored["ltp"])
            if not math.isfinite(score) or not math.isfinite(price) or not 0 <= score <= 100 or price <= 0:
                continue
            qualified.append({
                "symbol": sym, "industry": industry, "score": score,
                "close": price, "date": scored["asof"],
                "rsi": scored.get("rsi"), "adx": scored.get("adx"),
            })
        except Exception:
            # Never emit customer tickers or provider details in worker logs.
            continue
    qualified.sort(key=lambda row: (-row["score"], row["symbol"]))
    chosen, counts = [], {}
    for row in qualified:
        label = row["industry"]
        if counts.get(label, 0) >= MAX_PER_INDUSTRY:
            continue
        chosen.append(row)
        counts[label] = counts.get(label, 0) + 1
        if len(chosen) >= MAX_RESULTS:
            break
    out["qualified"] = len(qualified)
    out["candidates"] = chosen
    # Price momentum is disclosed only as one comparable measurement; no
    # fundamental-quality ranking is inferred from short-window returns.
    selected_symbols = {row['symbol'] for row in chosen}
    comparison_rows = []
    for industry in industries:
        ordered = sorted((row for row in comparisons if row["industry"] == industry
                          and row["symbol"] not in selected_symbols),
                         key=lambda row: (-row["return20"], row["symbol"]))
        comparison_rows.extend(ordered[:MAX_PER_INDUSTRY])
    out["comparisons"] = comparison_rows[:10]
    out["status"] = "SCREENED"
    return out
