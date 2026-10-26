"""MSK PDF technical chart upgrade, built only from the report's SAME EOD OHLCV.

The standalone gen_smart_stock_chart.py performs downloads at import time and
uses different RSI/ADX formulas. Never import it here. Reuse its *visual ideas*
(candles, channel, volume flags, patterns) and the canonical indicator library
instead. No network calls, inferred live prices or fabricated BUY/SELL labels.
"""
from __future__ import annotations

import logging
import math
import os
import tempfile
import uuid

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from technical_indicators import rsi_series

logger = logging.getLogger(__name__)
NAVY = "#0B2545"
BLUE = "#1976D2"
ORANGE = "#E39B18"
PURPLE = "#8E44AD"
GREEN = "#15805A"
RED = "#C0392B"
GREY = "#5D6979"
PANEL = "#F2F6FC"


def validate_price_history(df: pd.DataFrame, tech: dict) -> None:
    """Fail closed rather than drawing incorrect signals from short/bad candles."""
    required = ("Open", "High", "Low", "Close", "Volume")
    if df is None or len(df) < 200 or any(k not in df for k in required):
        raise ValueError("Verified 200+ OHLCV candles required for technical PDF")
    if not df.index.is_monotonic_increasing or not df.index.is_unique:
        raise ValueError("Price candles are not uniquely dated and ordered")
    arr = df[list(required)].to_numpy(dtype=float)
    if not np.isfinite(arr).all():
        raise ValueError("OHLCV contains missing/infinite data")
    o, h, l, c, v = (df[k].to_numpy(dtype=float) for k in required)
    if np.any(o <= 0) or np.any(c <= 0) or np.any(h < np.maximum(o, c)) or np.any(l > np.minimum(o, c)) or np.any(l <= 0):
        raise ValueError("Price candle has inconsistent OHLC values")
    if np.any(v < 0):
        raise ValueError("Negative share volume")
    last_close = float(c[-1])
    if not math.isfinite(float(tech.get("ltp", float("nan")))) or abs(float(tech["ltp"]) - last_close) > max(0.015, last_close * 0.00001):
        raise ValueError("Chart and research report use different last closes")


def adx_series(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX/+DI/-DI from Wilder-smoothed true range and directional movement.

    Negative DM is based on actual LOW FALLS (previous low - current low);
    using abs(low.diff()) falsely counts higher lows as negative momentum.
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    up = high.diff()
    down = -low.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    alpha = 1 / period
    smoothed_tr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    minus_di = 100 * minus.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def confirmed_double_pattern(df: pd.DataFrame, lookback: int = 70, width: int = 4) -> dict | None:
    """Return a potential or neckline-confirmed double top/bottom. No guessed targets.

    Pivot requires four completed candles on BOTH sides. Only the last close
    breaching the intervening neckline may confirm a candidate.
    """
    d = df.iloc[-lookback:]
    if len(d) < 30:
        return None
    peaks, troughs = [], []
    h, l = d["High"].to_numpy(), d["Low"].to_numpy()
    for i in range(width, len(d)-width):
        if h[i] > max(np.max(h[i-width:i]), np.max(h[i+1:i+width+1])):
            peaks.append(i)
        if l[i] < min(np.min(l[i-width:i]), np.min(l[i+1:i+width+1])):
            troughs.append(i)
    outcomes = []
    # Last two confirmed pivots can form a clear, readable structure.
    for direction, piv, data, neckline_data in (("Double Top", peaks, h, l), ("Double Bottom", troughs, l, h)):
        if len(piv) < 2:
            continue
        i, j = piv[-2:]
        if not 6 <= j-i <= 48 or abs(data[i]-data[j]) / max(abs(data[i]), abs(data[j])) > 0.025:
            continue
        neck = float(np.min(neckline_data[i+1:j])) if direction == "Double Top" else float(np.max(neckline_data[i+1:j]))
        last = float(d["Close"].iloc[-1])
        confirmed = last < neck if direction == "Double Top" else last > neck
        outcomes.append({"name": direction, "confirmed": confirmed,
                         "pivots": (i, j), "neckline": neck,
                         "view_offset": len(df) - len(d)})
    return sorted(outcomes, key=lambda p: (not p["confirmed"], -p["pivots"][1]))[0] if outcomes else None


def _series(df: pd.DataFrame) -> dict:
    c = df["Close"]
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    rsi = rsi_series(c, 14)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    adx, pdi, mdi = adx_series(df)
    volavg = df["Volume"].shift(1).rolling(20, min_periods=20).mean()
    ratio = df["Volume"] / volavg.replace(0, np.nan)
    return dict(ema20=ema20, ema50=ema50, ema200=ema200, rsi=rsi,
                macd=macd, sig=macd_sig, hist=macd-macd_sig, adx=adx,
                pdi=pdi, mdi=mdi, volavg=volavg, ratio=ratio)


def breakout_observation(df: pd.DataFrame, z: dict) -> dict:
    """Evidence-based observation, never an automatic trade recommendation."""
    c = df["Close"]
    prior_hi = df["High"].shift(1).rolling(20).max()
    prior_lo = df["Low"].shift(1).rolling(20).min()
    idx = -1
    vr = float(z["ratio"].iloc[idx]) if np.isfinite(z["ratio"].iloc[idx]) else float("nan")
    adx = float(z["adx"].iloc[idx]) if np.isfinite(z["adx"].iloc[idx]) else float("nan")
    pdi, mdi = float(z["pdi"].iloc[idx]), float(z["mdi"].iloc[idx])
    up = bool(c.iloc[-1] > prior_hi.iloc[-1] and z["ema20"].iloc[-1] > z["ema50"].iloc[-1]
              and vr >= 2.0 and adx >= 20 and pdi > mdi)
    down = bool(c.iloc[-1] < prior_lo.iloc[-1] and z["ema20"].iloc[-1] < z["ema50"].iloc[-1]
                and vr >= 2.0 and adx >= 20 and mdi > pdi)
    return {"status": "BREAKOUT OBSERVED" if up else "BREAKDOWN OBSERVED" if down else "WAIT / NO CONFIRMED SETUP",
            "verified": up or down, "vol_ratio": vr, "adx": adx,
            "price": float(c.iloc[-1]), "date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])[:10]}


def _chart_path(symbol: str, kind: str) -> str:
    safe = "".join(ch for ch in symbol.upper() if ch.isascii() and (ch.isalnum() or ch in "-_"))[:20] or "EQUITY"
    p = os.path.join(tempfile.gettempdir(), "msk_research_charts")
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, f"{safe}_{kind}_{uuid.uuid4().hex[:10]}.png")


def _footer(fig, text: str):
    fig.text(.02, .012, text, ha="left", fontsize=8, color=GREY)
    fig.text(.73, .42, "MSK RESEARCH", ha="center", va="center", fontsize=29,
             color="#DCE3EB", alpha=.24, rotation=25, zorder=-1)


def _render_price(df: pd.DataFrame, sym: str, t: dict, z: dict, summary: dict) -> str:
    d = df.iloc[-105:]
    x = np.arange(len(d))
    o, c, hi, lo = (d[k].to_numpy(float) for k in ("Open", "Close", "High", "Low"))
    up = c >= o
    colors = np.where(up, GREEN, RED)
    fig, ax = plt.subplots(figsize=(9, 5.7))
    fig.subplots_adjust(left=.08, right=.95, top=.85, bottom=.19)
    ax.set_facecolor("#FAFCFE")
    ax.vlines(x, lo, hi, colors=colors, linewidth=.8, zorder=3)
    bodies = np.maximum(np.abs(c-o), c*.0003)
    ax.bar(x, bodies, bottom=np.minimum(o, c), color=colors, width=.66, zorder=4)
    for k, col, title, lw in (("ema20", ORANGE, "EMA20", 1.3), ("ema50", BLUE, "EMA50", 1.4), ("ema200", PURPLE, "EMA200", 1.2)):
        values = z[k].iloc[-len(d):].to_numpy()
        ax.plot(x, values, color=col, label=f"{title} {values[-1]:,.1f}", lw=lw, zorder=5)

    # Least-squares trend channel on the last 60 completed closes; statistical, not predictive.
    window = min(60, len(d))
    if window >= 40:
        xx = np.arange(window, dtype=float)
        yy = c[-window:]
        slope, intercept = np.polyfit(xx, yy, 1)
        mid = slope*xx + intercept
        residual = yy-mid
        std = np.std(residual)
        if np.isfinite(std) and std > 0:
            bounds = 1.5*std
            xxplot = np.arange(len(d)-window, len(d))
            ax.plot(xxplot, mid, color=GREY, linewidth=.9, linestyle=":", label="60D regression", zorder=2)
            ax.plot(xxplot, mid+bounds, color=GREY, linewidth=.75, linestyle="--", zorder=2)
            ax.plot(xxplot, mid-bounds, color=GREY, linewidth=.75, linestyle="--", zorder=2)
            ax.fill_between(xxplot, mid-bounds, mid+bounds, color=BLUE, alpha=.055, zorder=1)
    for label, key, color in (("S", "supports", GREEN), ("R", "resistances", RED)):
        levels = t.get(key) or []
        if levels:
            value = float(levels[0]["price"])
            if np.isfinite(value) and value > 0:
                ax.axhline(value, color=color, ls="--", lw=.95, alpha=.8)
                ax.annotate(f"{label} {value:,.1f}", (len(d)+.6, value), fontsize=8, color=color, va="center")
    pattern = confirmed_double_pattern(df)
    if pattern:
        # Translate confirmed full-history pivot positions to displayed x-coordinates.
        points = [p + pattern["view_offset"] - (len(df)-len(d)) for p in pattern["pivots"]]
        if all(0 <= p < len(d) for p in points):
            series = hi if pattern["name"]=="Double Top" else lo
            ax.scatter(points, [series[p] for p in points], color=RED if pattern["name"]=="Double Top" else GREEN,
                       s=45, marker="v" if pattern["name"]=="Double Top" else "^", zorder=7)
            ax.plot(points, [series[p] for p in points], ls=":", lw=1, color=RED if pattern["name"]=="Double Top" else GREEN)
            ax.axhline(pattern["neckline"], color=GREY, lw=.8, ls="-.")
    if summary["verified"]:
        marker = "^" if "BREAKOUT" in summary["status"] else "v"
        color = GREEN if marker == "^" else RED
        ax.scatter([len(d)-1], [lo[-1]*.993 if marker=="^" else hi[-1]*1.007],
                   s=130, marker=marker, color=color, edgecolors="white", linewidths=.7, zorder=8)
    dates = np.linspace(0, len(d)-1, 7, dtype=int)
    ax.set_xticks(dates)
    ax.set_xticklabels([str(d.index[i])[:10] for i in dates], rotation=20, fontsize=7.5)
    ax.set_xlim(-1, len(d)+11)
    ax.set_ylabel("INR / share")
    ax.grid(axis="y", alpha=.25)
    ax.legend(loc="upper left", fontsize=7.3, ncol=4, frameon=False)
    pattern_name = ((pattern["name"] + (" · neckline confirmed" if pattern["confirmed"] else " · candidate only"))
                    if pattern else "No confirmed double-top/bottom pattern")
    fig.suptitle(f"{sym}  |  TECHNICAL PRICE SETUP  |  EOD {summary['date']}", x=.065, y=.965,
                 ha="left", weight="bold", size=13, color=NAVY)
    fig.text(.065, .907, f"{summary['status']}  ·  {pattern_name}", fontsize=9, color=GREEN if summary["verified"] else GREY)
    _footer(fig, "Candlesticks + EMAs + regression channel + verified pivots. EOD only. Pattern detection is heuristic.")
    dest = _chart_path(sym, "price")
    fig.savefig(dest, dpi=160, facecolor="white")
    plt.close(fig)
    return dest


def _render_momentum(df: pd.DataFrame, sym: str, z: dict, summary: dict) -> str:
    d = df.iloc[-105:]
    x = np.arange(len(d))
    size = len(d)
    fig, (av, ar, am, aa) = plt.subplots(4, 1, figsize=(9, 6.35), sharex=True,
                                         gridspec_kw={"height_ratios": [1.1, 1, 1.3, 1]})
    fig.subplots_adjust(left=.085, right=.96, top=.88, bottom=.14, hspace=.13)
    vc = np.where(d["Close"].values >= d["Open"].values, GREEN, RED)
    vols = (d["Volume"] / 100000).values
    ma = (z["volavg"].iloc[-size:] / 100000).values
    av.bar(x, vols, color=vc, width=.72)
    av.plot(x, ma, color=NAVY, lw=1.15, label="Prior 20-session volume mean")
    spikes = np.where((z["ratio"].iloc[-size:] >= 2.0).fillna(False).to_numpy())[0]
    # Annotate 2x and 3x volume only on the last 50 sessions; cap labels to four.
    for p in spikes[spikes >= size-50][-4:]:
        v = float(z["ratio"].iloc[-size+p])
        av.scatter([p], [vols[p]], color=ORANGE, s=24, zorder=4)
        av.annotate(f"{v:.1f}x", (p, vols[p]), xytext=(0, 5), textcoords="offset points",
                    ha="center", fontsize=7.3, color=ORANGE)
    av.set_ylabel("Vol (L)", fontsize=8)
    av.legend(loc="upper left", fontsize=7.3, frameon=False)
    ar.plot(x, z["rsi"].iloc[-size:].values, color=PURPLE, lw=1.35)
    for level, color in ((70, RED), (50, GREY), (30, GREEN)):
        ar.axhline(level, lw=.6, ls="--", color=color, alpha=.7)
    ar.set_ylim(0, 100)
    ar.set_ylabel("RSI14", fontsize=8)
    ax_hist = z["hist"].iloc[-size:].values
    am.bar(x, ax_hist, color=np.where(ax_hist>=0,GREEN,RED), width=.7, alpha=.7)
    am.plot(x, z["macd"].iloc[-size:].values, color=BLUE, lw=1.1, label="MACD(12,26)")
    am.plot(x, z["sig"].iloc[-size:].values, color=ORANGE, lw=1, label="Signal(9)")
    am.axhline(0, color=GREY, lw=.5)
    am.set_ylabel("MACD", fontsize=8)
    am.legend(loc="upper left", fontsize=7.2, frameon=False, ncol=2)
    aa.plot(x, z["adx"].iloc[-size:].values, color=NAVY, lw=1.35, label="ADX14")
    aa.plot(x, z["pdi"].iloc[-size:].values, color=GREEN, lw=.85, label="+DI")
    aa.plot(x, z["mdi"].iloc[-size:].values, color=RED, lw=.85, label="-DI")
    aa.axhline(25, color=ORANGE, lw=.8, ls="--", label="ADX 25 reference")
    aa.set_ylabel("ADX / DI", fontsize=8)
    aa.legend(loc="upper left", fontsize=7.2, frameon=False, ncol=4)
    for ax in (av, ar, am, aa):
        ax.grid(axis="y", alpha=.19)
        ax.tick_params(labelsize=7.5)
        ax.spines[["top", "right"]].set_visible(False)
    ticks = np.linspace(0, size-1, 7, dtype=int)
    aa.set_xticks(ticks)
    aa.set_xticklabels([str(d.index[i])[:10] for i in ticks], rotation=20)
    fig.suptitle(f"{sym}  |  MOMENTUM + VOLUME CONFIRMATION  |  EOD {summary['date']}",
                 x=.07, y=.96, ha="left", fontsize=12.6, color=NAVY, weight="bold")
    ratio_text = f"{summary['vol_ratio']:.2f}x prior 20D avg" if np.isfinite(summary["vol_ratio"]) else "Volume comparison unavailable"
    fig.text(.08, .906, f"Current volume: {ratio_text}   |   ADX: {summary['adx']:.1f}   |   {summary['status']}",
             color=GREY, size=8.7)
    _footer(fig, "2x and 3x flags are volume observations, not guaranteed signals. ADX is trend strength, not direction.")
    dest = _chart_path(sym, "momentum")
    fig.savefig(dest, dpi=160, facecolor="white")
    plt.close(fig)
    return dest


def build_setup_charts(df: pd.DataFrame, sym: str, tech: dict) -> dict | None:
    """Render two PDF-sized charts, or return None with no misleading partial PDF."""
    files = []
    try:
        validate_price_history(df, tech)
        z = _series(df)
        summary = breakout_observation(df, z)
        if not np.isfinite(summary["adx"]):
            raise ValueError("ADX not established from enough candles")
        price = _render_price(df, sym, tech, z, summary)
        files.append(price)
        momentum = _render_momentum(df, sym, z, summary)
        files.append(momentum)
        return {"price": price, "momentum": momentum, "summary": summary}
    except Exception as exc:
        for file in files:
            try:
                os.unlink(file)
            except OSError:
                pass
        logger.warning("[MSK PDF chart] advanced setup omitted: %s", str(exc))
        return None
