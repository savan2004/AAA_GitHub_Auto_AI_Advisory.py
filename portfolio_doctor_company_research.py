"""Optional per-company Doctor research facts from VERIFIED private-worker candles.

This is a short evidence report, not the separate full 8+ page equity report.
No disk, caching, external requests, holders' identity, speculative prices,
personalised investment opinions, or log records.
"""
import math
from datetime import datetime
from portfolio_doctor_calculations import _ema, _rsi
from portfolio_doctor_live import IST, verified_completed_daily_frame

MAX_COMPANIES = 5
MAX_CHART_DAYS = 160


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ema_series(closes, period):
    factor = 2 / (period + 1)
    series, value = [], closes[0]
    for close in closes:
        value += factor * (close - value)
        series.append(value)
    return series


def _macd_hist(closes):
    if len(closes) < 35:
        return None
    fast = slow = closes[0]
    signal = 0.0
    for index, price in enumerate(closes):
        fast += (2 / 13) * (price - fast)
        slow += (2 / 27) * (price - slow)
        macd = fast - slow
        if index == 0:
            signal = macd
        else:
            signal += (2 / 10) * (macd - signal)
    return round(macd - signal, 3)


def _atr_pct(frame, closes):
    if len(frame) < 16 or not all(k in frame for k in ("High", "Low")):
        return None
    high = [_safe_float(v) for v in frame["High"].iloc[-14:].tolist()]
    low = [_safe_float(v) for v in frame["Low"].iloc[-14:].tolist()]
    previous = closes[-15:-1]
    if (len(high) != 14 or len(low) != 14 or len(previous) != 14 or
            any(h is None or l is None or h < l or l <= 0
                for h, l in zip(high, low))):
        return None
    ranges = [max(h - l, abs(h - p), abs(l - p))
              for h, l, p in zip(high, low, previous)]
    return round(100 * sum(ranges) / 14 / closes[-1], 2)


def _financial_evidence(fund):
    """Only externally labelled metrics; zero never means a missing value."""
    if (not isinstance(fund, dict) or fund.get("status") != "PARTIAL"
            or fund.get("source") != "Screener.in (secondary)"
            or fund.get("scope") not in ("consolidated", "standalone")
            or not fund.get("retrieved_utc")):
        return {"status": "UNAVAILABLE", "metrics": []}
    values = []
    for label, key, unit in (
        ("Revenue", "revenue", "Rs Cr"),
        ("Net profit", "net_profit", "Rs Cr"),
        ("Operating margin", "operating_margin", "%"),
        ("ROE", "roe", "%"),
        ("ROCE", "roce", "%"),
        ("Debt / equity", "debt_equity", "x"),
        ("Operating cash flow", "operating_cash_flow", "Rs Cr"),
        ("P/E", "pe", "x"),
        ("P/B", "pb", "x"),
        ("Revenue CAGR 3Y", "revenue_cagr3", "%"),
        ("Profit CAGR 3Y", "profit_cagr3", "%"),
    ):
        detail = (fund.get("metrics") or {}).get(key)
        if not isinstance(detail, dict):
            continue
        value = _safe_float(detail.get("value"))
        if value is None or (unit == "x" and value < 0):
            continue
        period = str(detail.get("period") or "not supplied")[:22]
        # No fabricated period for undated vendor ratios.
        values.append({"label": label, "value": value, "unit": unit,
                       "period": period})
    return {
        "status": "SECONDARY" if values else "UNAVAILABLE",
        "metrics": values,
        "scope": fund["scope"], "source": fund["source"],
        "annual_period": fund.get("annual_period") or "unknown",
        "retrieved_utc": fund.get("retrieved_utc"),
        "issuer_verified": False,
    }


def prepare_company_pages(holdings, frames, *, fundamentals=None,
                          industries=None, now=None):
    """Return one technical research dossier per submitted holding.

    Each dossier requires the completed-session candle of that same holding.
    An unavailable ticker's history yields a labelled incomplete dossier
    rather than accidentally showing facts for a different security.
    """
    if not 1 <= len(holdings) <= MAX_COMPANIES:
        return []
    now = now or datetime.now(IST)
    fundamentals = fundamentals or {}
    industries = industries or {}
    pages = []
    for row in holdings:
        sym = str(row["symbol"])
        body = {
            "symbol": sym, "date": str(row["date"]),
            "industry": str(industries.get(sym) or "Industry not independently classified")[:60],
            "status": "UNAVAILABLE", "financial": _financial_evidence(
                fundamentals.get(sym)), "chart": [], "chart_dates": [],
        }
        try:
            frame = verified_completed_daily_frame(frames.get(sym), now=now)
            if frame.index[-1].date().isoformat() != body["date"] or len(frame) < 60:
                pages.append(body)
                continue
            closes = [_safe_float(v) for v in frame["Close"].tolist()]
            if any(v is None or v <= 0 for v in closes):
                pages.append(body)
                continue
            # Only corroborated records may be charted under their symbol.
            if abs(closes[-1] - float(row["close"])) > .011:
                pages.append(body)
                continue
            dates = [x.date().isoformat() for x in frame.index[-MAX_CHART_DAYS:]]
            closes_chart = closes[-MAX_CHART_DAYS:]
            tail = closes[-min(252, len(closes)):]
            change20 = 100 * (closes[-1] / closes[-21] - 1)
            day_returns = [p / q - 1 for q, p in zip(closes[-61:-1],
                                                       closes[-60:])]
            vol = (100 * math.sqrt(252) *
                   (sum((v - sum(day_returns) / len(day_returns)) ** 2
                        for v in day_returns) / (len(day_returns) - 1)) ** .5)
            row20 = _safe_float(row.get("ema20"))
            row50 = _safe_float(row.get("ema50"))
            ema200 = _ema(closes, 200)
            if not all(math.isfinite(v) for v in (change20, vol)):
                pages.append(body)
                continue
            volume_ratio = None
            if "Volume" in frame and len(frame) >= 21:
                volume = [_safe_float(v) for v in frame["Volume"].iloc[-21:].tolist()]
                if all(v is not None and v >= 0 for v in volume):
                    average = sum(volume[:-1]) / 20
                    if average > 0:
                        volume_ratio = round(volume[-1] / average, 2)
            body.update(
                status="VERIFIED", close=closes[-1], ema20=row20,
                ema50=row50, ema200=ema200,
                rsi14=_rsi(closes), macd_hist=_macd_hist(closes),
                atr14_pct=_atr_pct(frame, closes),
                volume_ratio=volume_ratio,
                return20=round(change20, 2),
                annual_vol_pct=round(vol, 2),
                year_close_high=max(tail),
                year_close_low=min(tail),
                chart=[round(x, 4) for x in closes_chart],
                chart_ema20=[round(x, 4) for x in _ema_series(closes, 20)[-MAX_CHART_DAYS:]],
                chart_ema50=[round(x, 4) for x in _ema_series(closes, 50)[-MAX_CHART_DAYS:]],
                chart_dates=dates,
            )
        except Exception:
            # Neither an error message nor a ticker enters MSK server logs.
            pass
        pages.append(body)
    return pages
