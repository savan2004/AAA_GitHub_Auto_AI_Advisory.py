"""MSK Trading Chart Engine: render_chart(df) -> (standalone_html, json_payload)."""
from .engine import analyze, validate_data
from .config import ChartConfig, DEFAULT_CONFIG

def render_chart(df, cfg=DEFAULT_CONFIG):
    """Optional interactive HTML backend; PNG consumers need no Plotly."""
    from .renderer import render_chart as _render_chart
    return _render_chart(df, cfg)

__all__ = ["render_chart","analyze","validate_data","ChartConfig","DEFAULT_CONFIG"]
