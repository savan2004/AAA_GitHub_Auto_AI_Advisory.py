"""Collect and store a real Nifty 500 EOD snapshot in separate Neon table.

CI only; no Telegram bot import, send, restart or portfolio storage changes.
"""
import logging

from market_breadth import (
    _collect_breadth,
    _load_daily_snapshot,
    _valid_daily_snapshot,
    expected_completed_session,
    persist_daily_snapshot,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    expected = str(expected_completed_session())
    report = _collect_breadth()  # genuine prices; full verified universe, no placeholders
    if not _valid_daily_snapshot(report, expected):
        raise RuntimeError("Breadth NOT READY: verified Nifty 500 coverage/session incomplete")
    persist_daily_snapshot(report)  # only independent msk_breadth_snapshots_v1 table
    stored = _load_daily_snapshot(expected)
    if not _valid_daily_snapshot(stored, expected):
        raise RuntimeError("Breadth NOT READY: Neon read-back failed")
    print("MSK_BREADTH_READY session={} valid={} requested={} coverage={:.1f}%".format(
        stored["session"], stored["valid"], stored["requested"], stored["coverage"]
    ))


if __name__ == "__main__":
    main()
