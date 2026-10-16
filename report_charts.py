"""
report_charts.py — Chart builders for the institutional research report
────────────────────────────────────────────────────────────────────────────────
Every function returns a PNG path or None (never raises), so one failed chart
just removes that figure from the PDF instead of killing the whole report.
"""

import os
import logging
import tempfile
from typing import List, Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

logger = logging.getLogger(__name__)

NAVY, ACCENT = "#0B2545", "#1E88E5"
GREEN, RED, GREY, AMBER = "#1B8A5A", "#C62828", "#5A6472", "#D98E04"
LIGHT = "#E8EDF4"
DPI = 170

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8,
    "axes.edgecolor": "#B8C0CC", "axes.linewidth": 0.7,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlesize": 9, "axes.titleweight": "bold", "axes.titlecolor": NAVY,
    "axes.labelcolor": GREY, "xtick.color": GREY, "ytick.color": GREY,
    "grid.color": "#E3E8EF", "grid.linewidth": 0.6,
})


def _tmp(name: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "aaa_report_charts")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _save(fig, name) -> str:
    path = _tmp(name)
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _inr(x, _pos=None):
    return f"₹{x:,.0f}"


# ══════════════════════════════════════════════════════════════════════════
def chart_technical(df: pd.DataFrame, sym: str, t: dict, bars: int = 150) -> Optional[str]:
    try:
        d = df.iloc[-bars:].copy()
        c = df["Close"]
        e20 = c.ewm(span=20, adjust=False).mean().iloc[-bars:]
        e50 = c.ewm(span=50, adjust=False).mean().iloc[-bars:]
        e200 = c.ewm(span=200, adjust=False).mean().iloc[-bars:] if len(c) >= 200 else None
        delta = c.diff()
        g = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        ls = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean().replace(0, 1e-10)
        rsi = (100 - 100 / (1 + g / ls)).iloc[-bars:]
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        sig = macd.ewm(span=9, adjust=False).mean()
        hist = (macd - sig).iloc[-bars:]
        macd, sig = macd.iloc[-bars:], sig.iloc[-bars:]

        fig = plt.figure(figsize=(7.4, 7.6))
        gs = fig.add_gridspec(4, 1, height_ratios=[5, 1.3, 1.5, 1.5], hspace=0.07)
        ax = fig.add_subplot(gs[0])
        axv = fig.add_subplot(gs[1], sharex=ax)
        axr = fig.add_subplot(gs[2], sharex=ax)
        axm = fig.add_subplot(gs[3], sharex=ax)

        x = np.arange(len(d))
        up = d["Close"] >= d["Open"]
        col = np.where(up, GREEN, RED)
        ax.vlines(x, d["Low"], d["High"], color=col, linewidth=0.7, zorder=2)
        body_lo = np.minimum(d["Open"], d["Close"])
        body_h = np.maximum((d["Open"] - d["Close"]).abs(), d["Close"] * 0.0006)
        ax.bar(x, body_h, bottom=body_lo, color=col, width=0.62, zorder=3)
        ax.plot(x, e20.values, color="#F5A623", lw=1.0, label="EMA 20", zorder=4)
        ax.plot(x, e50.values, color=ACCENT, lw=1.1, label="EMA 50", zorder=4)
        if e200 is not None:
            ax.plot(x, e200.values, color="#7B1FA2", lw=1.2, label="EMA 200", zorder=4)

        xr = len(d) + 1
        for s in t.get("supports", [])[:2]:
            ax.axhline(s["price"], color=GREEN, lw=0.8, ls="--", alpha=0.8, zorder=1)
            ax.text(0.5, s["price"], f"S ₹{s['price']:,.0f}", color=GREEN, fontsize=7, va="bottom",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85), zorder=6)
        for r in t.get("resistances", [])[:2]:
            ax.axhline(r["price"], color=RED, lw=0.8, ls="--", alpha=0.8, zorder=1)
            ax.text(0.5, r["price"], f"R ₹{r['price']:,.0f}", color=RED, fontsize=7, va="bottom",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85), zorder=6)
        ax.text(xr, d["Close"].iloc[-1], f" ₹{d['Close'].iloc[-1]:,.2f}", color="white", fontsize=7.5,
                fontweight="bold", va="center",
                bbox=dict(boxstyle="round,pad=0.25", fc=NAVY, ec="none"))
        ax.set_xlim(-1, len(d) + 11)
        ax.yaxis.set_major_formatter(FuncFormatter(_inr))
        ax.grid(True, axis="y")
        ax.legend(loc="upper left", ncol=3, frameon=False, fontsize=7.5)
        ax.set_title(f"{sym} — Daily price, moving averages & key levels", loc="left")
        plt.setp(ax.get_xticklabels(), visible=False)

        axv.bar(x, d["Volume"] / 1e5, color=col, width=0.62, alpha=0.75)
        axv.plot(x, (df["Volume"].rolling(20).mean().iloc[-bars:] / 1e5).values, color=NAVY, lw=0.9)
        axv.set_ylabel("Vol (L)", fontsize=7)
        plt.setp(axv.get_xticklabels(), visible=False)

        axr.plot(x, rsi.values, color=NAVY, lw=1.0)
        axr.axhline(70, color=RED, lw=0.6, ls=":")
        axr.axhline(30, color=GREEN, lw=0.6, ls=":")
        axr.fill_between(x, 30, 70, color=LIGHT, alpha=0.6)
        axr.set_ylim(10, 90)
        axr.set_ylabel("RSI 14", fontsize=7)
        plt.setp(axr.get_xticklabels(), visible=False)

        axm.bar(x, hist.values, color=np.where(hist.values >= 0, GREEN, RED), width=0.62, alpha=0.7)
        axm.plot(x, macd.values, color=ACCENT, lw=0.9)
        axm.plot(x, sig.values, color="#F5A623", lw=0.9)
        axm.axhline(0, color=GREY, lw=0.5)
        axm.set_ylabel("MACD", fontsize=7)
        ticks = np.linspace(0, len(d) - 1, 7).astype(int)
        axm.set_xticks(ticks)
        axm.set_xticklabels([d.index[i].strftime("%d %b") if hasattr(d.index[i], "strftime") else str(i) for i in ticks],
                            fontsize=7)
        return _save(fig, f"{sym}_tech.png")
    except Exception as e:
        logger.warning(f"[charts] technical failed: {e}", exc_info=True)
        plt.close("all")
        return None


def chart_relative_and_drawdown(close: pd.Series, nifty_close: Optional[pd.Series], sym: str) -> Optional[str]:
    try:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 2.7), gridspec_kw={"wspace": 0.22})
        s = close / close.iloc[0] * 100
        x = np.arange(len(s))
        a1.plot(x, s.values, color=NAVY, lw=1.4, label=sym)
        if nifty_close is not None and len(nifty_close) > 20:
            n = nifty_close.reindex(close.index).ffill().bfill()
            n = n / n.iloc[0] * 100
            a1.plot(x, n.values, color="#9AA5B4", lw=1.2, label="Nifty 50")
            a1.fill_between(x, s.values, n.values, where=s.values >= n.values, color=GREEN, alpha=0.15)
            a1.fill_between(x, s.values, n.values, where=s.values < n.values, color=RED, alpha=0.15)
        a1.axhline(100, color=GREY, lw=0.5, ls=":")
        a1.set_title("Relative performance (rebased to 100)", loc="left")
        a1.legend(frameon=False, fontsize=7.5, loc="upper left")
        a1.grid(True, axis="y")
        ticks = np.linspace(0, len(s) - 1, 5).astype(int)
        a1.set_xticks(ticks)
        a1.set_xticklabels([close.index[i].strftime("%b %y") for i in ticks], fontsize=7)

        dd = (close / close.cummax() - 1) * 100
        a2.fill_between(x, dd.values, 0, color=RED, alpha=0.35)
        a2.plot(x, dd.values, color=RED, lw=0.9)
        a2.set_title("Drawdown from peak (%)", loc="left")
        a2.grid(True, axis="y")
        a2.set_xticks(ticks)
        a2.set_xticklabels([close.index[i].strftime("%b %y") for i in ticks], fontsize=7)
        a2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        return _save(fig, f"{sym}_rel_dd.png")
    except Exception as e:
        logger.warning(f"[charts] relative/drawdown failed: {e}")
        plt.close("all")
        return None


def chart_scorecard(pillars: dict, composite: int, sym: str) -> Optional[str]:
    try:
        names = [k for k, v in pillars.items() if v is not None]
        vals = [pillars[k] for k in names]
        labels = {"Risk": "Risk (higher = safer)"}
        fig, ax = plt.subplots(figsize=(7.4, 2.5))
        y = np.arange(len(names))[::-1]
        cols = [GREEN if v >= 65 else AMBER if v >= 45 else RED for v in vals]
        ax.barh(y, [100] * len(y), color=LIGHT, height=0.55, zorder=1)
        ax.barh(y, vals, color=cols, height=0.55, zorder=2)
        for yi, v in zip(y, vals):
            if v > 14:
                ax.text(v - 1.5, yi, f"{v}", va="center", ha="right", fontsize=8, fontweight="bold", color="white")
            else:
                ax.text(v + 1.5, yi, f"{v}", va="center", ha="left", fontsize=8, fontweight="bold", color=NAVY)
        ax.set_yticks(y)
        ax.set_yticklabels([labels.get(n, n) for n in names], fontsize=8.5)
        ax.set_xlim(0, 100)
        ax.axvline(65, color=GREEN, lw=0.6, ls=":")
        ax.axvline(45, color=AMBER, lw=0.6, ls=":")
        ax.set_xticks([0, 45, 65, 100])
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_title(f"Six-pillar scorecard — composite {composite}/100", loc="left")
        return _save(fig, f"{sym}_score.png")
    except Exception as e:
        logger.warning(f"[charts] scorecard failed: {e}")
        plt.close("all")
        return None


def chart_financials(years: List[dict], sym: str, unit: str = "₹ Cr") -> Optional[str]:
    try:
        yrs = [y for y in years if y.get("revenue") is not None]
        if len(yrs) < 3:
            return None
        yrs = yrs[-8:]
        labs = [y["label"].replace(" 20", " '") if " 20" in y["label"] else y["label"] for y in yrs]
        x = np.arange(len(yrs))
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 2.8), gridspec_kw={"wspace": 0.28})
        w = 0.38
        a1.bar(x - w / 2, [y["revenue"] for y in yrs], w, color=NAVY, label="Revenue")
        pats = [y.get("net_profit") or 0 for y in yrs]
        a1.bar(x + w / 2, pats, w, color=ACCENT, label="Net profit")
        a1.set_xticks(x)
        a1.set_xticklabels(labs, fontsize=6.8, rotation=30, ha="right")
        a1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        a1.set_title(f"Revenue & net profit ({unit})", loc="left")
        a1.legend(frameon=False, fontsize=7.5, loc="upper left")
        a1.grid(True, axis="y")

        opm = [y.get("opm") for y in yrs]
        if any(v is not None for v in opm):
            a2.plot(x, [v if v is not None else np.nan for v in opm], color=GREEN, lw=1.6, marker="o", ms=3.5, label="Operating margin %")
        roce = [y.get("roce") for y in yrs]
        if any(v is not None for v in roce):
            a2.plot(x, [v if v is not None else np.nan for v in roce], color=AMBER, lw=1.4, marker="s", ms=3, label="ROCE %")
        a2.set_xticks(x)
        a2.set_xticklabels(labs, fontsize=6.8, rotation=30, ha="right")
        a2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        a2.set_title("Margin & return trend", loc="left")
        if a2.get_legend_handles_labels()[0]:
            a2.legend(frameon=False, fontsize=7.5, loc="best")
        a2.grid(True, axis="y")
        return _save(fig, f"{sym}_fin.png")
    except Exception as e:
        logger.warning(f"[charts] financials failed: {e}")
        plt.close("all")
        return None


def chart_shareholding(sh: List[dict], sym: str) -> Optional[str]:
    try:
        rows = [r for r in sh if r.get("promoters") is not None]
        if len(rows) < 3:
            return None
        labs = [r["label"] for r in rows]
        x = np.arange(len(rows))
        series = [("Promoters", "promoters", NAVY), ("FII", "fii", ACCENT),
                  ("DII", "dii", GREEN), ("Public", "public", "#B8C0CC")]
        fig, ax = plt.subplots(figsize=(7.4, 2.6))
        bottom = np.zeros(len(rows))
        for nm, key, colr in series:
            vals = np.array([r.get(key) or 0 for r in rows], float)
            ax.bar(x, vals, bottom=bottom, color=colr, width=0.62, label=nm)
            for xi, (v, b) in enumerate(zip(vals, bottom)):
                if v >= 6:
                    ax.text(xi, b + v / 2, f"{v:.1f}", ha="center", va="center", fontsize=6.8,
                            color="white" if nm != "Public" else NAVY)
            bottom += vals
        ax.set_xticks(x)
        ax.set_xticklabels(labs, fontsize=7, rotation=20, ha="right")
        ax.set_ylim(0, 100)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.legend(ncol=4, frameon=False, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, 1.14))
        return _save(fig, f"{sym}_sh.png")
    except Exception as e:
        logger.warning(f"[charts] shareholding failed: {e}")
        plt.close("all")
        return None
