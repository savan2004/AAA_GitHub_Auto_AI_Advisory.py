"""Read-only retrospective Yahoo full-vs-incremental history probe.

No database, Telegram, alternative providers, cache writes or trading actions.
The historical prefix is reconstructed from the full response, NOT a stored
prior snapshot. This measures provider parity, not independent NSE accuracy
or production speed. Supply independently confirmed completed NSE sessions.
"""
import argparse
from datetime import date, datetime
import json
import re
from time import perf_counter
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import bulk_history, to_yahoo
from msk_chart_release import validated_daily_history
from msk_history_incremental_pilot import merge_verified_history


def run_probe(symbols, previous_session, new_sessions, fetch=bulk_history):
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols))
    if not 1 <= len(symbols) <= 10 or any(
        not re.fullmatch(r"[A-Z0-9&-]+(?:\.NS)?", s) for s in symbols
    ) or len({to_yahoo(s) for s in symbols}) != len(symbols):
        raise ValueError("Supply 1-10 distinct NSE equity tickers")
    prior = date.fromisoformat(previous_session)
    days = [date.fromisoformat(s) for s in new_sessions]
    if not days or days != sorted(set(days)) or days[0] <= prior:
        raise ValueError("Supply ascending, distinct completed sessions after prior")
    session = days[-1].isoformat()
    options = dict(chunk_size=len(symbols), retries=0, max_fallbacks=0,
                   cache_ttl=0, adjusted=False, download_timeout=12)
    start = perf_counter()
    full = fetch(symbols, period="1y", **options)
    full_seconds = perf_counter() - start
    start = perf_counter()
    delta = fetch(symbols, period="5d", **options)
    delta_seconds = perf_counter() - start
    results = {}
    for symbol in symbols:
        baseline = validated_daily_history(full.get(symbol), session)
        incoming = delta.get(symbol)
        record = {"status": "withheld", "full_rows": 0 if baseline is None else len(baseline),
                  "delta_rows": 0 if incoming is None else len(incoming)}
        results[symbol] = record
        if baseline is None or incoming is None:
            record["reason"] = "missing_or_invalid_provider_history"
            continue
        # Exclude current/future candles before comparison. The merge still
        # requires every explicitly supplied new session and an exact overlap.
        incoming = incoming.loc[incoming.index <= pd.Timestamp(session)].copy()
        previous = baseline.loc[baseline.index <= pd.Timestamp(prior)].copy()
        merged = merge_verified_history(previous, incoming, prior.isoformat(),
                                        session, [d.isoformat() for d in days])
        if merged is None:
            record["reason"] = "incremental_validation_rejected"
            continue
        try:
            pd.testing.assert_frame_equal(merged, baseline, check_exact=True,
                                          check_dtype=False, check_freq=False)
        except AssertionError:
            record["reason"] = "full_history_mismatch"
            continue
        record["status"] = "matched"
        record["merged_rows"] = len(merged)
    matched = sum(r["status"] == "matched" for r in results.values())
    return {"mode": "retrospective_same_provider_not_live_certification",
            "provider": "Yahoo unadjusted only", "previous_session": prior.isoformat(),
            "session": session, "expected_sessions": [d.isoformat() for d in days],
            "requested": len(symbols), "matched": matched, "withheld": len(symbols)-matched,
            "full_fetch_seconds": round(full_seconds, 3),
            "delta_fetch_seconds": round(delta_seconds, 3), "symbols": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--previous-session", required=True)
    parser.add_argument("--sessions", nargs="+", required=True,
                        help="Every independently confirmed completed NSE session after prior")
    args = parser.parse_args()
    try:
        # Conservative: do not use today's candle even after market close.
        if any(date.fromisoformat(s) >= datetime.now(ZoneInfo("Asia/Kolkata")).date()
               for s in [args.previous_session, *args.sessions]):
            raise ValueError("Use completed sessions before today's IST date")
        report = run_probe(args.symbols, args.previous_session, args.sessions)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["matched"] == report["requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
