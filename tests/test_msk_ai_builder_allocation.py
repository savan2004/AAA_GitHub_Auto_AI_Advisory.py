import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
from msk_ai_builder_profile import ResearchProfile


SESSION = date(2026, 11, 20)


def candidate(symbol, serial, industry, price, score):
    return ApprovedCandidate(symbol, f"IN{serial:010d}", industry, SESSION,
                             Decimal(price), Decimal(score))


class AllocationTest(unittest.TestCase):
    def test_automatically_ranks_and_sizes_distinct_industries(self):
        profile = ResearchProfile(100000, "medium", 36)
        candidates = [
            candidate("NTPC", 1, "Utilities", "320.50", "92"),
            candidate("POWERGRID", 2, "utilities", "300", "80"),
            candidate("INFY", 3, "Technology", "1588", "90"),
            candidate("SBIN", 4, "Banking", "833", "82"),
        ]
        plan = allocate_portfolio(profile, reversed(candidates), expected_session=SESSION)
        self.assertEqual([row.candidate.symbol for row in plan.positions], ["NTPC", "INFY", "SBIN"])
        self.assertEqual([row.shares for row in plan.positions], [46, 9, 18])
        self.assertEqual(plan.cash, Decimal("55971.00"))

    def test_short_low_risk_and_empty_bank_leave_cash(self):
        row = candidate("INFY", 1, "Technology", "100", "90")
        plan = allocate_portfolio(ResearchProfile(1000, "low", 6), [row], expected_session=SESSION)
        self.assertEqual((plan.positions, plan.cash), ((), Decimal(1000)))
        plan = allocate_portfolio(ResearchProfile(1000, "high", 36), [], expected_session=SESSION)
        self.assertEqual((plan.positions, plan.cash), ((), Decimal(1000)))

    def test_rejects_duplicates_stale_quotes_and_nonfinite_prices(self):
        row = candidate("INFY", 1, "Technology", "100", "90")
        profile = ResearchProfile(1000, "high", 36)
        for rows in ([row, row], [replace(row, close_date=SESSION - timedelta(days=1))],
                     [replace(row, close_rupees=Decimal("NaN"))]):
            with self.assertRaises(ValueError):
                allocate_portfolio(profile, rows, expected_session=SESSION)
