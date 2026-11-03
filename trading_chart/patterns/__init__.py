"""Nine deterministic pattern detectors; no forced label on random/choppy price."""
from . import (bull_flag, bear_flag, channel_breakout, ascending_triangle,
               descending_triangle, double_bottom, double_top,
               consolidation_breakout, pullback_to_ema)
from .common import Context, Hit

DETECTORS = (
    bull_flag.detect, bear_flag.detect, channel_breakout.detect,
    ascending_triangle.detect, descending_triangle.detect,
    double_bottom.detect, double_top.detect,
    consolidation_breakout.detect, pullback_to_ema.detect,
)


def select_pattern(ctx: Context, candle_index: int):
    """Return max-cleanliness pattern. On ties prioritize breakout patterns.

    All detectors consume data through candle_index, never future bars.
    """
    detected = []
    for detector in DETECTORS:
        pattern = detector(ctx, candle_index)
        if pattern and pattern.confidence >= ctx.cfg.min_cleanliness:
            detected.append(pattern)
    if not detected:
        return None
    return max(detected, key=lambda h: (h.confidence, int(h.breakout), -h.anchor_index, h.name))
