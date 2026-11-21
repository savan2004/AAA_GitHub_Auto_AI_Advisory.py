"""Strict, PDF-only recovery for a lagging completed NSE daily candle.

Never combines candles from different price providers, fills missing dates,
or publishes yesterday's prices as the required EOD session.
"""
import logging
import math
import pandas as pd

LOG = logging.getLogger("msk_research_pdf_prices")
REQUIRED = ("Open", "High", "Low", "Close", "Volume")


def normalize_real_history(history, expected_session, minimum=200):
    if history is None or getattr(history, "empty", True):
        return None
    try:
        frame = history.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            # Only one symbol can be accepted. Reject ambiguous multi-symbol data.
            names = frame.columns.get_level_values(0)
            if set(REQUIRED).issubset(set(names)):
                if len(set(frame.columns.get_level_values(1))) != 1:
                    return None
                frame.columns = names
            else:
                return None
        if not set(REQUIRED).issubset(frame.columns):
            return None
        frame = frame.loc[:, REQUIRED].copy()
        dates = pd.DatetimeIndex(pd.to_datetime(frame.index))
        if dates.tz is not None:
            dates = dates.tz_convert("Asia/Kolkata").tz_localize(None)
        frame.index = dates.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        for name in REQUIRED:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
        frame = frame.replace([float("inf"), float("-inf")], float("nan")).dropna()
        frame = frame[frame.index <= pd.Timestamp(expected_session)]
        if (len(frame) < minimum
                or frame.index[-1].date().isoformat() != str(expected_session)):
            return None
        if not ((frame.loc[:, ["Open", "High", "Low", "Close"]] > 0).all().all()
                and (frame["Volume"] >= 0).all()
                and (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1)).all()
                and (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1)).all()):
            return None
        return frame
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None


def stitch_yahoo_windows(annual, recent, expected_session):
    """Complete annual Yahoo bars with only genuine same-provider missing days.

    Both windows must be unadjusted OHLCV from market_data._download_chunk.
    Require 10 fully matching overlapping candle closes and volumes; reject
    adjusted/raw mixes, altered bars, unverified gaps and unavailable data.
    """
    session = str(expected_session)
    fresh = normalize_real_history(recent, session, minimum=25)
    if fresh is None:
        return None
    full = normalize_real_history(annual, session, minimum=200)
    if full is not None:
        return full
    # Only the previous business day is allowed as annual's last session.
    previous = pd.bdate_range(end=pd.Timestamp(session), periods=2)[0]
    long = normalize_real_history(annual, str(previous.date()), minimum=200)
    if long is None:
        return None
    overlap = long.index.intersection(fresh.index)
    if len(overlap) < 10:
        return None
    # Compare unadjusted close + volume. A 0.5% mismatch implies different
    # adjustment basis or unreliable providers; never merge those series.
    recent_overlap = fresh.loc[overlap]
    long_overlap = long.loc[overlap]
    for key in ("Close", "Volume"):
        diff = (long_overlap[key] - recent_overlap[key]).abs()
        tolerance = (long_overlap[key].abs() * (0.005 if key == "Close" else 0.01))
        if (diff > tolerance).any():
            return None
    appended = fresh.loc[fresh.index > long.index[-1]]
    if len(appended) != 1 or appended.index[-1].date().isoformat() != session:
        return None
    merged = pd.concat([long, appended]).sort_index()
    return normalize_real_history(merged, session, minimum=200)


def _independent_yahoo_windows(symbol, expected_session):
    """Use the same real bulk Yahoo collector already used by index research."""
    from market_data import _download_chunk
    ticker = symbol + ".NS"
    annual = _download_chunk([ticker], "1y", retries=0, adjusted=False, timeout=12).get(ticker)
    recent = _download_chunk([ticker], "6mo", retries=0, adjusted=False, timeout=12).get(ticker)
    if annual is None or recent is None:
        return None
    return stitch_yahoo_windows(annual, recent, expected_session)


def verified_pdf_history(symbol, base_fetch, expected_session, alternate_fetch=None):
    """Latest confirmed full-year issuer OHLCV; recover only if base is stale."""
    if symbol not in ("SBIN", "DLF"):
        # This narrow repair serves independently audited PDF publishers only.
        return base_fetch(symbol, "1y")
    original = base_fetch(symbol, "1y")
    verified = normalize_real_history(original, expected_session)
    if verified is not None:
        return verified
    LOG.warning("MSK_PDF_STALE_PRIMARY symbol=%s required=%s", symbol, expected_session)

    if alternate_fetch is None:
        def alternate_fetch(ticker):
            return _independent_yahoo_windows(ticker, expected_session)

    try:
        alternate = alternate_fetch(symbol)
        verified = normalize_real_history(alternate, expected_session)
    except Exception as exc:
        LOG.warning("MSK_PDF_FRESH_ALT_FAILED symbol=%s type=%s",
                    symbol, type(exc).__name__)
        verified = None
    if verified is None:
        raise ValueError(
            "Research report withheld: historical price session stale or "
            "unverified; no current completed-session full-year NSE OHLCV "
            "from either real source"
        )
    LOG.info("MSK_PDF_FRESH_ALT_ACCEPTED symbol=%s session=%s rows=%d",
             symbol, expected_session, len(verified))
    return verified
