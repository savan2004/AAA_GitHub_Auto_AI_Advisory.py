"""Chart-first target and stop calculation. No fixed reward multiple or ATR cap.

Only completed OHLC history through the signal is allowed. Levels are observed
confirmed pivots, clustered into price zones. ATR is a buffer, not a target.
"""
import math
import numpy as np

TARGET_MODEL = "structure-v2"


def _zones(values, atr, high):
    """Cluster confirmed five-bar swing points; do not infer unseen levels."""
    z = values if high else -values
    middle = z[2:-2]
    pivots = middle[(middle >= z[:-4]) & (middle >= z[1:-3])
                    & (middle >= z[3:-1]) & (middle >= z[4:])
                    & ((middle > z[1:-3]) | (middle > z[3:-1]))]
    prices = sorted(float(x if high else -x) for x in pivots)
    groups = []
    for price in prices:
        if not groups or price - groups[-1][0] > 0.20 * atr:
            groups.append([price])
        else:
            groups[-1].append(price)
    return [(g[0], g[-1]) for g in groups]


def calculate_targets(high, low, i, *, entry, atr, side):
    """Stop beyond nearest protective zone; targets before opposing zones.

    Return no plan if either stop or first target is unsupported by the chart.
    Second target is optional. A very tight stop (<0.25 ATR) is rejected,
    not artificially moved to manufacture a large reward/risk.
    """
    try:
        entry, atr = float(entry), float(atr)
        if (side not in ("LONG", "SHORT") or not math.isfinite(entry)
                or not math.isfinite(atr) or min(entry, atr) <= 0
                or i < 20 or i >= len(high) or i >= len(low)):
            return None
        h = np.asarray(high[max(0, i-251):i+1], dtype=float)
        l = np.asarray(low[max(0, i-251):i+1], dtype=float)
        if (h.ndim != 1 or l.shape != h.shape or not np.isfinite(h).all()
                or not np.isfinite(l).all() or (l <= 0).any() or (h < l).any()
                or not l[-1] <= entry <= h[-1]):
            return None
        supports = _zones(l, atr, False)
        resistances = _zones(h, atr, True)
        buffer = 0.10 * atr
        if side == "LONG":
            protective = [z for z in supports if z[1] < entry]
            opposing = [z for z in resistances if z[1] > entry]
            opposing.sort(key=lambda z: z[0])
            if not protective:
                return None
            anchor = max(protective, key=lambda z: z[1])[0]
            stop = anchor - buffer
            levels = [z[0] for z in opposing]
            sign = 1
        else:
            protective = [z for z in resistances if z[0] > entry]
            opposing = [z for z in supports if z[0] < entry]
            opposing.sort(key=lambda z: z[1], reverse=True)
            if not protective:
                return None
            anchor = min(protective, key=lambda z: z[0])[1]
            stop = anchor + buffer
            levels = [z[1] for z in opposing]
            sign = -1
        risk = (entry - stop) * sign
        if not levels or stop <= 0 or risk < 0.25 * atr:
            return None
        targets = [level - sign * buffer for level in levels[:2]]
        if min(targets) <= 0 or (targets[0] - entry)*sign <= 0:
            return None
        t1 = targets[0]
        t2 = targets[1] if len(targets) > 1 and (targets[1]-t1)*sign > 0 else None
        basis = "resistance" if sign == 1 else "support"
        return dict(sl=stop, stop_anchor=anchor, stop_basis="support" if sign == 1 else "resistance",
                    t1=t1, t2=t2, t1_anchor=levels[0],
                    t2_anchor=levels[1] if t2 is not None else None,
                    rr1=(t1-entry)*sign/risk, rr2=(t2-entry)*sign/risk if t2 is not None else None,
                    target_model=TARGET_MODEL, t1_basis=basis, t2_basis=basis if t2 is not None else None)
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


def target_quality(row, *, entry_key="entry", min_rr=1.5):
    """Reject legacy or unsupported levels and recalculate actual reward/risk."""
    try:
        if row.get("target_model") != TARGET_MODEL:
            return False
        side = row.get("side", "LONG")
        if side not in ("LONG", "SHORT"):
            return False
        sign = 1 if side == "LONG" else -1
        basis = "resistance" if sign == 1 else "support"
        if row.get("t1_basis") != basis or row.get("stop_basis") == basis:
            return False
        if row.get("stop_basis") != ("support" if sign == 1 else "resistance"):
            return False
        entry, stop, t1, anchor, a1, atr = (float(row[k]) for k in
            (entry_key, "sl", "t1", "stop_anchor", "t1_anchor", "atr"))
        if not all(math.isfinite(x) and x > 0 for x in (entry, stop, t1, anchor, a1, atr)):
            return False
        risk = (entry-stop)*sign
        if (risk < 0.25*atr or (t1-entry)*sign/risk < min_rr-1e-9
                or not math.isclose((anchor-stop)*sign, .10*atr, abs_tol=1e-7)
                or not math.isclose((a1-t1)*sign, .10*atr, abs_tol=1e-7)
                or (entry-anchor)*sign <= 0):
            return False
        if row.get("t2") is None:
            return row.get("t2_basis") is None and row.get("t2_anchor") is None
        t2, a2 = float(row["t2"]), float(row["t2_anchor"])
        return (row.get("t2_basis") == basis and math.isfinite(t2) and math.isfinite(a2)
                and min(t2, a2) > 0 and (t2-t1)*sign > 0
                and math.isclose((a2-t2)*sign, .10*atr, abs_tol=1e-7))
    except (KeyError, ValueError, TypeError, OverflowError, ZeroDivisionError):
        return False
