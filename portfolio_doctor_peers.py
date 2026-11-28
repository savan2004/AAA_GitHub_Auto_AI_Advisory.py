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
ARCHIVE_ROSTER = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
MIN_MEMBERS = 450
MAX_MEMBERS = 550
MAX_SCREENED = 180  # Cover a typical 151-peer pool, while bounding free-tier resources.
MAX_RESULTS = 12
MAX_PER_INDUSTRY = 3


def official_nifty500_industries(*, get=None, provenance=None):
    """Use either official public roster without disclosing any holdings."""
    import requests
    get = get or requests.get
    for url in (ARCHIVE_ROSTER, OFFICIAL_ROSTER):
        try:
            response = get(url, timeout=(2, 2), allow_redirects=False,
                           headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv",
                                    "Referer": "https://www.nseindia.com/"})
            if response.ok and getattr(response, "status_code", 200) == 200:
                members = _parse_roster(response.content)
                if members:
                    if provenance is not None:
                        provenance.update(roster_url=url, roster_observed=datetime.now(IST).date().isoformat())
                    return members
        except (requests.RequestException, ValueError, TypeError):
            continue
    return {}


def _parse_roster(raw):
    try:
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
    except (UnicodeError, ValueError, TypeError,
            csv.Error, OverflowError):
        return {}


def research_industry_peers(holdings, *, members=None, history_fetcher=None,
                            evaluator=None, now=None):
    """Screen every same-industry member up to a bounded 180-stock cap.

    Prioritize the *highest current portfolio exposure* first. An incomplete
    public download never turns an unchecked stock into a failed candidate.
    Evidence is informational, not a personalized replacement recommendation.
    """
    now = now or datetime.now(IST)
    if members is None:
        members = official_nifty500_industries()
    out = {"status": "UNAVAILABLE", "universe": len(members or {}),
           "pool": 0, "screened": 0, "fresh": 0, "qualified": 0,
           "candidates": [], "comparisons": [], "held_industries": {},
           "industry_coverage": [], "failure_counts": {},
           "scope": "same-industry peer pool; weighted industry priority"}
    if not isinstance(members, dict) or not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        return out

    holdings = [row for row in holdings if row.get("exchange") != "BSE"
                and not str(row.get("price_source", "")).startswith("BSE:")]
    held = {str(row.get("symbol", "")).upper() for row in holdings}
    out["held_industries"] = {sym: members[sym] for sym in held if sym in members}
    # Row values are verified holdings-only figures. Do not persist or log them.
    weights = {}
    for row in holdings:
        sym = str(row.get("symbol", "")).upper()
        if sym not in members:
            continue
        try:
            value = float(row.get("value", 0))
        except (ValueError, TypeError, OverflowError):
            value = 0
        if not math.isfinite(value) or value < 0:
            value = 0
        industry = members[sym]
        weights[industry] = weights.get(industry, 0) + value
    industries = sorted(weights, key=lambda group: (-weights[group], group))
    if not industries:
        return out

    pool = [(sym, industry) for industry in industries for sym, label
            in members.items() if sym not in held and label == industry]
    out["pool"] = len(pool)
    coverage = {industry: {"industry": industry, "pool": 0, "attempted": 0,
                           "fresh": 0, "compared": 0, "qualified": 0}
                for industry in industries}
    for _, industry in pool:
        coverage[industry]["pool"] += 1
    sampled = pool[:MAX_SCREENED]
    for _, industry in sampled:
        coverage[industry]["attempted"] += 1
    out["screened"] = len(sampled)
    out["industry_coverage"] = [coverage[industry] for industry in industries]
    if not sampled:
        out["status"] = "NO_SAME_INDUSTRY_PEERS"
        return out

    if history_fetcher is None:
        from market_data import bulk_history
        # Three 60-symbol bounded chunks cover a typical 151-stock peer pool;
        # no per-symbol retries, giant concurrent job or synthetic candles.
        history_fetcher = lambda symbols: bulk_history(
            symbols, "1y", chunk_size=60, retries=0,
            max_fallbacks=0, download_timeout=9)
    if evaluator is None:
        from investment_screener import evaluate_candidate
        evaluator = evaluate_candidate
    try:
        histories = history_fetcher([sym for sym, _ in sampled])
    except Exception:
        out["failure_counts"] = {"PRICE_PROVIDER_UNAVAILABLE": len(sampled)}
        return out
    if not isinstance(histories, dict):
        out["failure_counts"] = {"PRICE_PROVIDER_UNAVAILABLE": len(sampled)}
        return out

    from portfolio_doctor_live import verified_completed_daily_frame
    from portfolio_doctor_calculations import _rsi
    qualified, comparisons, evidence = [], [], {}
    failures = {}
    def rejected(reason):
        failures[reason] = failures.get(reason, 0) + 1

    for sym, industry in sampled:
        try:
            checked = verified_completed_daily_frame(histories.get(sym), now=now)
        except Exception:
            rejected("MISSING_OR_STALE_COMPLETED_OHLCV")
            continue
        out["fresh"] += 1
        coverage[industry]["fresh"] += 1
        try:
            closes = [float(x) for x in checked["Close"].tolist()]
            if len(closes) < 51 or not all(math.isfinite(x) and x > 0 for x in closes):
                rejected("INSUFFICIENT_51_SESSION_HISTORY")
                continue
            change20 = 100 * (closes[-1] / closes[-21] - 1)
            rsi = _rsi(closes)
            if rsi is None or not all(math.isfinite(x) for x in (change20, float(rsi))):
                rejected("INVALID_TECHNICAL_HISTORY")
                continue
            observation = {
                "symbol": sym, "industry": industry,
                "close": closes[-1], "date": checked.index[-1].date().isoformat(),
                "return20": round(change20, 2), "rsi14": round(float(rsi), 1),
                "return_window": tuple(day.date().isoformat()
                                       for day in checked.index[-21:]),
            }
            comparisons.append(observation)
            evidence[sym] = observation
            coverage[industry]["compared"] += 1
            scored = evaluator(sym, checked, "moderate", asof=now)
            if not isinstance(scored, dict):
                rejected("MODERATE_TREND_MOMENTUM_LIQUIDITY_RISK")
                continue
            if scored.get("sym") != sym or scored.get("asof") != observation["date"]:
                rejected("SCORE_SOURCE_OR_DATE_MISMATCH")
                continue
            score, price = float(scored["score"]), float(scored["ltp"])
            if not all(math.isfinite(x) for x in (score, price)) or not (
                    0 <= score <= 100 and price > 0):
                rejected("INVALID_MODERATE_SCORE")
                continue
            qualified.append({
                "symbol": sym, "industry": industry, "score": score,
                "close": price, "date": scored["asof"],
                "rsi": scored.get("rsi"), "adx": scored.get("adx"),
                "return20": evidence[sym]["return20"],
                "return_window": evidence[sym]["return_window"],
            })
            coverage[industry]["qualified"] += 1
        except (ValueError, TypeError, KeyError, IndexError, OverflowError,
                ArithmeticError, AttributeError):
            rejected("INVALID_TECHNICAL_OR_SCORE_DATA")
        except Exception:
            # No symbols, user inputs, holdings or provider text enter logs.
            rejected("MODERATE_EVALUATOR_UNAVAILABLE")

    qualified.sort(key=lambda row: (-row["score"], row["symbol"]))
    chosen, counts = [], {}
    for row in qualified:
        industry = row["industry"]
        if counts.get(industry, 0) >= MAX_PER_INDUSTRY:
            continue
        chosen.append(row)
        counts[industry] = counts.get(industry, 0) + 1
        if len(chosen) >= MAX_RESULTS:
            break

    out["qualified"] = len(qualified)
    out["candidates"] = chosen
    selected = {row["symbol"] for row in chosen}
    observations = []
    # Show nonqualifying comparisons first from the highest-weight industry,
    # rather than exhausting the page with the first roster industry.
    for industry in industries:
        ordered = sorted((row for row in comparisons
                          if row["industry"] == industry and row["symbol"] not in selected),
                         key=lambda row: (-row["return20"], row["symbol"]))
        observations.extend(ordered[:MAX_PER_INDUSTRY])
    out["comparisons"] = observations[:MAX_RESULTS]
    out["failure_counts"] = failures
    out["status"] = "SCREENED"
    return out
