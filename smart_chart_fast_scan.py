"""Bounded real-price scan for the MSK automatic Smart Chart.

A provider failure must not become a fabricated ticker, quote, or trading signal.
The retry path only inspects the original deterministic sample; it never claims
to rank unscanned Nifty constituents.
"""
import math
import random
import re
import time
from datetime import date, timedelta

import pandas as pd


class ChartScanUnavailable(RuntimeError):
    pass


_REQUIRED = ("Open", "High", "Low", "Close", "Volume")


def _ticker_frame(prices, symbol, ticker_count):
    """Handle both yfinance MultiIndex orientations and single-ticker frames."""
    if prices is None or getattr(prices, "empty", True):
        return None
    columns = prices.columns
    if getattr(columns, "nlevels", 1) >= 2:
        for level in (0, 1):
            if symbol in columns.get_level_values(level):
                return prices.xs(symbol, axis=1, level=level)
        return None
    return prices if ticker_count == 1 else None


def _completed_frame(frame, session_key, *, diagnostics=None):
    """Same strict completed-bar contract for scanner and renderer."""
    from msk_chart_release import validated_daily_history
    return validated_daily_history(frame, session_key, diagnostics=diagnostics)


def choose_auto_chart(universe, *, download, scorer, session_key,
                      sample_limit=60, fallback_history=None, min_score=None,
                      require_confirmed_setup=False, scan_all=False,
                      recover_history=None, alternate_history=None,
                      individual_history=None, shortlist_rank=None,
                      shortlist_recovery=None, recover_partial=False):
    """Return (winning real candidate, sampled, verified, total universe).

    The batch is attempted once per bounded group. When no candidate exists,
    bounded individual or serial retries are allowed. With recover_partial,
    the existing shortlist may also recover stale histories when fresh
    candidates exist, using the same shared archive and no extra individual calls.

    There is no stale EOD fallback, no cross-symbol substitutions, and no
    full-universe claim. All paths use the same timestamp/price/score gates.
    """
    distinct = {}
    for symbol, name in universe:
        if (isinstance(symbol, str) and re.fullmatch(r"[A-Z0-9&._-]{1,25}\.NS", symbol)
                and symbol not in distinct):
            distinct[symbol] = name
    whole = len(distinct)
    if whole == 0:
        raise ChartScanUnavailable("No current NSE chart universe")
    if not isinstance(sample_limit, int) or not 1 <= sample_limit <= 120:
        raise ValueError("Sample size must be between 1 and 120")

    selected = (list(distinct.items()) if scan_all else
                random.Random(str(session_key)).sample(
                    list(distinct.items()), min(whole, sample_limit)))
    names = dict(selected)
    symbols = list(names)
    candidates = {}
    diagnostics = {"rejected": 0, "scorer": 0, "download_failures": 0}
    rejection_reasons = {}
    verified_symbols = set()
    preferred = ("RELIANCE.NS", "SBIN.NS", "TCS.NS", "HDFCBANK.NS",
                 "ICICIBANK.NS", "INFY.NS", "ITC.NS", "LT.NS")
    recovery_symbols = sorted(symbols, key=lambda sym: (sym not in preferred, symbols.index(sym)))
    preliminary = {}
    shortlist_frames = {}
    individual_attempted = set()
    # At most 24 histories, not all 501; only two shared exchange archives.
    shortlist_limit = 24

    recovered_symbols = set()

    def consider(symbol, frame):
        if symbol in candidates:
            return
        source = getattr(frame, "attrs", {}).get("history_source", "Yahoo")
        complete = _completed_frame(frame, session_key, diagnostics=rejection_reasons)
        if complete is None and individual_history is not None and shortlist_rank is not None:
            try:
                rank = shortlist_rank(frame, session_key)
                if rank is not None and math.isfinite(float(rank)):
                    preliminary[symbol] = float(rank)
                    if shortlist_recovery is not None and frame is not None:
                        shortlist_frames[symbol] = frame.copy()
                        if len(shortlist_frames) > shortlist_limit:
                            weakest = min(shortlist_frames, key=lambda sym: (preliminary[sym], sym))
                            del shortlist_frames[weakest]
            except (ValueError, TypeError, KeyError, RuntimeError):
                pass
        if complete is None and recover_history is not None and individual_history is None:
            try:
                repaired = recover_history(symbol, frame)
                complete = _completed_frame(repaired, session_key) if repaired is not None else None
                if complete is not None:
                    recovered_symbols.add(symbol)
                    source = "Yahoo history + NSE final EOD"
            except Exception:
                diagnostics["download_failures"] += 1
        if complete is None:
            diagnostics["rejected"] += 1
            return
        verified_symbols.add(symbol)
        try:
            candidate = scorer(symbol, names[symbol], df=complete.copy())
            if not isinstance(candidate, dict) or candidate.get("sym") != symbol:
                diagnostics["scorer"] += 1
                return
            close = float(candidate.get("close", math.nan))
            score = float(candidate.get("score", math.nan))
            if not (math.isfinite(close) and close > 0 and
                    math.isfinite(score) and
                    math.isclose(close, float(complete["Close"].iloc[-1]),
                                 rel_tol=0.00001)):
                diagnostics["scorer"] += 1
                return
            if min_score is not None and score < min_score:
                diagnostics["scorer"] += 1
                return
            if require_confirmed_setup and not candidate.get("setup_confirmed"):
                diagnostics["scorer"] += 1
                return
            candidate["_daily_history"] = complete
            candidate["_history_source"] = source
            candidates[symbol] = candidate
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            diagnostics["scorer"] += 1

    def bulk(tickers, *, threads, timeout, explicit_dates=False):
        try:
            window = {"period": "1y"}
            if explicit_dates:
                day = date.fromisoformat(str(session_key))
                window = {"start": (day - timedelta(days=370)).isoformat(),
                          "end": (day + timedelta(days=1)).isoformat()}
            prices = download(
                tickers=tickers, **window, interval="1d", progress=False,
                # Raw close and OHLC, as reported by the exchange on the
                # latest day. Never splice dividend-adjusted OHLC into raw NSE.
                auto_adjust=False, group_by="ticker", threads=threads,
                timeout=timeout,
            )
        except Exception:
            diagnostics["download_failures"] += 1
            return
        for sym in tickers:
            try:
                consider(sym, _ticker_frame(prices, sym, len(tickers)))
            except (KeyError, TypeError, ValueError, AttributeError):
                diagnostics["rejected"] += 1

    # Bounded full-universe scan. Small batches avoid 500-ticker Yahoo timeouts
    # and keep failures isolated. Stop starting bulk batches after 120s.
    attempted = 0
    started = time.monotonic()
    if scan_all:
        for start in range(0, len(symbols), 40):
            if time.monotonic() - started > 120:
                break
            batch = symbols[start:start + 40]
            attempted += len(batch)
            bulk(batch, threads=True, timeout=9)
    else:
        attempted = len(symbols)
        bulk(symbols, threads=True, timeout=12)
    if (not candidates or recover_partial) and shortlist_recovery is not None and shortlist_frames:
        # The two dated NSE files are shared by the bounded shortlist. No
        # extra Yahoo calls are needed for histories already in memory.
        ranked = sorted(shortlist_frames, key=lambda sym: (-preliminary[sym], sym))
        # Do not refetch an already validated history or extend the sample.
        ranked = [sym for sym in ranked if sym not in verified_symbols]
        for sym in ranked:
            if time.monotonic() - started >= 125:
                break
            try:
                repaired = shortlist_recovery(sym, shortlist_frames[sym])
                if repaired is not None:
                    repaired.attrs["history_source"] = "Yahoo raw OHLC + NSE final EOD"
                    consider(sym, repaired)
                    if sym in verified_symbols:
                        recovered_symbols.add(sym)
            except Exception:
                diagnostics["download_failures"] += 1
        shortlist_frames.clear()
    if not candidates and individual_history is not None:
        # Rank only the already-attempted, missing-history symbols. The
        # prior-session rank is not eligible for publication, ever.
        attempted_set = set(symbols[:attempted])
        ranked = sorted(preliminary, key=lambda sym: (-preliminary[sym], sym))
        order = list(dict.fromkeys(ranked + recovery_symbols))
        for sym in (s for s in order if s in attempted_set and s not in verified_symbols):
            if len(individual_attempted) >= 3 or time.monotonic() - started >= 135:
                break
            individual_attempted.add(sym)
            print(f"CHART_INDIVIDUAL_ATTEMPT symbol={sym}", flush=True)
            try:
                consider(sym, individual_history(sym))
            except Exception:
                diagnostics["download_failures"] += 1
            if sym in candidates:
                break
    elif not candidates and (not scan_all or attempted < 10):
        # Yahoo can rate-limit a 60-name request while serving a 10-name
        # serial batch. Retry once, without concurrent ticker workers.
        bulk(symbols[:min(10, len(symbols))], threads=False, timeout=9)
    elif scan_all and not verified_symbols and time.monotonic() - started < 120:
        # A full batch may return an empty expected-session row for every
        # ticker. Recover at most three already-attempted liquid symbols
        # using a dated, serial Yahoo request, with unchanged candle gates.
        attempted_symbols = set(symbols[:attempted])
        retry = [sym for sym in recovery_symbols if sym in attempted_symbols][:3]
        if retry:
            bulk(retry, threads=False, timeout=6, explicit_dates=True)

    if individual_history is None and not candidates and not verified_symbols and alternate_history is not None:
        for sym in recovery_symbols[:2]:
            if time.monotonic() - started >= 135:
                break
            try:
                consider(sym, alternate_history(sym))
            except Exception:
                diagnostics["download_failures"] += 1

    if individual_history is None and not candidates and fallback_history is not None:
        # A narrowly bounded official NSE historical source is independent
        # of Yahoo's batch endpoint. No more than two original sample calls.
        for sym in recovery_symbols[:2]:
            try:
                frame = fallback_history(sym)
                if frame is not None:
                    frame.attrs["history_source"] = "NSE historical API"
                consider(sym, frame)
            except Exception:
                diagnostics["download_failures"] += 1

    print(f"CHART_HISTORY_COVERAGE verified={len(verified_symbols)} recovered={len(recovered_symbols)} "
          f"attempted={attempted} eligible={len(candidates)}", flush=True)
    if not candidates:
        raise ChartScanUnavailable(
            ("No verified current-session histories " if not verified_symbols else
             "No verified 12+/20 breakout or trend-confirmation setup " if min_score is not None
             else "No valid real price bars in bounded chart sample ") +
            f"(session={session_key}; sampled={len(selected)}; "
            f"attempted={attempted}/{whole}; rejected={diagnostics['rejected']}; "
            f"verified_histories={len(verified_symbols)}; scorer_rejected={diagnostics['scorer']}; "
            f"provider_errors={diagnostics['download_failures']}; "
            f"history_reasons={dict(sorted(rejection_reasons.items()))})."
        )

    # Prefer a structure-backed plan over observation-only candidates, then
    # rank by FINAL score. Never fabricate targets if no plan qualifies.
    winner = max(candidates.values(), key=lambda c: (
        c.get("verified_trade_plan") is True, float(c["score"])))
    winner["_verified_count"] = len(verified_symbols)
    return winner, attempted, len(candidates), whole
