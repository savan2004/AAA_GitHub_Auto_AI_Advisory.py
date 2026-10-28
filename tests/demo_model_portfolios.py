"""Deterministic demonstration of the exact PR #38 selector/Telegram formatter.

Offline synthetic inputs only; none of these symbols, prices or metrics are
real market observations or recommendations. Run: python tests/demo_model_portfolios.py
"""
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_portfolios import format_model, select_model


FIXED_TIME = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc).timestamp()
SECTORS = ("Technology", "Consumer", "Industrials", "Healthcare", "Energy")


def fixture(group, index):
    sector = SECTORS[index // 2]
    data = {
        "symbol": f"{group}{index + 1:02d}",
        "name": f"Imaginary {group} Company {index + 1}",
        "sector": sector,
        "asof": "26-Oct-2026",
        "provider": "Synthetic approved research fixture",
        "provider_url": "https://www.nseindia.com/reports/fixture",
        "fiscal_sales_period": "2026-03",
        "fiscal_sales_previous": "2025-03",
        "balance_sheet_period": "2026-09",
        "fetched_at": FIXED_TIME - 300,
        "timing": "Constructive" if index % 3 != 2 else "Wait",
        "price": 200.0 + 11 * index,
        "turnover": 30.0 + index,
    }
    if group == "CONS":
        data.update(roe=12.0, growth=0.0, debt=0.60,
                    cap=21_000.0, pe=49.0, vol=34.0)
    elif group == "MOD":
        data.update(roe=11.0, growth=9.0, debt=0.90,
                    cap=9_000.0, pe=14.0, vol=21.0)
    elif group == "AGGR":
        data.update(roe=11.0, growth=30.0, debt=1.40,
                    cap=2_500.0, pe=55.0, vol=45.0)
    else:
        raise ValueError("Unsupported fixture")
    return data


def main():
    rows = [fixture(group, index)
            for group in ("CONS", "MOD", "AGGR")
            for index in range(10)]
    snap = {"rows": rows, "checked": len(rows), "universe": len(rows)}
    for profile, expected_group in (
        ("conservative", "CONS"),
        ("moderate", "MOD"),
        ("aggressive", "AGGR"),
    ):
        model = select_model(rows, profile, now=FIXED_TIME)
        messages, symbols = format_model(model, snap)
        assert model["status"] == "complete", f"{profile}: {model['rejected']}"
        assert len(messages) == 4 and len(symbols) == 10
        assert all(symbol.startswith(expected_group) for symbol in symbols), (
            profile, symbols
        )
        print(f"\n{'=' * 20} {profile.upper()} / SIMULATED {'=' * 20}")
        for number, message in enumerate(messages, 1):
            print(f"\n--- Telegram message {number} of {len(messages)} ---")
            print(message)
        print(f"\nSIMULATOR SUMMARY {profile}: "
              f"holdings={len(symbols)} invested_rupees="
              f"{sum(h['allocation_rupees'] for h in model['holdings'])} "
              f"cash_rupees={model['cash_rupees']}")

    # The very same code must refuse to fabricate ten picks from nine.
    incomplete = select_model(rows[:9], "conservative", now=FIXED_TIME)
    error_msgs, symbols = format_model(incomplete, {"checked": 9, "universe": 30})
    assert incomplete["status"] == "insufficient_verified_candidates"
    assert incomplete["holdings"] == [] and symbols == []
    print("\n==================== INCOMPLETE DATA / SIMULATED ====================")
    print(error_msgs[0])
    print("\nSIMULATOR ALL ASSERTIONS PASSED; no real stock prices or Telegram sends.")


if __name__ == "__main__":
    main()
