"""Compatibility adapter: NEW verified quant data, EXISTING MSK PNG canvas.

Do not move axes, redraw the legacy information panel, or fetch market data
here. The untouched gen_smart_stock_chart.py remains the sole layout owner.
"""
import math

import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from trading_chart.engine import analyze
from trading_chart.config import DEFAULT_CONFIG

# Visual accents deliberately match gen_smart_stock_chart.py v7.
_GREEN = "#26A69A"
_RED = "#EF5350"
_AMBER = "#F6C026"
_SECONDARY = "#787B86"
_PRIMARY = "#D1D4DC"
_CARD = "#2A2E39"
_BORDER = "#363A45"


def compute_chart_evidence(ohlcv):
    """Convert real closed-bar OHLCV to causal pattern/ADX/volume evidence.

    Never convert missing or NaN signals into a buy/sell. Preserve source date.
    Returns both indicator series and the six-field signal payload.
    """
    if not isinstance(ohlcv, pd.DataFrame):
        raise TypeError("Chart data must be a DataFrame")
    names = ("Open", "High", "Low", "Close", "Volume")
    if not all(k in ohlcv.columns for k in names):
        raise ValueError("Missing OHLCV columns")
    d = pd.DataFrame({
        "timestamp": pd.to_datetime(ohlcv.index, utc=True),
        "open": ohlcv["Open"].to_numpy(dtype=float),
        "high": ohlcv["High"].to_numpy(dtype=float),
        "low": ohlcv["Low"].to_numpy(dtype=float),
        "close": ohlcv["Close"].to_numpy(dtype=float),
        "volume": ohlcv["Volume"].to_numpy(dtype=float),
    })
    df, payload = analyze(d)
    return df, payload


def _valid(value):
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def formatted_rows(payload):
    """Always return the six exact requested field names in a fixed order."""
    trend = str(payload.get("trend") or "Insufficient data")
    pattern = str(payload.get("pattern") or "No clean pattern")
    adx = payload.get("adx")
    vol = payload.get("vol_multiple")
    adx_text = f"{adx:.0f} {payload.get('adx_label', 'Unavailable')}" if _valid(adx) else "Insufficient data"
    vol_text = f"{vol:.1f}x" if _valid(vol) else "Insufficient data"
    if _valid(vol) and payload.get("vol_label"):
        vol_text += " " + str(payload["vol_label"])
    signal = str(payload.get("signal") or "WAIT")
    if signal not in ("BUY", "SELL", "WAIT"):
        signal = "WAIT"
    quality = str(payload.get("quality") or "Weak")
    if quality not in ("High", "Medium", "Weak"):
        quality = "Weak"
    return [
        ("Trend", trend),
        ("Pattern", pattern),
        ("ADX", adx_text),
        ("Volume", vol_text),
        ("Signal", signal),
        ("Quality", quality),
    ]


def draw_signal_rows(sidebar, payload, border_color=_BORDER):
    """Paint spacious six-line data panel into original right sidebar region.

    The old header, score indicator, trade levels and watermark stay in place.
    """
    rows = formatted_rows(payload)
    top, bottom = 0.793, 0.218
    gap = (top - bottom) / len(rows)
    sidebar.text(0.065, 0.812, "CONFIRMED QUANT READOUT",
                 fontsize=7.0, color=_SECONDARY, fontweight="bold",
                 va="center", zorder=8)
    for i, (label, value) in enumerate(rows):
        y = top - i * gap
        if i % 2 == 0:
            sidebar.add_patch(
                FancyBboxPatch(
                    (0.043, y-gap*0.94), 0.91, gap*0.9,
                    boxstyle="round,pad=0.002", fc=_CARD,
                    ec="none", alpha=0.58, zorder=3))
        color = (_GREEN if value in ("BUY", "Uptrend", "High") else
                 _RED if value in ("SELL", "Downtrend") else
                 _AMBER if value in ("WAIT", "Weak") else _PRIMARY)
        sidebar.text(0.075, y-gap*0.49, label.upper(),
                     color=_SECONDARY, fontsize=7.4, fontweight="bold",
                     va="center", ha="left", zorder=7)
        # Long pattern names are intentionally single-line; no clipping/overlap.
        sidebar.text(0.93, y-gap*0.49, value[:22], color=color, fontsize=7.9,
                     ha="right", va="center", fontweight="bold", zorder=7)
    sidebar.plot([0.05, 0.95], [0.205, 0.205], lw=0.65,
                 color=border_color, zorder=7)


def draw_price_evidence(ax, df, payload):
    """Subtle regression channel, one confirmed pattern label and one marker.

    Background fill is 5% opacity; no Fibonacci/Elliott clutter is added.
    No marker is shown unless the *causal* signal engine confirmed it.
    """
    if payload.get("status") != "OK" or len(df) < DEFAULT_CONFIG.min_bars:
        return
    n = len(df)
    bars = min(DEFAULT_CONFIG.channel_bars, n)
    xs = np.arange(n-bars, n)
    closes = df["close"].to_numpy(dtype=float)[-bars:]
    gradient, intercept = np.polyfit(np.arange(bars), closes, 1)
    centre = intercept + gradient*np.arange(bars)
    spread = float(np.std(closes-centre))
    if not math.isfinite(spread):
        return
    width = max(spread*DEFAULT_CONFIG.channel_band_stdev,
                float(df["atr"].iloc[-1])*DEFAULT_CONFIG.channel_atr_floor)
    upper, lower = centre + width, centre - width
    ax.plot(xs, upper, color="#26C6DA", lw=0.85, alpha=0.52, ls="--", zorder=2)
    ax.plot(xs, lower, color="#26C6DA", lw=0.85, alpha=0.52, ls="--", zorder=2)
    ax.fill_between(xs, lower, upper, color="#26C6DA", alpha=0.035, zorder=1)
    anchors = payload.get("pattern_anchors") or []
    if anchors:
        item = anchors[0]
        x = item.get("index")
        if isinstance(x, int) and max(0, n-DEFAULT_CONFIG.visible_bars) <= x < n:
            label = str(item.get("name") or "Pattern")[:23]
            direction = item.get("direction")
            color = _GREEN if direction == "BUY" else _RED
            y = float(df["low"].iloc[x] if direction == "BUY" else df["high"].iloc[x])
            ax.annotate(f"{label}  {item.get('confidence', 0):.0f}/100",
                        xy=(x, y), xytext=(5, 14 if direction=="BUY" else -19),
                        textcoords="offset points", fontsize=6.4,
                        color=color, fontweight="bold",
                        bbox={"boxstyle":"round,pad=0.25",
                              "facecolor":"#131722", "edgecolor":color,
                              "alpha":0.88, "linewidth":0.7},
                        zorder=9)
    markers = payload.get("markers") or []
    if markers:
        item = markers[0]
        x = item.get("index")
        if isinstance(x, int) and 0 <= x < n:
            direction = item.get("type")
            if direction in ("BUY", "SELL"):
                price = float(df["low"].iloc[x] if direction=="BUY" else df["high"].iloc[x])
                color = _GREEN if direction == "BUY" else _RED
                ax.scatter([x], [price], marker="^" if direction=="BUY" else "v",
                           c=[color], s=85, edgecolors="#131722",
                           linewidths=0.7, zorder=12)
                ax.annotate(direction, xy=(x, price),
                            xytext=(0,-16 if direction=="BUY" else 16),
                            textcoords="offset points", fontsize=6.2,
                            color=color, fontweight="bold", ha="center", zorder=12)
