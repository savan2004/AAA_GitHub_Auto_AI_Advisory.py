"""Evidence-based virtual model tests: all fixtures are synthetic, no network calls."""
from datetime import datetime, timezone
import unittest

from model_portfolios import MODEL_CAPITAL_RUPEES, format_model, select_model

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp()


def candidate(index, sector=None, **updates):
    row = {
        "symbol": f"EXAMPLE{index:02d}",
        "name": f"Fixture {index}",
        "sector": sector or f"Sector {index // 2}",
        "asof": "26-Oct-2026",
        "fetched_at": NOW - 500,
        "roe": 22.0,
        "growth": 12.0,
        "debt": 0.25,
        "cap": 50_000,  # rupees crore, normalized research_upgrade unit
        "pe": 22.0,
        "vol": 19.0,  # annualized volatility percent
        "turnover": 25.0,  # rupees crore/day
        "price": 300.0,
        "timing": "Constructive",
    }
    row.update(updates)
    return row


class ConservativeModelTests(unittest.TestCase):
    def test_exact_ten_picks_and_corpus_is_ten_not_hundred_crore(self):
        rows = [candidate(i) for i in range(10)]
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(MODEL_CAPITAL_RUPEES, 100_000_000)
        self.assertEqual(model["status"], "complete")
        self.assertEqual(len(model["holdings"]), 10)
        self.assertEqual(sum(h["allocation_rupees"] for h in model["holdings"]), 80_000_000)
        self.assertEqual(model["cash_rupees"], 20_000_000)
        messages, symbols = format_model(model, {"checked": 10, "universe": 10})
        self.assertEqual(len(symbols), 10)
        self.assertEqual(len(messages), 4)
        self.assertIn("not invested", messages[0])
        self.assertNotIn("Total P&L", " ".join(messages))

    def test_fail_closed_when_one_candidate_missing(self):
        model = select_model([candidate(i) for i in range(9)], "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        self.assertEqual(model["holdings"], [])
        self.assertIsNone(model["cash_rupees"])
        messages, symbols = format_model(model, {"checked": 9, "universe": 9})
        self.assertEqual(symbols, [])
        self.assertIn("No ten-stock model", messages[0])

    def test_old_fundamental_or_historical_price_is_excluded(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["fetched_at"] = NOW - 200_000
        rows[1]["asof"] = "01-Oct-2026"
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        self.assertGreaterEqual(len(model["rejected"]), 2)

    def test_rejects_low_liquidity_nan_and_unverified_finance(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["turnover"] = 4.0
        rows[1]["roe"] = float("nan")
        rows[2]["sector"] = "Banking & Finance"
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        self.assertEqual(model["holdings"], [])

    def test_sector_cap_two_in_a_ten_stock_model(self):
        rows = [candidate(i) for i in range(10)]
        rows[2]["sector"] = rows[0]["sector"]
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        rows.append(candidate(10, sector="Different Sector"))
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "complete")
        sectors = [row["sector"] for row in model["holdings"]]
        self.assertLessEqual(max(sectors.count(s) for s in sectors), 2)

    def test_profile_threshold_not_relaxed(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["roe"] = 7
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        self.assertEqual(model["qualified"], 9)

    def test_no_boolean_or_future_financial_snapshot(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["price"] = True
        rows[1]["fetched_at"] = NOW + 1000
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")

    def test_escapes_external_company_and_sector_names(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["sector"] = "<Unknown & testing>"
        model = select_model(rows, "conservative", now=NOW)
        self.assertEqual(model["status"], "complete")
        messages, _ = format_model(model, {"checked": 10, "universe": 10})
        output = " ".join(messages)
        self.assertNotIn("<Unknown & testing>", output)
        self.assertIn("&lt;Unknown &amp; testing&gt;", output)

    def test_invalid_corpus_is_rejected(self):
        with self.assertRaises(ValueError):
            select_model([], "conservative", capital_rupees=True)
        with self.assertRaises(ValueError):
            select_model([], "conservative", capital_rupees=0)


class ModerateModelTests(unittest.TestCase):
    def test_moderate_ninety_percent_invested_with_ten_percent_cash(self):
        rows = [candidate(i) for i in range(10)]
        rows[0].update(roe=11, growth=3, debt=1.0, cap=8000, pe=55, vol=40)
        conservative = select_model(rows, "conservative", now=NOW)
        self.assertEqual(conservative["status"], "insufficient_verified_candidates")
        moderate = select_model(rows, "moderate", now=NOW)
        self.assertEqual(moderate["status"], "complete")
        self.assertEqual(sum(h["allocation_rupees"] for h in moderate["holdings"]), 90_000_000)
        self.assertEqual(moderate["cash_rupees"], 10_000_000)

    def test_moderate_never_relaxes_roe_or_sector_gates(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["roe"] = 8
        model = select_model(rows, "moderate", now=NOW)
        self.assertEqual(model["status"], "insufficient_verified_candidates")
        self.assertEqual(model["holdings"], [])


if __name__ == "__main__":
    unittest.main()
