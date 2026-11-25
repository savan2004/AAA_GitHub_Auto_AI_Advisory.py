"""Shared historical price-structure targets, no network or extra dependency.

Targets are conditional reference scenarios, not price predictions. Pivots
require two completed bars on either side, all at or before the signal.
"""
import math
import numpy as np

TARGET_MODEL = "structure-v1"


def calculate_targets(high, low, i, *, entry, stop, atr, side,
                      first_distance, second_distance):
    """Cap planned targets at observed barriers, using at most 252 prior bars."""
    try:
        entry, stop, atr, d1, d2 = map(float, (entry, stop, atr, first_distance, second_distance))
        if side not in ("LONG", "SHORT"):
            return None
        sign = 1 if side == "LONG" else -1
        risk = (entry - stop) * sign
        if (not all(math.isfinite(x) for x in (entry, stop, atr, d1, d2))
                or min(entry, stop, atr, risk, d1) <= 0 or d2 <= d1
                or i < 20 or i >= len(high) or i >= len(low)):
            return None
        src = high if sign == 1 else low
        # No signal-day or future candle is used as an historical barrier.
        values = np.asarray(src[max(0, i - 252):i], dtype=float) * sign
        if not np.isfinite(values).all() or (values * sign <= 0).any():
            return None
        middle = values[2:-2]
        pivots = middle[(middle >= values[:-4]) & (middle >= values[1:-3])
                        & (middle >= values[3:-1]) & (middle >= values[4:])
                        & ((middle > values[1:-3]) | (middle > values[3:-1]))]
        extremes = [float(values[-n:].max()) for n in (20, 55, 252) if len(values) >= n]
        obstacles = sorted(float(x - sign * entry)
                           for x in np.concatenate((pivots, extremes))
                           if x > sign * entry)
        # Cluster nearby prices, retaining the closest (most conservative) level.
        clusters = []
        for distance in obstacles:
            if not clusters or distance - clusters[-1] > 0.15 * atr:
                clusters.append(distance)
        buffer = 0.10 * atr
        distances, bases = [], []
        rank = 0
        for planned in (d1, d2):
            barrier = clusters[rank] if rank < len(clusters) else None
            if barrier is not None and barrier - buffer <= planned:
                distance, basis = barrier - buffer, "resistance" if sign == 1 else "support"
                rank += 1
            else:
                distance, basis = planned, "projection"
            distances.append(distance)
            bases.append(basis)
        if distances[0] <= 0 or distances[1] <= distances[0]:
            return None
        t1, t2 = (entry + sign * x for x in distances)
        if min(t1, t2) <= 0:
            return None
        return dict(t1=t1, t2=t2, rr1=distances[0]/risk, rr2=distances[1]/risk,
                    target_model=TARGET_MODEL, t1_basis=bases[0], t2_basis=bases[1])
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


def target_quality(row, *, entry_key="entry", min_rr=1.5):
    """Recheck persisted target provenance and arithmetic before publication."""
    try:
        if row.get("target_model") != TARGET_MODEL:
            return False
        side = row.get("side", "LONG")
        if side not in ("LONG", "SHORT"):
            return False
        sign = 1 if side == "LONG" else -1
        allowed = {"projection", "resistance" if sign == 1 else "support"}
        if any(row.get(k) not in allowed for k in ("t1_basis", "t2_basis")):
            return False
        entry, stop, t1, t2 = (float(row[k]) for k in (entry_key, "sl", "t1", "t2"))
        if not all(math.isfinite(x) and x > 0 for x in (entry, stop, t1, t2)):
            return False
        risk = (entry-stop)*sign
        return (risk > 0 and (t1-entry)*sign/risk >= min_rr-1e-9
                and (t2-t1)*sign > 0)
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
