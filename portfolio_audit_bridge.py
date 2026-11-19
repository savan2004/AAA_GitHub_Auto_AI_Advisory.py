"""Adapter for private, one-time Portfolio Audit (no customer storage or logs).

The existing Doctor verifies every NSE close before this adapter is reached.
Only the completed-session aligned data is allowed into the audit PDF.
Market/financial lookup failures are represented by MISSING evidence, never
synthetic fixtures or invented exchange/issuer verification.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from portfolio_doctor_live import (
    IST, PortfolioDoctorError, doctor_trading_session,
    expected_doctor_session, optional_review_deadline,
    verified_completed_daily_frame,
)


def audited_data(holdings: list[dict], recs: list[dict], frames: dict,
                 *, now: datetime | None = None,
                 extras: Any = None, budget_seconds: float = 58):
    """Return fully checked public Dataset for an isolated private audit."""
    from portfolio_audit_report import Dataset, MSKRepoProvider, PriceSeries
    now = now or datetime.now(IST)
    start = monotonic()
    provider = extras or MSKRepoProvider()
    data = Dataset(synthetic=False)
    records = {str(r["symbol"]): r for r in recs}
    if not 1 <= len(holdings) <= 5 or len(records) != len(holdings):
        raise PortfolioDoctorError("BAD_INPUT")
    if any(h.get("exchange") == "BSE" or
           str(records.get(h["symbol"], {}).get("price_source", "")).startswith("BSE:")
           for h in holdings):
        # The standalone audit's symbol identity is NSE-only.
        raise PortfolioDoctorError("BAD_INPUT")

    def lookup(seconds, fn, fallback=None, *, label=None):
        remaining = budget_seconds - (monotonic() - start)
        if remaining < 1:
            if label: data.errors[label] = "collection budget exhausted"
            return fallback
        try:
            with optional_review_deadline(min(seconds, remaining)):
                return fn()
        except BaseException as exc:
            if label:
                data.errors[label] = ("source timeout" if type(exc).__name__ == "OptionalReviewTimeout"
                                      else "source lookup failed")
            # Never print a client's symbol, cost or provider exception text.
            return fallback

    # Strict historical preflight: latest close and date must reconcile with
    # the already exchange-verified private Doctor result.
    verified = {}
    for row in holdings:
        sym = row["symbol"]
        rec = records.get(sym)
        if not rec or not isinstance(frames, dict) or sym not in frames:
            raise PortfolioDoctorError("NO_PRICES")
        daily = verified_completed_daily_frame(frames[sym], now=now)
        if (daily.index[-1].date().isoformat() != str(rec["date"]) or
                abs(float(daily["Close"].iloc[-1]) - float(rec["close"])) > 0.015):
            raise PortfolioDoctorError("STALE_PRICES")
        verified[sym] = daily

    # Financial evidence gets a fair bounded slot BEFORE optional history,
    # dividends and events can consume the whole enrichment deadline.
    symbols = list(verified)
    for i, sym in enumerate(symbols):
        remaining = max(0, budget_seconds - (monotonic() - start))
        allowance = min(9, remaining / (len(symbols) - i + 1))
        raw = lookup(allowance, lambda s=sym: provider.fundamentals(s), {},
                     label="fundamentals:"+sym) or {}
        data.fundamentals[sym] = raw if isinstance(raw, dict) else {}
        if not data.fundamentals[sym]:
            data.errors.setdefault("fundamentals:"+sym, "source returned no usable financial evidence")

    # Upgrade up to 2Y in one bounded batch, never at the cost of a validated
    # one-year fallback. Official EOD is still checked by the original worker.
    from market_data import bulk_history, index_history
    symbols = list(verified)
    extended = lookup(16, lambda: bulk_history(
        symbols, "2y", chunk_size=5, retries=0, max_fallbacks=0,
        download_timeout=12), {}) or {}
    for sym in symbols:
        frame = verified[sym]
        candidate = extended.get(sym) if isinstance(extended, dict) else None
        if candidate is not None:
            try:
                longer = verified_completed_daily_frame(candidate, now=now)
                same_day = (longer.index[-1].date() == frame.index[-1].date())
                overlap = frame.index.intersection(longer.index)
                comparable = len(overlap) >= 40
                if comparable:
                    delta = (longer.loc[overlap, "Close"].astype(float) /
                             frame.loc[overlap, "Close"].astype(float) - 1).abs()
                    comparable = bool((delta <= 0.0075).all())
                if same_day and comparable and len(longer) >= len(frame):
                    frame = longer
            except Exception:
                pass
        data.stocks[sym] = PriceSeries(
            frame=frame, source="MSK completed-session OHLCV (provider history; secondary)",
            retrieved_utc=datetime.now(IST).isoformat(),
            verified_exchange=False, adjusted_for_actions=False,
            adjustment_reconciled=False)

    for sym, attr in (("^NSEI", "nifty50"), ("^CRSLDX", "nifty500")):
        candidate = lookup(10, lambda s=sym: index_history(
            s, "2y", retries=0, timeout=9))
        if candidate is None:
            data.errors["index:"+sym] = "index history unavailable"
            continue
        try:
            completed = verified_completed_daily_frame(candidate, now=now)
            setattr(data, attr, PriceSeries(
                frame=completed, source="MSK index provider (secondary)",
                retrieved_utc=datetime.now(IST).isoformat(),
                verified_exchange=False))
        except Exception:
            data.errors["index:"+sym] = "completed index session not verified"

    from portfolio_doctor_peers import official_nifty500_industries
    industries = lookup(7, official_nifty500_industries, {}) or {}
    if isinstance(industries, dict) and 450 <= len(industries) <= 550:
        data.constituents = {sym: {"industry": label, "market_cap_bucket": ""}
                             for sym, label in industries.items()}
        data.constituents_source = "Nifty Indices constituent roster"
    else:
        data.errors["nifty500:constituents"] = "official index roster unavailable"

    for sym in symbols:
        raw_div = lookup(3, lambda s=sym: provider.dividends(s), []) or []
        data.dividends[sym] = list(raw_div) if isinstance(raw_div, (list, tuple)) else []
        raw_act = lookup(3, lambda s=sym: provider.actions(s), []) or []
        data.actions[sym] = list(raw_act) if isinstance(raw_act, (list, tuple)) else []
        data.events[sym] = {}
    return data


def build_verified_audit(holdings: list[dict], recs: list[dict],
                         frames: dict, *, now: datetime | None = None,
                         extras: Any = None, budget_seconds: float = 58) -> bytes:
    """Render new PDF strictly after the original Doctor's verified prices."""
    from portfolio_audit_report import create_report
    now = now or datetime.now(IST)
    data = audited_data(holdings, recs, frames, now=now,
                        extras=extras, budget_seconds=budget_seconds)
    holidays = tuple((now.date() - timedelta(days=i)) for i in range(30)
                     if (now.date() - timedelta(days=i)).weekday() < 5 and
                     not doctor_trading_session(now.date() - timedelta(days=i)))
    result = create_report(holdings, dataset=data, now=now, holidays=holidays)
    if data.synthetic or b"SYNTHETIC DEMO" in result.pdf:
        raise PortfolioDoctorError("PDF_FAILED")
    return result.pdf

