"""Evidence-based Conservative technical watchlist, not guaranteed-safe investment advice.

The candidate pool is diversified and static; its membership is NOT a verified
current index list or evidence of financial strength. Only completed daily
candles are scored, and missing data fails closed. No fabricated data/targets.
"""
from datetime import timedelta
import math

import pandas as pd


# Liquid, established NSE candidate names to SEARCH, never guaranteed picks.
# Sector buckets prevent a single industry dominating the output.
CONSERVATIVE_SECTORS = {
    "Banking": ("HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK"),
    "IT": ("TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"),
    "FMCG": ("ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO"),
    "Pharma": ("SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "LUPIN"),
    "Utilities": ("NTPC", "POWERGRID", "COALINDIA", "ONGC", "GAIL"),
    "Auto": ("MARUTI", "M&M", "EICHERMOT", "BAJAJ-AUTO"),
    "Infrastructure": ("LT", "SIEMENS", "ABB", "CUMMINSIND"),
    "Consumer": ("ASIANPAINT", "PIDILITIND", "TITAN", "TATACONSUM"),
    "Telecom": ("BHARTIARTL",),
    "Diversified": ("RELIANCE", "ULTRACEMCO", "GRASIM"),
}
CONSERVATIVE_CANDIDATES = tuple(
    (symbol, sector) for sector, tickers in CONSERVATIVE_SECTORS.items()
    for symbol in tickers
)
SECTOR_BY_SYMBOL = dict(CONSERVATIVE_CANDIDATES)
# Conservative gates are screening heuristics, not an empirical safety rating.
MIN_BARS = 105
MAX_STALE_DAYS = 7
MIN_MEDIAN_TURNOVER_RUPEES = 10_00_00_000  # ₹10 crore (approx. Close × Volume)
MAX_ATR_PERCENT = 3.5
MAX_ANNUAL_VOL_PERCENT = 45.0


def evaluate_conservative(sym, df, asof, sector_override=None):
    """Return a qualifying technical observation or None.

    asof is the IST calendar date; same-day candles are excluded because
    the bar may not be final. Price/volume can be adjusted/provider-delayed.
    """
    if (sym not in SECTOR_BY_SYMBOL and not sector_override) or df is None or df.empty:
        return None
    if not {"Close", "High", "Low", "Volume"}.issubset(df.columns):
        return None

    try:
        dates = pd.to_datetime(df.index, errors="coerce")
        if dates.isna().any():
            return None
        complete = [d.date() < asof for d in dates]
        candles = df.loc[complete].copy()
        if len(candles) < MIN_BARS:
            return None
        last_date = pd.to_datetime(candles.index[-1]).date()
        if last_date < asof - timedelta(days=MAX_STALE_DAYS):
            return None

        numeric = candles[["Close", "High", "Low", "Volume"]].apply(
            pd.to_numeric, errors="coerce"
        )
        if not numeric.tail(MIN_BARS).notna().all().all():
            return None
        close, high, low, volume = (
            numeric["Close"], numeric["High"], numeric["Low"], numeric["Volume"]
        )
        if (close.tail(MIN_BARS) <= 0).any():
            return None
        if (high.tail(MIN_BARS) < low.tail(MIN_BARS)).any():
            return None
        if (volume.tail(20) <= 0).any():
            return None

        latest = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema100 = float(close.ewm(span=100, adjust=False).mean().iloc[-1])
        if not (latest > ema20 > ema50 > ema100):
            return None

        move20 = (latest / float(close.iloc[-21]) - 1) * 100
        move60 = (latest / float(close.iloc[-61]) - 1) * 100
        if not (0 < move20 <= 25 and move60 > 0):
            return None

        delta = close.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        avg_gain, avg_loss = float(gains.iloc[-1]), float(losses.iloc[-1])
        rsi = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
        if not 52 <= rsi <= 72:
            return None

        prev_close = close.shift(1)
        true_range = pd.concat(
            [(high - low), (high - prev_close).abs(),
             (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr_pct = float(true_range.tail(14).mean()) / latest * 100
        annual_vol = float(close.pct_change().tail(20).std(ddof=0)) * math.sqrt(252) * 100
        max_pullback = (1 - latest / float(close.tail(20).max())) * 100
        turnover = float((close * volume).tail(20).median())
        day_change = (latest / prev - 1) * 100
        checks = [latest, prev, ema20, ema50, ema100, move20, move60,
                  rsi, atr_pct, annual_vol, max_pullback, turnover, day_change]
        if not all(math.isfinite(v) for v in checks):
            return None
        if not (0 < atr_pct <= MAX_ATR_PERCENT and
                0 <= annual_vol <= MAX_ANNUAL_VOL_PERCENT and
                0 <= max_pullback <= 10 and
                turnover >= MIN_MEDIAN_TURNOVER_RUPEES):
            return None
        # Ranking is relative to this scan only; never a win probability.
        score = (min(move20, 15) * 1.5 + min(move60, 35) * 0.5
                 - atr_pct * 4 - annual_vol * 0.15)
        return {
            "sym": sym, "sector": sector_override or SECTOR_BY_SYMBOL[sym],
            "ltp": round(latest, 2), "chg": round(day_change, 2),
            "rsi": round(rsi, 1), "atr_pct": round(atr_pct, 2),
            "volatility_pct": round(annual_vol, 1),
            "move20_pct": round(move20, 1), "asof": last_date.isoformat(),
            "score": round(score, 2),
        }
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError):
        return None


def pick_diversified(results, limit=10, max_per_sector=2):
    """Choose up to limit qualified rows without inventing missing picks."""
    chosen = []
    counts = {}
    seen = set()
    for row in sorted(
        (item for item in results if item and item.get("sym") in SECTOR_BY_SYMBOL),
        key=lambda row: (row["score"], -row["atr_pct"], row["sym"]),
        reverse=True,
    ):
        sym, sector = row["sym"], row["sector"]
        if sym in seen or counts.get(sector, 0) >= max_per_sector:
            continue
        chosen.append(row)
        seen.add(sym)
        counts[sector] = counts.get(sector, 0) + 1
        if len(chosen) == limit:
            break
    return chosen
