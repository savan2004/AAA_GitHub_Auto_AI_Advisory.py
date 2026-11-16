"""Regression: risky breakout observations must not become actionable picks."""
from pathlib import Path


def test_buzz_risk_gate_before_actionable_update():
    source = (Path(__file__).resolve().parents[1] / "stocks_buzz.py").read_text()
    start = source.index("risk = ltp - sl")
    end = source.index('base.update({\n                "kind": "BREAKOUT"', start)
    gate = source[start:end]
    assert "risk / ltp > 0.08" in gate
    assert "ext_pct > 8" in gate
    assert "rsi > 80" in gate
    assert 'ltp < brk["level"]' in gate
    assert '"kind": "EXTENDED"' in gate
    assert "return base" in gate
