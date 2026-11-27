"""Public-only, non-customer Doctor EOD freshness probe.

Does not accept holdings, Telegram IDs, uploaded content or client symbols.
Informational smoke: GitHub network reachability is not Render reachability.
"""
import logging
from datetime import datetime

from portfolio_doctor_live import IST, expected_doctor_session
from market_data import bulk_history

PUBLIC_DEMO_SYMBOL = "INFY"


def verify_final_archive(frame, *, now, expected, alternate_fetch=None):
    """Force only the legacy transport to be unavailable; fetch real NSE ZIPs.

    This public-symbol acceptance probe exercises the new parser and recovery
    branch even when the ordinary Yahoo history already contains the last day.
    No simulated prices, customer data, database writes or Telegram sends.
    """
    from types import SimpleNamespace
    import requests
    from portfolio_doctor_bhavcopy import (FINAL_ARCHIVE_HOSTS, fetch_nse_bhavcopy_rows,
                                           stitch_one_verified_day)
    from portfolio_doctor_live import previous_doctor_session

    previous = previous_doctor_session(expected)
    official_prefixes = tuple(host + "/content/cm/BhavCopy_NSE_CM_0_0_0_"
                              for host in FINAL_ARCHIVE_HOSTS)
    requested = []

    def archive_only(url, **kwargs):
        if not url.startswith(official_prefixes):
            # Test-only injection: exercise fallback, not legacy availability.
            return SimpleNamespace(ok=False)
        requested.append(url)
        response = requests.get(url, **kwargs)
        print("MSK_DOCTOR_FINAL_UDIFF_HTTP status={} url={}".format(
            response.status_code, url))
        return response

    rows = {}
    streamed = []
    for day in (previous, expected):
        result = fetch_nse_bhavcopy_rows(day, [PUBLIC_DEMO_SYMBOL], get=archive_only)
        if PUBLIC_DEMO_SYMBOL not in result:
            # Same public dated UDiFF file, independent bounded streaming parser.
            # No legacy substitution, no other session, and no price estimate.
            from smart_chart_eod_recovery import final_archive_rows
            secondary = final_archive_rows(day, [PUBLIC_DEMO_SYMBOL + ".NS"])
            row = secondary.get(PUBLIC_DEMO_SYMBOL + ".NS")
            if row is None:
                raise SystemExit("MSK_DOCTOR_FINAL_UDIFF_FAIL session={} archive_unverified".format(day))
            streamed.append(day.isoformat())
        else:
            row = result[PUBLIC_DEMO_SYMBOL]
        rows[day] = row
    expected_files = {f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
                      for day in (previous, expected)}
    if (not 2 <= len(requested) <= 4 or len(set(requested)) != len(requested)
            or {url.rsplit('/', 1)[-1] for url in requested} != expected_files):
        raise SystemExit("MSK_DOCTOR_FINAL_UDIFF_FAIL archive_request_contract")

    from portfolio_doctor_bhavcopy import official_daily_bar_matches
    import pandas as pd

    def reconcile(candidate, label):
        # Reject malformed raw history before slicing. Never hide duplicate
        # sessions or reorder rows to make a failed provider appear valid.
        if (not isinstance(candidate, pd.DataFrame) or candidate.empty
                or not isinstance(candidate.index, pd.DatetimeIndex)
                or candidate.index.hasnans or candidate.index.has_duplicates
                or not candidate.index.is_monotonic_increasing
                or len(set(candidate.index.date)) != len(candidate)):
            print("MSK_DOCTOR_PROVIDER_REJECTED provider={} reason=invalid_history".format(label))
            return None
        current = candidate.loc[candidate.index.date == expected]
        if len(current) and not official_daily_bar_matches(current.iloc[-1], rows[expected]):
            print("MSK_DOCTOR_PROVIDER_REJECTED provider={} reason=official_ohlcv_mismatch".format(label))
            route = "official-recovery-conflict"
        elif len(current):
            route = "official-match"
        else:
            route = "official-recovery-stale"
        # The rejected current candle never enters the replacement history.
        # The strict stitch independently proves prior OHLCV, PreviousClose,
        # corporate-action gap, exact session and final official OHLCV.
        history = candidate.loc[candidate.index.date <= previous]
        recovered = stitch_one_verified_day(
            history, prior=rows[previous], latest=rows[expected],
            previous_day=previous, expected_day=expected, now=now)
        if recovered is None or recovered.index[-1].date() != expected:
            print("MSK_DOCTOR_PROVIDER_REJECTED provider={} reason=prior_overlap_or_continuity".format(label))
            return None
        for field in ("Open", "High", "Low", "Close", "Volume"):
            if float(recovered.iloc[-1][field]) != rows[expected][field]:
                return None
        recovered.attrs["doctor_eod_path"] = label + ":" + route
        return recovered

    recovered = reconcile(frame, "primary")
    if recovered is None and alternate_fetch is not None:
        try:
            alternate = alternate_fetch()
        except Exception as exc:
            print("MSK_DOCTOR_PUBLIC_EOD_SOURCE provider=Yahoo-v8 exception_type={}".format(
                type(exc).__name__))
            alternate = None
        recovered = reconcile(alternate, "alternate")
    if recovered is None:
        raise SystemExit("MSK_DOCTOR_FINAL_UDIFF_FAIL prior_overlap_or_continuity")
    print("MSK_DOCTOR_FINAL_UDIFF_TRANSPORT streaming_fallback_sessions={}".format(",".join(streamed) or "none"))
    print("MSK_DOCTOR_FINAL_UDIFF_PASS symbol={} prior={} session={} close={} volume={} rows={} path={}".format(
        PUBLIC_DEMO_SYMBOL, previous, expected, rows[expected]["Close"],
        rows[expected]["Volume"], len(recovered), recovered.attrs["doctor_eod_path"]))
    return recovered


def main():
    logging.disable(logging.CRITICAL)
    now = datetime.now(IST)
    expected = expected_doctor_session(now)
    try:
        frames = bulk_history([PUBLIC_DEMO_SYMBOL], "1y", chunk_size=1,
                              retries=0, max_fallbacks=0, download_timeout=12)
        primary = frames.get(PUBLIC_DEMO_SYMBOL)
    except Exception as exc:
        print("MSK_DOCTOR_PUBLIC_EOD_SOURCE provider=primary exception_type={}".format(
            type(exc).__name__))
        primary = None
    primary_last = str(primary.index[-1].date()) if primary is not None and not primary.empty else "NONE"
    print("MSK_DOCTOR_PUBLIC_EOD_PRIMARY last={} rows={}".format(
        primary_last, len(primary) if primary is not None else 0))

    def alternate_fetch():
        from data_engine import _yahoo_v8_hist
        from market_data import to_yahoo
        frame = _yahoo_v8_hist(to_yahoo(PUBLIC_DEMO_SYMBOL), period="1y")
        last = str(frame.index[-1].date()) if frame is not None and not frame.empty else "NONE"
        print("MSK_DOCTOR_PUBLIC_EOD_SOURCE provider=Yahoo-v8 last={} rows={}".format(
            last, len(frame) if frame is not None else 0))
        return frame

    df = verify_final_archive(primary, now=now, expected=expected, alternate_fetch=alternate_fetch)
    got = df.index[-1].date()
    print("MSK_DOCTOR_PUBLIC_EOD_PROBE expected={} got={} path={} complete={}".format(
        expected, got, df.attrs["doctor_eod_path"], got == expected))
    if got != expected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
