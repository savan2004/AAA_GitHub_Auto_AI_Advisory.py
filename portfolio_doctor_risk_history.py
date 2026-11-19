"""Historical risk of CURRENT holdings, adapted from the retired portfolio_report.py.

No saved portfolio, user identifiers, disk I/O, chart files or quote generation.
Uses independently verified completed daily histories for ALL submitted positions.
No forward/back fill, simulated trading, fabricated days or historical purchases.
"""
import math
from itertools import combinations

import pandas as pd

MIN_COMMON_DAYS = 65


def _unavailable(reason):
    return {"status": "UNAVAILABLE", "reason": reason,
            "coverage": 0, "benchmark_coverage": 0}


def _common_history(holdings, frames, *, now):
    from portfolio_doctor_live import verified_completed_daily_frame
    if not isinstance(frames, dict) or not 1 <= len(holdings) <= 5:
        return None
    dates = {str(row["date"]) for row in holdings}
    if len(dates) != 1:
        return None
    expected = next(iter(dates))
    columns = {}
    for row in holdings:
        symbol = row["symbol"]
        if symbol in columns or symbol not in frames:
            return None
        qty = float(row["qty"])
        if not math.isfinite(qty) or qty <= 0:
            return None
        try:
            checked = verified_completed_daily_frame(frames[symbol], now=now)
            if checked.index[-1].date().isoformat() != expected:
                return None
            close = pd.to_numeric(checked["Close"], errors="coerce")
            if len(close) < MIN_COMMON_DAYS or not close.notna().all():
                return None
            if not (close.gt(0) & close.apply(math.isfinite)).all():
                return None
            # Preserve exact observed session dates; do not fill gaps.
            columns[symbol] = close.astype(float)
        except Exception:
            return None
    try:
        aligned = pd.concat(columns, axis=1, join="inner").sort_index()
        if len(aligned) < MIN_COMMON_DAYS:
            return None
        if aligned.index[-1].date().isoformat() != expected:
            return None
        # Latest 252 common sessions: coverage disclosed; no 1y claim if shorter.
        return aligned.iloc[-252:].copy()
    except (ValueError, TypeError, KeyError):
        return None


def calculate_historical_risk(holdings, frames, benchmark_frame=None, *, now=None):
    """Return dated historical sensitivity; it is NOT customer realised P&L.

    A stock with missing/stale history makes the combined portfolio analysis
    unavailable rather than quietly excluding a large losing position.
    """
    from portfolio_doctor_live import IST, verified_completed_daily_frame
    from datetime import datetime
    now = now or datetime.now(IST)
    aligned = _common_history(holdings, frames, now=now)
    if aligned is None:
        return _unavailable("Incomplete or unverified common holdings history")
    try:
        quantities = pd.Series({
            row["symbol"]: float(row["qty"]) for row in holdings})
        total = aligned.mul(quantities, axis=1).sum(axis=1)
        if not total.gt(0).all() or not total.apply(math.isfinite).all():
            return _unavailable("Invalid historical portfolio valuations")
        port_returns = total.pct_change().dropna()
        if len(port_returns) < MIN_COMMON_DAYS - 1:
            return _unavailable("Insufficient daily-return observations")
        annual_vol = float(port_returns.std(ddof=1) * math.sqrt(252) * 100)
        drawdown = total.div(total.cummax()).sub(1.0) * 100
        max_dd = float(drawdown.min())
        current_dd = float(drawdown.iloc[-1])
        if not all(math.isfinite(v) for v in (annual_vol, max_dd, current_dd)):
            return _unavailable("Non-finite computed risk")
        raw_change = float(100 * (total.iloc[-1] / total.iloc[0] - 1))
        correlation_pairs = []
        if len(holdings) >= 2:
            changes = aligned.pct_change().dropna()
            for first, second in combinations(aligned.columns, 2):
                a, b = changes[first], changes[second]
                if len(a) < MIN_COMMON_DAYS - 1 or a.std() <= 0 or b.std() <= 0:
                    continue
                value = float(a.corr(b))
                if math.isfinite(value) and value >= 0.75:
                    correlation_pairs.append({
                        "a": first, "b": second, "correlation": round(value, 3)})
        correlation_pairs.sort(
            key=lambda row: (-row["correlation"], row["a"], row["b"]))
        result = {
            "status": "VERIFIED", "coverage": len(aligned),
            "included": len(holdings), "first_date": aligned.index[0].date().isoformat(),
            "last_date": aligned.index[-1].date().isoformat(),
            "annual_vol_pct": round(annual_vol, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "current_drawdown_pct": round(current_dd, 2),
            "static_change_pct": round(raw_change, 2),
            "pairs": correlation_pairs[:5],
            "beta": None, "beta_coverage": 0, "benchmark_coverage": 0,
            "chart_portfolio": [float(100 * value / total.iloc[0]) for value in total],
            "chart_nifty": None, "chart_dates": [
                item.date().isoformat() for item in total.index],
            "chart_coverage": len(total),
            "chart_first_date": total.index[0].date().isoformat(),
            "chart_last_date": total.index[-1].date().isoformat(),
            "chart_portfolio_change_pct": round(raw_change, 2),
            "chart_nifty_change_pct": None,
            "scope": "Historical value of today's share counts; not actual portfolio returns",
        }
        # Nifty 50 is an OPTIONAL comparator. Refuse if any session is stale,
        # insufficient or misaligned; never claim index-relative outperformance.
        if benchmark_frame is not None:
            try:
                checked = verified_completed_daily_frame(benchmark_frame, now=now)
                if checked.index[-1].date().isoformat() == result["last_date"]:
                    index_close = pd.to_numeric(
                        checked["Close"], errors="coerce").astype(float)
                    pair = pd.concat([total.rename("holdings"),
                                      index_close.rename("nifty")],
                                     axis=1, join="inner").dropna().sort_index()
                    if (len(pair) >= MIN_COMMON_DAYS and
                            pair.index[-1].date().isoformat() == result["last_date"] and
                            (pair > 0).all().all() and
                            pair.apply(lambda col: col.apply(math.isfinite).all()).all()):
                        returns = pair.pct_change().dropna()
                        variance = float(returns["nifty"].var(ddof=1))
                        if math.isfinite(variance) and variance > 1e-16:
                            beta = float(returns["holdings"].cov(returns["nifty"]) / variance)
                            if math.isfinite(beta) and abs(beta) <= 20:
                                result["beta"] = round(beta, 2)
                                result["beta_coverage"] = len(pair)
                        # Chart uses its own matched observed-date window.
                        # Missing benchmark dates must not hide a valid paired
                        # comparison or be forward/back filled. Full-history
                        # portfolio risk above remains unchanged and separately
                        # labelled; only these chart arrays use the pair.
                        result["benchmark_coverage"] = len(pair)
                        result.update({
                            "chart_portfolio": [
                                float(100 * value / pair["holdings"].iloc[0])
                                for value in pair["holdings"]],
                            "chart_nifty": [
                                float(100 * value / pair["nifty"].iloc[0])
                                for value in pair["nifty"]],
                            "chart_dates": [item.date().isoformat()
                                            for item in pair.index],
                            "chart_coverage": len(pair),
                            "chart_first_date": pair.index[0].date().isoformat(),
                            "chart_last_date": pair.index[-1].date().isoformat(),
                            "chart_portfolio_change_pct": round(float(100 * (
                                pair["holdings"].iloc[-1] /
                                pair["holdings"].iloc[0] - 1)), 2),
                            "chart_nifty_change_pct": round(float(100 * (
                                pair["nifty"].iloc[-1] /
                                pair["nifty"].iloc[0] - 1)), 2),
                        })
            except Exception:
                pass
        return result
    except (ValueError, TypeError, OverflowError, ArithmeticError):
        return _unavailable("Insufficient validated historical series")

