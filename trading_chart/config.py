"""Single home for chart/indicator/pattern/signal parameters."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChartConfig:
    min_bars: int = 50
    ema_fast: int = 20
    ema_slow: int = 50
    adx_period: int = 14
    atr_period: int = 14
    volume_period: int = 20
    channel_bars: int = 45
    visible_bars: int = 115
    pattern_scan_bars: int = 8
    recent_ema_cross_bars: int = 10
    swing_order: int = 2
    sideways_ema_slope_bars: int = 10
    sideways_ema_slope_fraction: float = .004
    adx_weak_below: float = 20.
    adx_building_to: float = 25.
    adx_strong_to: float = 35.
    volume_above_normal: float = 1.5
    volume_high: float = 2.
    volume_explosive: float = 3.
    min_cleanliness: float = 60.
    high_cleanliness: float = 75.
    slope_fit_min_r2: float = .55
    pole_bars: int = 9
    flag_bars: int = 9
    flag_pole_min_move: float = .075
    flag_max_retracement: float = .55
    channel_pattern_bars: int = 22
    triangle_bars: int = 28
    double_pattern_bars: int = 38
    double_tolerance_fraction: float = .01
    double_min_separation: int = 5
    min_pivot_touches: int = 2
    triangle_touch_atr: float = .48
    consolidation_bars: int = 12
    consolidation_atr_fraction: float = .5
    breakout_atr_fraction: float = .1
    channel_parallel_tolerance: float = .30
    channel_band_stdev: float = 1.6
    volume_confirmation: float = 1.5
    channel_breakout_volume: float = 2.
    pullback_lookback: int = 5
    confidence_max: float = 99.
    confidence_base: float = 55.
    confidence_fit_weight: float = 24.
    confidence_volume_weight: float = 9.
    confidence_geometry_weight: float = 13.
    triangle_flat_atr: float = .42
    channel_touch_atr: float = .50
    max_pattern_age_bars: int = 5
    # Pattern/volume geometry and anti-chop parameters (one source of truth)
    trend_swing_lookback: int = 75
    channel_min_width_atr: float = 1.
    channel_touch_width_fraction: float = .1
    triangle_min_swing_height_atr: float = .3
    double_neckline_min_atr: float = 1.1
    double_geometry_scale_atr: float = 4.
    consolidation_history_bars: int = 30
    consolidation_max_range_atr: float = 2.5
    consolidation_score_base: float = 63.
    pullback_touch_atr: float = .25
    pullback_gap_fit_atr: float = 2.
    pullback_score_base: float = 61.
    turning_lookback: int = 3
    turning_ema_tolerance: float = .003
    channel_atr_floor: float = .5
    chart_title: str = "MSK Trading Chart | Research Signal"
    plots_template: str = "plotly_dark"


DEFAULT_CONFIG = ChartConfig()
