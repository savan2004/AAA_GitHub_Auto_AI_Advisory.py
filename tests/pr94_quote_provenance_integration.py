"""Explicit opt-in real-provider quote provenance smoke, never a unit fixture.

No Telegram, customer data, paid feed, secrets, or public publication. This is
not cross-provider price reconciliation or a production quote-accuracy score.
Only source, exchange as-of time and pass/fail are persisted.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from data_engine import get_verified_quote

EQUITIES = ("SBIN", "RELIANCE", "TCS")
INDEXES = ("^NSEI", "^NSEBANK")
ALLOWED_SOURCES = {"Yahoo Finance v8", "NSE India"}
ALLOWED_STATUSES = {"recent_observation", "last_observed_session"}


def main():
    if os.getenv("MSK_ALLOW_EXTERNAL_MARKET_TEST") != "YES":
        raise SystemExit("Real quote provider test requires explicit opt-in")

    records = []
    for symbol in (*EQUITIES, *INDEXES):
        try:
            quote = get_verified_quote(symbol)
            if quote is None:
                raise ValueError("No source-and-time verified quote")
            if (quote.get("quote_source") not in ALLOWED_SOURCES
                    or quote.get("quote_status") not in ALLOWED_STATUSES
                    or not isinstance(quote.get("quote_asof_ist"), str)
                    or type(quote.get("price")) not in (int, float)
                    or not math.isfinite(quote["price"])
                    or quote["price"] <= 0):
                raise ValueError("Invalid provider provenance")
            asof = datetime.fromisoformat(quote["quote_asof_ist"])
            if asof.tzinfo is None:
                raise ValueError("Missing exchange timezone")
            records.append({
                "symbol": symbol, "ok": True, "source": quote["quote_source"],
                "asof_ist": quote["quote_asof_ist"],
                "observation_class": quote["quote_status"],
            })
        except Exception as exc:
            records.append({
                "symbol": symbol, "ok": False,
                "reason": type(exc).__name__, "source": None, "asof_ist": None,
            })

    good = sum(rec["ok"] for rec in records)
    good_indices = all(rec["ok"] for rec in records if rec["symbol"] in INDEXES)
    threshold = 4  # At least 4 of 5 real quotes, both indices mandatory.
    summary = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "test_kind": "real_provider_quote_timestamp_and_provenance",
        "requested": len(records), "source_time_verified": good,
        "coverage_percent": 100 * good / len(records),
        "all_indices_verified": good_indices,
        "note": "Actual quotes withheld from artifact; not production E2E validation",
        "records": records,
    }
    output = Path(os.getenv("MSK_QUOTE_TEST_REPORT", "pr94-quote-provenance.json"))
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if good < threshold or not good_indices:
        print("FAIL: insufficient source-and-exchange-time quote verification",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
