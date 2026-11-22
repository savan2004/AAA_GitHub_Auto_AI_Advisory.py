"""Synthetic, offline validation only; never real company research."""
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
from msk_ai_builder_automatic import approved_plan, ResearchUnavailable
from msk_ai_builder_optimizer import validated_returns
from msk_ai_builder_profile import ResearchProfile

SESSION = date(2026, 11, 20)


def fixture():
    import numpy as np
    import pandas as pd
    days = pd.bdate_range(end=SESSION, periods=160)
    rng = np.random.default_rng(82)
    names = ["TESTA", "TESTB", "TESTC", "TESTD", "TESTE"]
    history = dict(status="APPROVED_ADJUSTED_DAILY",
                   sessions=[d.date().isoformat() for d in days],
                   returns_by_symbol={n: rng.normal(0, .008 + i*.012, len(days)).tolist()
                                      for i, n in enumerate(names)})
    candidates = [ApprovedCandidate(n, f"IN{i:010d}", f"Sector {i}", SESSION,
                                    Decimal("100"), Decimal(90-i))
                  for i, n in enumerate(names)]
    return candidates, history


class OptimizerTest(unittest.TestCase):
    def test_real_solver_cash_stock_caps_and_lower_variance(self):
        import numpy as np
        candidates, history = fixture()
        profile = ResearchProfile(100000, "medium", 18)
        equal = allocate_portfolio(profile, candidates, expected_session=SESSION)
        plan = allocate_portfolio(profile, candidates, expected_session=SESSION,
                                  risk_history=history)
        self.assertEqual(len(plan.positions), 5)
        self.assertGreaterEqual(plan.cash, Decimal(40000))
        self.assertTrue(all(0 < p.cost <= 15000 for p in plan.positions))
        self.assertEqual(plan.cash + sum(p.cost for p in plan.positions), 100000)
        cov = validated_returns(history, [c.symbol for c in candidates], SESSION).cov().to_numpy()
        cov = .8 * cov + .2 * np.diag(np.diag(cov))
        a = np.array([float(p.cost / 100000) for p in plan.positions])
        b = np.array([float(p.cost / 100000) for p in equal.positions])
        self.assertLess(a @ cov @ a, b @ cov @ b)
        self.assertGreater(plan.positions[0].cost, plan.positions[-1].cost)

    def test_missing_stale_constant_and_bad_observations_rejected(self):
        candidates, history = fixture()
        profile = ResearchProfile(100000, "medium", 18)
        cases = [
            dict(history, status="STAGED"),
            dict(history, sessions=history["sessions"][:-1]),
            dict(history, returns_by_symbol={}),
            dict(history, returns_by_symbol=dict(history["returns_by_symbol"],
                                                 TESTA=[0]*160)),
            dict(history, returns_by_symbol=dict(history["returns_by_symbol"],
                                                 TESTA=[float("nan")]*160)),
        ]
        for bad in cases:
            with self.assertRaises(ValueError):
                allocate_portfolio(profile, candidates, expected_session=SESSION, risk_history=bad)

    def test_optimizer_failure_never_silently_falls_back(self):
        candidates, history = fixture()
        with patch("msk_ai_builder_optimizer.minimum_variance_amounts", side_effect=RuntimeError):
            with self.assertRaises(ValueError):
                allocate_portfolio(ResearchProfile(100000, "medium", 18), candidates,
                                   expected_session=SESSION, risk_history=history)

    def test_automatic_schema_requires_history_and_keeps_old_contract(self):
        from test_msk_ai_builder_automatic import sample
        payload = sample()
        profile = ResearchProfile(100000, "medium", 18)
        self.assertTrue(approved_plan(profile, json.dumps(payload), latest_session=SESSION).positions)
        with self.assertRaises(ResearchUnavailable):
            approved_plan(profile, json.dumps(payload), latest_session=SESSION, optimize=True)
        payload["schema_version"] = 2
        with self.assertRaises(ResearchUnavailable):
            approved_plan(profile, json.dumps(payload), latest_session=SESSION, optimize=True)
        _, history = fixture()
        history["returns_by_symbol"] = dict(zip(
            [row["symbol"] for row in payload["companies"]],
            list(history["returns_by_symbol"].values())[:3]))
        payload["risk_history"] = history
        self.assertTrue(approved_plan(profile, json.dumps(payload),
                                      latest_session=SESSION, optimize=True).positions)
