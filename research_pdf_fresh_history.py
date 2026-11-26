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
        if frame.index.hasnans or frame.index.has_duplicates or not frame.columns.is_unique:
            return None
        frame = frame.sort_index()
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


def _recover_official_day(symbol, annual, expected_session, minimum, *, now=None):
    """Reuse the public Doctor archive parser; no synthetic or adjusted bars."""
    from datetime import date, datetime
    from portfolio_doctor_live import IST, expected_doctor_session, previous_doctor_session
    from portfolio_doctor_bhavcopy import fetch_nse_bhavcopy_rows, stitch_one_verified_day
    try:
        now = now or datetime.now(IST)
        expected = date.fromisoformat(str(expected_session))
        if expected_doctor_session(now) != expected:
            return None
        previous = previous_doctor_session(expected)
        # Refuse to overwrite any existing target-session observation.
        if annual is None or any(d == expected for d in pd.DatetimeIndex(annual.index).date):
            return None
        prior_frame = normalize_real_history(annual, str(previous), minimum=minimum-1)
        if prior_frame is None:
            return None
        latest = fetch_nse_bhavcopy_rows(expected, [symbol]).get(symbol)
        prior = fetch_nse_bhavcopy_rows(previous, [symbol]).get(symbol)
        if not latest or not prior:
            return None
        observed = prior_frame.iloc[-1]
        if any(abs(float(observed[k])-float(prior[k])) > .05
               for k in ("Open", "High", "Low", "Close")):
            return None
        if float(observed["Volume"]) != float(prior["Volume"]):
            return None
        merged = stitch_one_verified_day(prior_frame, prior=prior, latest=latest,
            previous_day=previous, expected_day=expected, now=now)
        return normalize_real_history(merged, expected_session, minimum=minimum)
    except Exception as exc:
        LOG.warning("MSK_PDF_OFFICIAL_RECOVERY_FAILED symbol=%s type=%s",
                    symbol, type(exc).__name__)
        return None


def _alternate_history(symbol, expected_session, minimum):
    """Bounded unadjusted Yahoo downloads, then official one-day recovery."""
    from market_data import _download_chunk
    ticker = symbol + ".NS"
    annual = _download_chunk([ticker], "1y", retries=0, adjusted=False,
                             timeout=12).get(ticker)
    fresh = normalize_real_history(annual, expected_session, minimum=minimum)
    if fresh is not None:
        return fresh
    # A genuine recent window may cover a non-publisher issuer's 60-bar gate.
    if minimum <= 60:
        recent = _download_chunk([ticker], "6mo", retries=0, adjusted=False,
                                 timeout=12).get(ticker)
        fresh = normalize_real_history(recent, expected_session, minimum=minimum)
        if fresh is not None:
            return fresh
    return _recover_official_day(symbol, annual, expected_session, minimum)


def verified_pdf_history(symbol, base_fetch, expected_session, alternate_fetch=None):
    """Latest confirmed full-year issuer OHLCV; recover only if base is stale."""
    # Keep the existing publisher's full-year minimum. Other research reports
    # previously accepted 60 sessions; do not silently increase their minimum.
    minimum = 200 if symbol in ("SBIN", "DLF") else 60
    try:
        original = base_fetch(symbol, "1y")
        verified = normalize_real_history(original, expected_session, minimum=minimum)
    except Exception as exc:
        LOG.warning("MSK_PDF_PRIMARY_FAILED symbol=%s type=%s",
                    symbol, type(exc).__name__)
        verified = None
    if verified is not None:
        return verified
    LOG.warning("MSK_PDF_STALE_PRIMARY symbol=%s required=%s", symbol, expected_session)

    if alternate_fetch is None:
        def alternate_fetch(ticker):
            return _alternate_history(ticker, expected_session, minimum)

    try:
        alternate = alternate_fetch(symbol)
        verified = normalize_real_history(alternate, expected_session, minimum=minimum)
    except Exception as exc:
        LOG.warning("MSK_PDF_FRESH_ALT_FAILED symbol=%s type=%s",
                    symbol, type(exc).__name__)
        verified = None
    if verified is None:
        raise ValueError(
            "Research report withheld: historical price session stale or "
            "unverified; no sufficient completed-session NSE OHLCV "
            "from either real source"
        )
    LOG.info("MSK_PDF_FRESH_ALT_ACCEPTED symbol=%s session=%s rows=%d",
             symbol, expected_session, len(verified))
    return verified
