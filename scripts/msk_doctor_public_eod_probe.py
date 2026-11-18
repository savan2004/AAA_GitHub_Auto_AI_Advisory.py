"""Public-only, non-customer Doctor EOD freshness probe.

Does not accept holdings, Telegram IDs, uploaded content or client symbols.
Informational smoke: GitHub network reachability is not Render reachability.
"""
import logging
from datetime import datetime

from portfolio_doctor_live import IST, PortfolioDoctorError, recover_fresh_daily_for_doctor, verified_completed_daily_frame, expected_doctor_session
from market_data import bulk_history

PUBLIC_DEMO_SYMBOL = "INFY"


def main():
    logging.disable(logging.CRITICAL)
    now = datetime.now(IST)
    expected = expected_doctor_session(now)
    frames = bulk_history([PUBLIC_DEMO_SYMBOL], "1y", chunk_size=1,
                          retries=0, max_fallbacks=0, download_timeout=12)
    from market_data import _clean_frame, to_yahoo
    from data_engine import _yahoo_v8_hist

    source = "primary"
    primary = frames.get(PUBLIC_DEMO_SYMBOL)
    primary_last = str(primary.index[-1].date()) if primary is not None and not primary.empty else "NONE"
    print("MSK_DOCTOR_PUBLIC_EOD_PRIMARY last={} rows={}".format(
        primary_last, len(primary) if primary is not None else 0))
    try:
        df = verified_completed_daily_frame(primary, now=now)
    except PortfolioDoctorError:
        source = "recovered"
        providers = (
            ("Yahoo-v8", lambda: _yahoo_v8_hist(to_yahoo(PUBLIC_DEMO_SYMBOL), period="1y")),
        )
        def observed(name, fetch):
            try:
                raw = fetch()
                frame = _clean_frame(raw)
                last = str(frame.index[-1].date()) if frame is not None and not frame.empty else "NONE"
                print("MSK_DOCTOR_PUBLIC_EOD_SOURCE provider={} last={} rows={}".format(
                    name, last, len(frame) if frame is not None else 0))
                return frame
            except Exception as exc:
                print("MSK_DOCTOR_PUBLIC_EOD_SOURCE provider={} exception_type={}".format(
                    name, type(exc).__name__))
                return None
        df = recover_fresh_daily_for_doctor(
            PUBLIC_DEMO_SYMBOL, now=now,
            fetchers=tuple((lambda name=name, fetch=fetch: observed(name, fetch))
                           for name, fetch in providers))
    if df is None:
        from portfolio_doctor_bhavcopy import recover_from_public_bhavcopy
        official = recover_from_public_bhavcopy(
            {PUBLIC_DEMO_SYMBOL: primary}, [PUBLIC_DEMO_SYMBOL], now=now)
        df = official.get(PUBLIC_DEMO_SYMBOL)
        if df is not None:
            source = "verified-nse-bhavcopy"
    got = str(df.index[-1].date()) if df is not None and len(df) else "UNAVAILABLE"
    print("MSK_DOCTOR_PUBLIC_EOD_PROBE expected={} got={} path={} complete={}".format(
        expected, got, source, got == str(expected)))
    if got != str(expected):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
