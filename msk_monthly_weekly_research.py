"""MSK lean research cycle: one AI idea call/month, weekly rule-based technical scan.

Reuses existing NSE index membership, fundamental gate, bulk OHLCV downloader
and technical rules. Not a customer portfolio, trade signal, verified issuer
audit, or data redistribution authorization.
"""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import os
import re

SEGMENTS = {"large": "conservative", "mid": "moderate", "small": "aggressive"}
_MODEL = "openai/gpt-oss-120b"  # Groq's supported replacement for retired Llama free-tier models
_SYMBOL = re.compile(r"[A-Z0-9&_.-]{1,30}\Z")
_MAX_IDEAS = 12


class CycleUnavailable(ValueError):
    pass


def _valid_universe(profile, universe):
    if (not isinstance(universe, dict) or
            universe.get("source") != "Official NSE constituent CSV" or
            universe.get("stale") or
            universe.get("cached") and universe.get("stale")):
        raise CycleUnavailable("OFFICIAL_INDEX_UNAVAILABLE")
    symbols = universe.get("symbols")
    if (not isinstance(symbols, (tuple, list)) or
            not 95 <= len(symbols) <= 105 or
            len(set(symbols)) != len(symbols) or
            not all(isinstance(s, str) and _SYMBOL.fullmatch(s) for s in symbols)):
        raise CycleUnavailable("OFFICIAL_INDEX_COVERAGE")
    return list(symbols)


def groq_one_monthly_call(universes, *, key=None, create=None):
    """One non-retried AI inference total across all three segments.

    AI receives only official tickers and industries, not invented finance
    figures. An unavailable secret or provider halts the monthly run safely.
    """
    key = key if key is not None else os.getenv("GROQ_API_KEY")
    if not key:
        raise CycleUnavailable("MONTHLY_AI_KEY_MISSING")
    if create is None:
        from groq import Groq
        create = Groq(api_key=key, timeout=22, max_retries=0).chat.completions.create
    choices = {}
    for segment, universe in universes.items():
        symbols = _valid_universe(SEGMENTS[segment], universe)
        industry = universe.get("industries") or {}
        choices[segment] = [
            [s, str(industry.get(s) or "Unclassified")[:50]] for s in symbols
        ]
    request = (
        "Suggest exactly 12 research candidate NSE symbols per group. "
        "Use ONLY symbols from these official index membership lists. "
        "Consider diversified industries and known business risks. "
        "Do not claim live prices, audited financials, scores, guaranteed returns "
        "or special knowledge of events after training. "
        "JSON object only: {\"large\":[...],\"mid\":[...],\"small\":[...]}. "
        "These are tentative ideas, NOT buy recommendations.\n"
        + json.dumps(choices, separators=(",", ":"))
    )
    try:
        out = create(
            model=_MODEL, temperature=0.1, max_completion_tokens=1100,
            response_format={"type": "json_object"},
            messages=[
                {"role":"system","content":"Return exact valid JSON only. No invented stock codes or market facts."},
                {"role":"user","content":request},
            ],
        )
        text = out.choices[0].message.content
        response = json.loads(text)
    except Exception:
        raise CycleUnavailable("MONTHLY_AI_PROVIDER_FAILED") from None
    return validate_ai_ideas(response, universes)


def validate_ai_ideas(proposals, universes):
    if not isinstance(proposals, dict) or set(proposals) != set(SEGMENTS):
        raise CycleUnavailable("MONTHLY_AI_SCHEMA")
    validated = {}
    all_seen = set()
    for segment in SEGMENTS:
        members = set(_valid_universe(SEGMENTS[segment], universes[segment]))
        ideas = proposals[segment]
        if (not isinstance(ideas, list) or not 5 <= len(ideas) <= _MAX_IDEAS
                or len(set(map(str, ideas))) != len(ideas)):
            raise CycleUnavailable("MONTHLY_AI_COUNT")
        if any(not isinstance(s, str) or s not in members
               or s in all_seen for s in ideas):
            raise CycleUnavailable("MONTHLY_AI_NOT_IN_INDEX")
        all_seen.update(ideas)
        validated[segment] = ideas
    return validated


def make_monthly(now, *, universe_provider=None, ai_provider=None,
                 fundamental_provider=None):
    """Only one AI call. Fundamental data checked once, not on every tap."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise CycleUnavailable("TIMEZONE_REQUIRED")
    if universe_provider is None:
        from index_constituents import get_index_universe
        universe_provider = get_index_universe
    if fundamental_provider is None:
        from top5_company_ratings import fetch_company_evidence
        fundamental_provider = fetch_company_evidence
    universes = {k: universe_provider(profile) for k, profile in SEGMENTS.items()}
    for k, u in universes.items():
        _valid_universe(SEGMENTS[k], u)
    ideas = validate_ai_ideas(
        (ai_provider or groq_one_monthly_call)(universes), universes)
    reviewed, errors = {}, {}
    for segment, symbols in ideas.items():
        u = universes[segment]
        reviewed[segment] = []
        errors[segment] = {}
        for symbol in symbols:
            industry = (u.get("industries") or {}).get(symbol) or "Unclassified"
            try:
                item = fundamental_provider(
                    symbol, industry, SEGMENTS[segment], now.date())
            except Exception:
                item = None
            if (not isinstance(item, dict) or
                    item.get("fund_status") != "VALIDATED" or
                    not isinstance(item.get("fund_score"), (int, float)) or
                    not math.isfinite(item["fund_score"]) or
                    not 0 <= item["fund_score"] <= 100):
                errors[segment][symbol] = "FUNDAMENTALS_NOT_VALIDATED"
                continue
            reviewed[segment].append({
                "symbol":symbol, "industry":industry,
                "fund_score":item["fund_score"],
                "fundamental_period":str(item.get("quarter") or "unverified"),
                "fund_source":str(item.get("fund_source") or "unverified")[:120],
                "fund_status":"SECONDARY_DATA_VALIDATED_ONLY",
            })
    return {
        "schema":1, "month":now.date().strftime("%Y-%m"),
        "asof":now.date().isoformat(), "model":_MODEL,
        "universe_hashes": {
            k:sha256("\n".join(sorted(u["symbols"])).encode()).hexdigest()
            for k,u in universes.items()},
        "ai_calls":1, "ideas":ideas,
        "reviewed":reviewed, "skipped":errors,
        "publication_status":"WITHHELD",
        "limitation":"Public secondary fundamentals are not independently issuer-approved; no customer recommendations.",
    }


def make_weekly(monthly, now, *, price_provider=None, technical_provider=None):
    """No AI, no repeated fundamental requests. Completed EOD candles only."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise CycleUnavailable("TIMEZONE_REQUIRED")
    if (not isinstance(monthly, dict) or
            monthly.get("schema") != 1 or
            monthly.get("month") != now.date().strftime("%Y-%m") or
            monthly.get("publication_status") != "WITHHELD" or
            monthly.get("ai_calls") != 1 or
            set(monthly.get("reviewed", {})) != set(SEGMENTS) or
            set(monthly.get("ideas", {})) != set(SEGMENTS)):
        raise CycleUnavailable("MONTHLY_SEED_MISSING_OR_STALE")
    if price_provider is None:
        from market_data import bulk_history
        price_provider = bulk_history
    if technical_provider is None:
        from top5_technical_ratings import technical_candidate
        technical_provider = technical_candidate
    symbols = [r["symbol"] for group in monthly["reviewed"].values() for r in group]
    if len(symbols) != len(set(symbols)) or len(symbols) > 36:
        raise CycleUnavailable("MONTHLY_SEED_DUPLICATE")
    if not symbols:
        return {"status":"NO_VERIFIED_FUNDAMENTAL_CANDIDATES",
                "week":now.date().isoformat(),"publication_status":"WITHHELD","technical":{}}
    try:
        bars = price_provider(symbols, "6mo", chunk_size=35, retries=1,
                              max_fallbacks=12, adjusted=False)
    except Exception:
        raise CycleUnavailable("WEEKLY_PRICE_SOURCE_UNAVAILABLE") from None
    if not isinstance(bars, dict):
        raise CycleUnavailable("WEEKLY_PRICE_SOURCE_UNAVAILABLE")
    selections, missing = {}, {}
    for segment, records in monthly["reviewed"].items():
        graded, rejected = [], {}
        for item in records:
            symbol = item.get("symbol")
            if symbol not in monthly["ideas"][segment]:
                raise CycleUnavailable("MONTHLY_SEED_IDENTITY_INVALID")
            if symbol not in bars:
                rejected[symbol] = "COMPLETED_BARS_UNAVAILABLE"
                continue
            try:
                tech = technical_provider(
                    symbol, bars[symbol], SEGMENTS[segment], now)
            except Exception:
                tech = None
            if not isinstance(tech, dict) or tech.get("sym") != symbol:
                rejected[symbol] = "TECHNICAL_NOT_QUALIFIED"
                continue
            stamp = tech.get("asof")
            try:
                bar_day = date.fromisoformat(stamp)
            except (TypeError, ValueError):
                rejected[symbol] = "TECHNICAL_SESSION_INVALID"
                continue
            if not 0 <= (now.date() - bar_day).days <= 7:
                rejected[symbol] = "TECHNICAL_STALE"
                continue
            score = tech.get("tech_score")
            if (not isinstance(score, (int, float))
                    or not math.isfinite(score) or not 0 <= score <= 100):
                rejected[symbol] = "TECHNICAL_SCORE_INVALID"
                continue
            graded.append({
                "symbol":symbol, "industry":item["industry"],
                "fund_score":item["fund_score"],
                "tech_score":score, "signal":tech.get("signal","WAIT"),
                "close_date":stamp, "research_status":"WATCHLIST_ONLY",
            })
        graded.sort(key=lambda a:(-a["tech_score"],-a["fund_score"],a["symbol"]))
        selections[segment] = graded[:5]
        missing[segment] = rejected
    return {
        "status":"WEEKLY_TECHNICAL_RESEARCH",
        "month":monthly["month"], "week":now.date().isoformat(),
        "ai_calls":0, "fundamental_fetches":0,
        "technical":selections, "skipped":missing,
        "publication_status":"WITHHELD",
        "limitation":"Research watchlist from secondary data, not a customer recommendation or executable buy signal.",
    }


def main(argv=None):
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["monthly","weekly"])
    parser.add_argument("--input",default="msk-monthly-research.json")
    parser.add_argument("--output",default=None)
    opts=parser.parse_args(argv)
    now=datetime.now(timezone.utc)
    try:
        if opts.mode=="monthly":
            result=make_monthly(now)
        else:
            monthly=json.loads(Path(opts.input).read_text(encoding="utf-8"))
            result=make_weekly(monthly,now)
        target=opts.output or (
            "msk-monthly-research.json" if opts.mode=="monthly" else "msk-weekly-research.json")
        Path(target).write_text(json.dumps(result,indent=2,sort_keys=True),encoding="utf-8")
        print(json.dumps({"status":result.get("status","MONTHLY_RESEARCH_PREPARED"),
                          "publication_status":"WITHHELD",
                          "file":target,"ai_calls":result["ai_calls"]},sort_keys=True))
        return 0
    except (CycleUnavailable, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status":"RESEARCH_CYCLE_UNAVAILABLE",
                          "publication_status":"WITHHELD"}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
