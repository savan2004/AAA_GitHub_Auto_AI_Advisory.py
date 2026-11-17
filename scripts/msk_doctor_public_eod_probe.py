"""Public-only, non-customer Doctor EOD freshness probe.

Does not accept holdings, Telegram IDs, uploaded content or client symbols.
Informational smoke: GitHub network reachability is not Render reachability.
"""
import logging
from datetime import datetime

from portfolio_doctor_live import IST, PortfolioDoctorError, recover_fresh_daily_for_doctor, verified_completed_daily_frame
from market_breadth import expected_completed_session
from market_data import bulk_history

PUBLIC_DEMO_SYMBOL = "INFY"


def main():
    logging.disable(logging.CRITICAL)
    now = datetime.now(IST)
    expected = expected_completed_session(now)
    frames = bulk_history([PUBLIC_DEMO_SYMBOL], "1y", chunk_size=1,
                          retries=0, max_fallbacks=0, download_timeout=12)
    source = "primary"
    try:
        df = verified_completed_daily_frame(frames.get(PUBLIC_DEMO_SYMBOL), now=now)
    except PortfolioDoctorError:
        source = "recovered"
        df = recover_fresh_daily_for_doctor(PUBLIC_DEMO_SYMBOL, now=now)
    got = str(df.index[-1].date()) if df is not None and len(df) else "UNAVAILABLE"
    print("MSK_DOCTOR_PUBLIC_EOD_PROBE expected={} got={} path={} complete={}".format(
        expected, got, source, got == str(expected)))
    if got != str(expected):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
