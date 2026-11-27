"""Bounded incremental OHLCV snapshots for the audited public PDF worker.

Only SBIN/DLF; no portfolio/customer symbols, DB or Telegram. The cache is an
optimization, never authority: require official same-session OHLCV, same raw
Yahoo download transport, exact overlap, previous close continuity and a
periodic full refresh. Historical provider corrections outside the overlap
remain possible until the next full refresh.
"""
import json
import os
import tempfile
from pathlib import Path
from datetime import date, datetime
import pandas as pd

from msk_chart_release import REQUIRED, validated_daily_history
from msk_history_incremental_pilot import merge_verified_history

PUBLIC_SYMBOLS = frozenset(("SBIN", "DLF"))
BASIS = "yahoo_download_daily_unadjusted_v1"
MAX_BYTES = 100_000
MAX_DELTA_REFRESHES = 3


def _read(path, symbol):
    try:
        if path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data["symbol"] != symbol or data["basis"] != BASIS
                or type(data["refreshes"]) is not int
                or not 0 <= data["refreshes"] < MAX_DELTA_REFRESHES
                or not 200 <= len(data["bars"]) <= 280):
            return None
        session = date.fromisoformat(data["session"])
        frame = pd.DataFrame(data["bars"], columns=["Date", *REQUIRED])
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date"), errors="raise"))
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            return None
        checked = validated_daily_history(frame, session.isoformat())
        if checked is None or len(checked) != len(frame):
            return None
        return session, checked, data["refreshes"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def _write(path, symbol, session, frame, refreshes):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            return
        data = {"symbol": symbol, "basis": BASIS, "session": session.isoformat(),
                "refreshes": refreshes, "bars": [
                    [stamp.date().isoformat(), *map(float, row)]
                    for stamp, row in zip(frame.index, frame.loc[:, list(REQUIRED)].to_numpy())]}
        payload = json.dumps(data, allow_nan=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_BYTES:
            return
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".public-history-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def refresh_public_history(symbol, expected_session, path, *, now=None,
                           fetch=None, reference=None):
    """Return a current verified raw series; never return the old cache alone."""
    if symbol not in PUBLIC_SYMBOLS or not path:
        return None
    from portfolio_doctor_live import IST, expected_doctor_session, previous_doctor_session
    from portfolio_doctor_bhavcopy import official_daily_bar_matches
    from research_pdf_price_guard import public_pdf_reference
    from market_data import _download_chunk
    try:
        expected = date.fromisoformat(str(expected_session))
        now = now or datetime.now(IST)
        if expected_doctor_session(now) != expected:
            return None
        previous = previous_doctor_session(expected)
        path = Path(path)
        if path.name != symbol + ".json":
            return None
        if reference is None:
            reference = public_pdf_reference
        if fetch is None:
            def fetch(period):
                ticker = symbol + ".NS"
                return _download_chunk([ticker], period, retries=0,
                                       adjusted=False, timeout=12).get(ticker)
        latest = reference(expected, [symbol]).get(symbol)
        if latest is None:
            return None
        cached = _read(path, symbol)
        result = None
        refreshes = 0
        mode = "full"
        if cached and cached[0] == previous:
            _, old, count = cached
            prior = reference(previous, [symbol]).get(symbol)
            if (prior is not None and official_daily_bar_matches(old.iloc[-1], prior)
                    and abs(float(latest["PreviousClose"]) - float(prior["Close"])) <= .05 + 1e-9
                    and abs(float(latest["Open"]) / float(latest["PreviousClose"]) - 1) <= .40):
                incoming = fetch("5d")
                if incoming is not None and not incoming.empty:
                    incoming = incoming.loc[incoming.index <= pd.Timestamp(expected)]
                    # Require multiple actual common observations, not just one.
                    if len(incoming.index.intersection(old.index)) >= 2:
                        candidate = merge_verified_history(old, incoming,
                            previous.isoformat(), expected.isoformat(), [expected.isoformat()])
                        if candidate is not None and official_daily_bar_matches(candidate.iloc[-1], latest):
                            result = candidate
                            refreshes = count + 1
                            mode = "incremental"
        if result is None:
            result = validated_daily_history(fetch("1y"), expected.isoformat())
            if result is None or not official_daily_bar_matches(result.iloc[-1], latest):
                return None
        # Preserve a bounded rolling annual window; no ever-growing history.
        cutoff = pd.Timestamp(expected) - pd.DateOffset(years=1)
        result = result.loc[result.index >= cutoff].copy()
        if not 200 <= len(result) <= 280:
            return None
        _write(path, symbol, expected, result, refreshes)
        print(f"PUBLIC_HISTORY_REFRESH symbol={symbol} session={expected} mode={mode} rows={len(result)}", flush=True)
        return result
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError,
            OverflowError, ZeroDivisionError):
        return None
