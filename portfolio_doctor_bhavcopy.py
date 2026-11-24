"""NSE public bhavcopy repair for a one-day lag in private Doctor OHLCV.

Both prior and latest *official* daily files must be available. The historical
series must end on the prior session and match its official OHLC prices within
0.75%. The latest official file must prove previous close continuity. Never
guess a candle, merge unverified histories or write client holdings to disk.
All data is request-local memory and this file never creates logs.
"""
import csv
import io
import math
from datetime import datetime, date
from typing import Mapping

import pandas as pd

MAX_CSV_BYTES = 20 * 1024 * 1024
MIN_EQ_ROWS = 400


def _number(value):
    try:
        x = float(str(value or "").replace(",", "").strip())
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _csv_day(raw):
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except (TypeError, ValueError):
            pass
    return None


def fetch_nse_bhavcopy_rows(day, wanted, *, get=None):
    """Read an official public NSE equity CSV, retaining only requested symbols.

    The remote URL and request headers are reused from the already working
    universe.py bhavcopy discovery. This never uses the shared universe cache.
    """
    from universe import _BHAV_URL, _UA
    import requests
    from portfolio_doctor_live import doctor_trading_session
    if not isinstance(day, date):
        return {}
    try:
        if not doctor_trading_session(day):
            return {}
    except Exception:
        return {}
    symbols = {str(sym).strip().upper() for sym in (wanted or [])}
    if not symbols or len(symbols) > 5:
        return {}
    url = _BHAV_URL.format(d=day.strftime("%d%m%Y"))
    get = get or requests.get
    try:
        resp = get(url, headers=_UA, timeout=12)
        if not getattr(resp, "ok", False):
            return {}
        body = resp.content
        if not body or not (50_000 <= len(body) <= MAX_CSV_BYTES):
            return {}
        source = io.StringIO(body.decode("utf-8-sig"))
        reader = csv.DictReader(source)
        headers = [str(h or "").strip().upper() for h in (reader.fieldnames or [])]
        required = {"SYMBOL", "SERIES", "DATE1", "OPEN_PRICE", "HIGH_PRICE",
                    "LOW_PRICE", "CLOSE_PRICE", "PREV_CLOSE", "TTL_TRD_QNTY"}
        if not required.issubset(headers):
            return {}
        seen = set()
        rows = {}
        eq_total = 0
        for raw in reader:
            row = {str(k or "").strip().upper(): str(v or "").strip()
                   for k, v in raw.items()}
            if row.get("SERIES") != "EQ":
                continue
            eq_total += 1
            symbol = row.get("SYMBOL", "").upper()
            if symbol not in symbols:
                continue
            if symbol in seen or _csv_day(row.get("DATE1")) != day:
                return {}
            seen.add(symbol)
            fields = {
                "Open": _number(row.get("OPEN_PRICE")),
                "High": _number(row.get("HIGH_PRICE")),
                "Low": _number(row.get("LOW_PRICE")),
                "Close": _number(row.get("CLOSE_PRICE")),
                "Volume": _number(row.get("TTL_TRD_QNTY")),
                "PreviousClose": _number(row.get("PREV_CLOSE")),
            }
            if (any(fields[k] is None or fields[k] <= 0 for k in
                    ("Open", "High", "Low", "Close", "PreviousClose")) or
                    fields["Volume"] is None or fields["Volume"] < 0 or
                    fields["High"] < max(fields["Open"], fields["Low"], fields["Close"]) or
                    fields["Low"] > min(fields["Open"], fields["High"], fields["Close"])):
                return {}
            rows[symbol] = fields
        return rows if eq_total >= MIN_EQ_ROWS else {}
    except (requests.RequestException, UnicodeError, ValueError, KeyError,
            TypeError, OverflowError, OSError, csv.Error):
        return {}


def stitch_one_verified_day(primary, *, prior, latest, previous_day, expected_day, now):
    """Append only if prior-session NSE OHLC agrees with existing series.

    PrevClose continuity prevents a hidden split/bonus restatement on the new
    session. A >40% overnight gap also blocks an unchecked corporate action.
    """
    from market_data import _clean_frame
    from portfolio_doctor_live import verified_completed_daily_frame
    if not isinstance(prior, Mapping) or not isinstance(latest, Mapping):
        return None
    try:
        # Inspect the raw sequence before a shared normalizer can sort or
        # collapse exact duplicates. Official one-day recovery must be strict.
        if (not isinstance(primary, pd.DataFrame) or
                not isinstance(primary.index, pd.DatetimeIndex) or
                primary.index.has_duplicates or primary.index.hasnans or
                not primary.index.is_monotonic_increasing or
                len(set(primary.index.date)) != len(primary)):
            return None
        original = _clean_frame(primary)
        if original is None:
            return None
        # Before the exchange close, Yahoo can already include an unfinished
        # current-day candle. That later candle must not prevent checking the
        # previous COMPLETED session against the official NSE file. Keep the
        # raw sequence strict: reject duplicate/out-of-order dates and never
        # overwrite an expected-session candle already present in the feed.
        if (not original.index.is_monotonic_increasing or
                original.index.has_duplicates or
                len(set(original.index.date)) != len(original)):
            return None
        if any(day == expected_day for day in original.index.date):
            return None
        original = original.loc[original.index.date <= previous_day]
        # Only a genuine preceding-session OHLCV history may be extended by
        # one official exchange candle. Never fill more than one missing day.
        if original.empty or original.index[-1].date() != previous_day or len(original) < 20:
            return None
        hist = original.iloc[-1]
        for field in ("Open", "High", "Low", "Close"):
            ref, observed = _number(prior.get(field)), _number(hist[field])
            if ref is None or observed is None or ref <= 0:
                return None
            if abs(observed / ref - 1) > 0.0075:
                return None
        prior_close = _number(prior.get("Close"))
        last_prev = _number(latest.get("PreviousClose"))
        latest_open = _number(latest.get("Open"))
        if (not prior_close or not last_prev or not latest_open or
                abs(last_prev / prior_close - 1) > 0.005 or
                abs(latest_open / last_prev - 1) > 0.40):
            return None
        target_fields = ("Open", "High", "Low", "Close", "Volume")
        row = pd.DataFrame(
            [{field: latest[field] for field in target_fields}],
            index=pd.DatetimeIndex([pd.Timestamp(expected_day)]))
        merged = _clean_frame(pd.concat([original, row]))
        if merged is None or merged.index.has_duplicates:
            return None
        complete = verified_completed_daily_frame(merged, now=now)
        return complete if len(complete) >= 20 else None
    except (IndexError, TypeError, KeyError, ValueError, AttributeError,
            OverflowError):
        return None


def recover_from_public_bhavcopy(primary_frames, symbols, *, now):
    """Fetch 2 exchange files ONCE for 1–5 symbols; do not cache or persist."""
    from portfolio_doctor_live import expected_doctor_session, previous_doctor_session
    expected = expected_doctor_session(now)
    previous = previous_doctor_session(expected)
    # Expected/prior dates skip reviewed exchange holidays, include special sessions.
    current_rows = fetch_nse_bhavcopy_rows(expected, symbols)
    if not current_rows:
        return {}
    prior_rows = fetch_nse_bhavcopy_rows(previous, symbols)
    if not prior_rows:
        return {}
    found = {}
    for sym in symbols:
        result = stitch_one_verified_day(
            primary_frames.get(sym), prior=prior_rows.get(sym),
            latest=current_rows.get(sym), previous_day=previous,
            expected_day=expected, now=now)
        if result is not None:
            found[sym] = result
    return found
