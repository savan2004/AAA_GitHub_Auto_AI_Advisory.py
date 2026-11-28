"""Read-only Nifty 100 stage. Existing 10-stock pilot is reused per batch.

Full observed Nifty 100 membership, checked against the official Nifty 500
identity roster. The full 100-stock denominator is never reduced. Exchange
archive data are shared across batches; one logical fetch per session.
"""
import argparse
from datetime import date
import hashlib
import json

from msk_central_missing_v4 import build_recovery_bank, write_recovery_bank

TARGET = 100
BATCH = 10


def plan_batches(symbols, roster):
    members = tuple(symbols)
    if len(members) != TARGET or len(set(members)) != TARGET:
        raise ValueError("exact 100 unique Nifty 100 members required")
    if any(s not in roster for s in members):
        raise ValueError("Nifty 100 identity not present in Nifty 500 roster")
    ordered = tuple(sorted(members))
    return tuple(ordered[i:i+BATCH] for i in range(0,TARGET,BATCH))


def summarize(rows, *, completed):
    counts = {k:sum(row.get(k) is True for row in rows.values()) for k in
              ("history_shape_valid","latest_raw_ohlcv_match","technical_calculated")}
    counts["fresh_financial_candidates"] = sum(row.get("financial_status")=="fresh_secondary_candidate"
                                                for row in rows.values())
    counts["partial_official_quarter_matches"] = sum(row.get("official_quarter_comparison")=="partial_match"
                                                     for row in rows.values())
    complete = (completed==TARGET and len(rows)==TARGET and
                all(counts[k]==TARGET for k in ("history_shape_valid","latest_raw_ohlcv_match",
                                              "technical_calculated","fresh_financial_candidates")))
    return counts, "pilot_observed" if complete else "pilot_incomplete"


def run(session, *, checkpoint=None):
    from scripts.msk_central_500_evidence import ROSTERS, fetch, _roster_details
    from scripts.msk_central_10_pilot import run as run_batch
    from smart_chart_universe import load_nifty100_universe
    from smart_chart_eod_recovery import final_archive_rows

    report = {"target":TARGET,"session":str(session),"stocks":{},
              "status":"withheld_roster","completed":0,
              "full_history_independently_verified":False,
              "full_fundamentals_independently_verified":False}
    try:
        members, source = load_nifty100_universe()
        if source not in ("NSE_NIFTY100_CONSTITUENTS_CSV","NSE_NIFTY50_PLUS_NEXT50"):
            raise ValueError("live official membership required")
        symbols = tuple(s for _,s in members)
        roster = {}
        for url in ROSTERS:
            try:
                raw = fetch(url,2_000_000)
                roster, series, _ = _roster_details(raw)
                batches = plan_batches(symbols,roster)
                roster_url = url
                break
            except Exception:
                continue
        else:
            raise ValueError("official roster identity match unavailable")
    except Exception as exc:
        report["error"] = type(exc).__name__
        if checkpoint: checkpoint(report)
        return report

    report.update(nifty100_source=source,roster_source=roster_url,
                  roster_sha256=hashlib.sha256(raw).hexdigest(),status="pilot_incomplete")
    for sym in symbols:
        report["stocks"][sym]={"isin":roster[sym],"series":series[sym],
            "history_shape_valid":False,"latest_raw_ohlcv_match":False,
            "technical_calculated":False,"financial_status":"not_attempted",
            "official_quarter_comparison":"not_attempted","batch_status":"not_attempted"}
    archive_cache = {}
    all_eq = tuple(s+".NS" for s in symbols if series[s]=="EQ")
    def cached_roster(url,limit):
        if url != roster_url:
            raise ValueError("use validated roster source")
        return raw
    def shared_archive(day,wanted):
        if day not in archive_cache:
            archive_cache[day] = final_archive_rows(day,all_eq)
        return {s:archive_cache[day][s] for s in wanted if s in archive_cache[day]}
    if checkpoint: checkpoint(report)
    for i,batch in enumerate(batches,1):
        try:
            part = run_batch(session,symbols=batch,fetch_roster=cached_roster,
                             fetch_archive=shared_archive)
            for sym,row in part.get("stocks",{}).items():
                if sym in batch and row.get("isin")==roster[sym]:
                    report["stocks"][sym].update(row,batch_status="observed")
        except Exception as exc:
            for sym in batch:
                report["stocks"][sym]["batch_error"] = type(exc).__name__
        report["completed"] = sum(r["batch_status"]=="observed" for r in report["stocks"].values())
        report["counts"],report["status"] = summarize(report["stocks"],completed=report["completed"])
        print("MSK_100_BATCH "+json.dumps({"batch":i,"completed":report["completed"],
               "counts":report["counts"],"status":report["status"]},sort_keys=True),flush=True)
        if checkpoint: checkpoint(report)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--session",required=True,type=date.fromisoformat)
    args=p.parse_args()
    def checkpoint(report):
        write_recovery_bank("msk_100_pilot_report.json",report)
        write_recovery_bank("msk_ai_missing_data.json",build_recovery_bank(report,args.session))
    report=run(args.session,checkpoint=checkpoint)
    print("MSK_100_PILOT "+json.dumps({k:v for k,v in report.items() if k!="stocks"},sort_keys=True),flush=True)
    raise SystemExit(0 if report["status"]=="pilot_observed" else 1)


if __name__=="__main__":
    main()
