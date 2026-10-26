"""Source-linked quarterly fundamentals chart for the existing MSK v2 PDF.

Plot reported quarters only. No synthetic gaps, projection or live data fetch.
"""
import logging
import math
import os
import tempfile
import uuid

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)


def render_quarterly_chart(quarters, symbol):
    rows = (quarters or [])[-8:]
    if len(rows) < 4 or any(not str(r.get("label") or "").strip() for r in rows):
        return None
    try:
        labels = [str(r["label"]).replace(" 20", " '") for r in rows]
        revenue = np.array([float(r["revenue"]) if r.get("revenue") is not None else np.nan for r in rows])
        profit = np.array([float(r["net_profit"]) if r.get("net_profit") is not None else np.nan for r in rows])
        opm = np.array([float(r["opm"]) if r.get("opm") is not None else np.nan for r in rows])
        if not np.all(np.isfinite(revenue)) or np.any(revenue <= 0):
            return None
        if np.count_nonzero(np.isfinite(profit)) < 4:
            return None
        if np.any((opm[np.isfinite(opm)] < -100) | (opm[np.isfinite(opm)] > 100)):
            return None
        x = np.arange(len(rows))
        fig, (a, b) = plt.subplots(1, 2, figsize=(7.4, 2.9))
        fig.subplots_adjust(left=.08, right=.91, bottom=.26, top=.76, wspace=.34)
        a.bar(x, revenue, color="#0B2545", width=.63)
        a.set_title("Quarterly Revenue - INR Cr", loc="left", fontsize=9, weight="bold")
        a.grid(axis="y", alpha=.2)
        b.bar(x, profit, color="#1E88E5", width=.63)
        b.set_title("Net Profit & Operating Margin", loc="left", fontsize=9, weight="bold")
        b.grid(axis="y", alpha=.2)
        if np.isfinite(opm).sum() >= 4:
            right = b.twinx()
            right.plot(x, opm, marker="o", markersize=3.6, linewidth=1.4, color="#1B8A5A")
            right.set_ylabel("OPM %", fontsize=8, color="#1B8A5A")
            right.tick_params(axis="y", labelsize=7, colors="#1B8A5A")
        for ax in (a, b):
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=7, rotation=30, ha="right")
            ax.tick_params(axis="y", labelsize=7)
        fig.suptitle(f"{symbol} | Reported quarterly results (actual, not forecast)",
                     x=.08, y=.96, ha="left", weight="bold", fontsize=11, color="#0B2545")
        directory = os.path.join(tempfile.gettempdir(), "msk_report_quarterly")
        os.makedirs(directory, exist_ok=True)
        safe = "".join(c for c in symbol if c.isascii() and c.isalnum())[:18] or "STOCK"
        out = os.path.join(directory, f"{safe}_{uuid.uuid4().hex[:10]}.png")
        fig.savefig(out, dpi=175, facecolor="white")
        plt.close(fig)
        return out
    except (ValueError, TypeError, OverflowError) as exc:
        logger.warning("[MSK PDF] quarterly chart omitted due to data quality: %s", exc)
        plt.close("all")
        return None
