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

def test_buzz_targets_have_minimum_three_to_one_reward_risk():
    source = (Path(__file__).resolve().parents[1] / "stocks_buzz.py").read_text()
    assert '"t1": ltp + 3.0 * risk, "t2": ltp + 4.0 * risk' in source
    assert '"t1": trigger + 3.0 * risk, "t2": trigger + 4.0 * risk' in source
    assert '"t1": ltp + 1.5 * risk' not in source
    assert '"t1": trigger + 1.5 * risk' not in source
