"""Shared deterministic, causal pattern mathematics with no drawing heuristics."""
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..config import ChartConfig, DEFAULT_CONFIG


@dataclass(frozen=True)
class Hit:
    name: str
    anchor_index: int
    confidence: float
    direction: str
    trigger_index: int
    breakout: bool = True


@dataclass(frozen=True)
class Context:
    df: pd.DataFrame
    cfg: ChartConfig = DEFAULT_CONFIG

    def array(self, key: str) -> np.ndarray:
        return self.df[key].to_numpy(dtype=float)

    def atr(self, index: int) -> float:
        a = float(self.df["atr"].iloc[index])
        return a if np.isfinite(a) and a > 0 else 0.

    def volume(self, index: int) -> float:
        a = float(self.df["vol_multiple"].iloc[index])
        return a if np.isfinite(a) else 0.

    def confirmed(self, index: int, minimum=None) -> bool:
        return self.volume(index) >= (minimum if minimum is not None else self.cfg.volume_confirmation)


def fit(values):
    """OLS of chronological values, returning slope, predicted line and fit R²."""
    v = np.asarray(values, dtype=float)
    if len(v) < 3 or not np.all(np.isfinite(v)):
        return 0., np.zeros_like(v), 0.
    xs = np.arange(len(v), dtype=float)
    slope, intercept = np.polyfit(xs, v, 1)
    predicted = slope * xs + intercept
    variance = float(np.sum((v - v.mean()) ** 2))
    r2 = 1. - float(np.sum((v - predicted) ** 2)) / variance if variance > 1e-10 else 1.
    return float(slope), predicted, max(0., min(1., r2))


def pivots(values, start: int, stop: int, order: int, kind: str):
    """Complete local extrema only: the current candle cannot validate itself."""
    v = np.asarray(values, dtype=float)
    result = []
    for pos in range(max(start + order, order), min(stop - order, len(v) - order)):
        neighborhood = v[pos-order:pos+order+1]
        x = v[pos]
        if kind == "high" and x == np.max(neighborhood) and v[pos-1] != x:
            result.append(pos)
        elif kind == "low" and x == np.min(neighborhood) and v[pos-1] != x:
            result.append(pos)
    return result


def score(ctx: Context, fit_r2: float, geometry: float, index: int, base=None):
    """0-100 cleanliness: fit consistency, volume evidence and geometric symmetry."""
    c = ctx.cfg
    v = min(1., ctx.volume(index) / c.volume_high)
    raw = (c.confidence_base if base is None else base) + c.confidence_fit_weight * min(1., max(0., fit_r2)) + c.confidence_geometry_weight * min(1., max(0., geometry)) + c.confidence_volume_weight * v
    return round(min(c.confidence_max, max(0., raw)), 1)


def breakout(ctx: Context, i: int, upper: float, direction: str, volume: float = None) -> bool:
    """Breakout must be on the *current* closed bar with confirmed volume."""
    price = float(ctx.df.close.iloc[i])
    threshold = ctx.cfg.breakout_atr_fraction * ctx.atr(i)
    valid_vol = ctx.confirmed(i, minimum=volume)
    if not valid_vol:
        return False
    return price > upper + threshold if direction == "BUY" else price < upper - threshold


def hit(name, anchor, ctx, i, direction, r2, geometry=1., breakout_type=True, base=None) -> Optional[Hit]:
    confidence = score(ctx, r2, geometry, i, base=base)
    return Hit(name, int(anchor), confidence, direction, i, breakout_type) if confidence >= ctx.cfg.min_cleanliness else None
