"""Bulk data gateway for MSK Moderate/Aggressive scan only.

One shared batch avoids 20 throttled get_hist() calls. A deadline stops waiting
but never reports zero bullish stocks merely because a download is still active.
Market data failures are not converted into fabricated candles or trade ideas.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

from investment_screener import IST, evaluate_candidate, format_shortlist

LOG = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="msk-investment-bulk")
_LOCK = threading.Lock()
_ACTIVE = None
_ACTIVE_PROFILE = None


def _bulk_evaluate(profile, symbols):
    from market_data import bulk_history

    start = datetime.now(IST)
    histories = bulk_history(symbols, "6mo", chunk_size=20,
                             cache_ttl=15 * 60, adjusted=False, retries=1)
    if not isinstance(histories, dict):
        histories = {}
    candidates = []
    received = 0
    rejected = 0
    for symbol in symbols:
        frame = histories.get(symbol)
        if frame is None or frame.empty:
            continue
        received += 1
        row = evaluate_candidate(symbol, frame, profile, start)
        if row is not None:
            candidates.append(row)
        else:
            rejected += 1
    missing = len(symbols) - received
    LOG.info("Investment bulk %s: received=%d/%d bullish=%d rejected=%d missing=%d",
             profile, received, len(symbols), len(candidates), rejected, missing)
    return format_shortlist(profile, candidates, received, len(symbols),
                            unfinished=0, missing=missing)


def run_investment_scan(profile, symbols, deadline=55):
    """Run one shared batch and wait a bounded time for Telegram response.

    If the provider is still running, return an honest pending status;
    its one background worker continues without creating an unbounded queue.
    """
    global _ACTIVE, _ACTIVE_PROFILE
    if profile not in ("moderate", "aggressive"):
        return "❌ Unsupported investment profile."
    symbols = tuple(dict.fromkeys(symbols))
    if not symbols:
        return "⚠️ No approved symbol universe configured."

    with _LOCK:
        if _ACTIVE is not None and not _ACTIVE.done():
            if _ACTIVE_PROFILE != profile:
                return "⏳ Another investment scan is running. Retry once it finishes."
            future = _ACTIVE
        else:
            future = _POOL.submit(_bulk_evaluate, profile, symbols)
            _ACTIVE = future
            _ACTIVE_PROFILE = profile

    done, _ = wait((future,), timeout=deadline)
    if not done:
        LOG.warning("Investment bulk %s: data download still active at %ds deadline", profile, deadline)
        return ("⏳ NSE market-data batch is still downloading. "
                "No investment recommendation issued from an incomplete scan. "
                "Retry the same button shortly; the shared download remains in progress.")
    try:
        return future.result()
    except Exception as exc:
        LOG.warning("Investment bulk %s failed (%s)", profile, type(exc).__name__)
        return ("⚠️ Market-data batch unavailable. No stocks were assessed or recommended. "
                "Please try again later; do not treat this as zero bullish stocks.")
