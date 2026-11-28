"""Ten public equities only; shadow QA, no storage, AI calls or Telegram.

Existing MSK history, technical and financial adapters are reused. History
shape and latest bar match are measured separately from complete historical
independent verification. Financial secondary-provider availability is not
official field-level verification.
"""
import argparse
from datetime import date
import json
import math

PILOT = ("RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN",
         "LT","BHARTIARTL","ITC","DMART")


def technical_values(frame):
    from msk_chart_release import wilder_rsi
    from msk_indicator_math import ema, macd, directional_series
    close = frame.Close
    line, signal, _ = macd(close)
    atr, adx, _, _ = directional_series(frame.High, frame.Low, close)
    values = {"rsi14": float(wilder_rsi(close)[-1]), "macd": float(line.iloc[-1]),
              "macd_signal": float(signal.iloc[-1]), "ema20": float(ema(close,20).iloc[-1]),
              "sma50": float(close.rolling(50).mean().iloc[-1]),
              "sma200": float(close.rolling(200).mean().iloc[-1]),
              "atr14": float(atr.iloc[-1]), "adx14": float(adx.iloc[-1])}
    if not all(math.isfinite(v) for v in values.values()):
        return {}
    if not (0 <= values["rsi14"] <= 100 and 0 <= values["adx14"] <= 100
            and values["atr14"] >= 0 and min(values["ema20"],values["sma50"],values["sma200"]) > 0):
        return {}
    return values


def financial_status(statements):
    if not statements:
        return "missing"
    if (statements.get("freshness") or {}).get("status") != "FRESH":
        return "stale_or_undated"
    quarters = statements.get("quarters") or []
    if not quarters:
        return "missing"
    try:
        if any(isinstance(quarters[-1].get(k),bool) or not math.isfinite(float(quarters[-1].get(k)))
               for k in ("revenue","net_profit","eps")):
            return "partial"
    except (ValueError,TypeError):
        return "partial"
    return "fresh_secondary_candidate"


def run(session, *, fetch_roster=None, fetch_archive=None, fetch_histories=None,
        fetch_statements=None):
    from scripts.msk_central_500_evidence import ROSTERS, fetch, _roster_details
    from smart_chart_eod_recovery import final_archive_rows
    from market_data import bulk_history
    from msk_chart_release import validated_daily_history
    from portfolio_doctor_bhavcopy import official_daily_bar_matches
    from research_data import get_statements
    from research_filing_evidence import reconcile_official_quarter

    fetch_roster = fetch_roster or fetch
    fetch_archive = fetch_archive or final_archive_rows
    fetch_histories = fetch_histories or bulk_history
    fetch_statements = fetch_statements or get_statements
    roster = {}
    roster_source = None
    for url in ROSTERS:
        try:
            roster, series, _ = _roster_details(fetch_roster(url, 2_000_000))
            if all(s in roster and series[s]=="EQ" for s in PILOT):
                roster_source = url
                break
        except Exception:
            continue
    if not roster_source:
        return {"status":"withheld_roster","target":10,"stocks":{}}
    try:
        official = fetch_archive(session, [s+".NS" for s in PILOT])
    except Exception:
        official = {}
    try:
        frames = fetch_histories(PILOT, period="1y", chunk_size=10, retries=0,
                                 adjusted=False, max_fallbacks=10, download_timeout=10)
    except Exception:
        frames = {}
    rows = {}
    for sym in PILOT:
        entry = {"isin":roster[sym], "history_bars":0,"history_shape_valid":False,
                 "latest_raw_ohlcv_match":False, "technical_calculated":False,
                 "financial_status":"missing", "official_quarter_comparison":"not_attempted"}
        try:
            data = validated_daily_history(frames.get(sym), str(session))
            if data is not None:
                entry["history_bars"] = len(data)
                entry["history_shape_valid"] = True
                entry["latest_raw_ohlcv_match"] = official_daily_bar_matches(
                    data.iloc[-1], official.get(sym+".NS"))
                if entry["latest_raw_ohlcv_match"] and len(data)>=200:
                    entry["technical_calculated"] = bool(technical_values(data))
        except Exception as exc:
            entry["history_error"] = type(exc).__name__
        try:
            stm = fetch_statements(sym,today=session)
            entry["financial_status"] = financial_status(stm)
            if stm:
                comparison = reconcile_official_quarter(sym, stm)
                entry["official_quarter_comparison"] = comparison.get("status","unknown")
        except Exception as exc:
            entry["financial_error"] = type(exc).__name__
        rows[sym] = entry
        print("MSK_10_STOCK "+json.dumps({"symbol":sym,**entry},sort_keys=True),flush=True)
    counts = {k:sum(r[k] is True for r in rows.values())
              for k in ("history_shape_valid","latest_raw_ohlcv_match","technical_calculated")}
    counts["fresh_financial_candidates"] = sum(r["financial_status"]=="fresh_secondary_candidate" for r in rows.values())
    counts["partial_official_quarter_matches"] = sum(r["official_quarter_comparison"]=="partial_match" for r in rows.values())
    return {"target":10,"session":str(session),"roster_source":roster_source,"stocks":rows,
            "counts":counts,"status":"pilot_observed" if counts["technical_calculated"]==10
            and counts["fresh_financial_candidates"]==10 else "pilot_incomplete",
            "full_history_independently_verified":False,
            "full_fundamentals_independently_verified":False}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--session",required=True,type=date.fromisoformat)
    args=p.parse_args()
    result=run(args.session)
    print("MSK_10_PILOT "+json.dumps(result,sort_keys=True),flush=True)
    raise SystemExit(0 if result["status"]=="pilot_observed" else 1)
