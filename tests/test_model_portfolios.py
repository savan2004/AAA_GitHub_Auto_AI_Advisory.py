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
        "provider": "Synthetic approved research fixture",
        "provider_url": "https://www.nseindia.com/reports/fixture",
        "fiscal_sales_period": "2026-03",
        "fiscal_sales_previous": "2025-03",
        "balance_sheet_period": "2026-09",
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


class AggressiveModelTests(unittest.TestCase):
    def test_high_growth_candidates_distinguish_aggressive(self):
        rows = [candidate(i) for i in range(10)]
        rows[0].update(roe=11, growth=15, debt=1.4, cap=2000, pe=75, vol=60)
        self.assertEqual(select_model(rows, "moderate", now=NOW)["status"],
                         "insufficient_verified_candidates")
        aggressive = select_model(rows, "aggressive", now=NOW)
        self.assertEqual(aggressive["status"], "complete")
        self.assertEqual(sum(h["allocation_rupees"] for h in aggressive["holdings"]), 95_000_000)
        self.assertEqual(aggressive["cash_rupees"], 5_000_000)

    def test_aggressive_still_rejects_loss_of_growth_or_liquidity(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["growth"] = 1
        rows[1]["turnover"] = 8.0
        aggressive = select_model(rows, "aggressive", now=NOW)
        self.assertEqual(aggressive["status"], "insufficient_verified_candidates")
        self.assertEqual(aggressive["holdings"], [])


class TelegramModelIntegrationTests(unittest.TestCase):
    def test_three_profile_requests_use_evidence_engine_not_static_lists(self):
        import ast
        from pathlib import Path
        import model_portfolios
        from unittest.mock import patch

        source = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8")
        tree = ast.parse(source)
        func = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "build_scan")
        code = ast.get_source_segment(source, func)
        self.assertIn("model_portfolios import run_model", code)
        self.assertNotIn("SCREENER_STOCKS", source)
        self.assertIn("messages, symbols = build_scan(prof)", source)

        snap = {"rows": [candidate(i) for i in range(10)],
                "checked": 10, "universe": 10}
        with patch.object(model_portfolios, "get_research_snapshot", return_value=snap):
            with patch.object(model_portfolios.time, "time", return_value=NOW):
                for profile, capital_cash in [
                    ("conservative", "2.00"),
                    ("moderate", "1.00"),
                    ("aggressive", "0.50"),
                ]:
                    messages, symbols = model_portfolios.run_model(profile)
                    self.assertEqual(len(symbols), 10)
                    self.assertEqual(len(messages), 4)
                    self.assertIn("₹10.00 Cr", messages[0])
                    self.assertIn("₹" + capital_cash + " Cr", messages[0])

    def test_malformed_symbol_not_offered_in_callback(self):
        rows = [candidate(i) for i in range(10)]
        rows[0]["symbol"] = "BAD|SHARE"
        self.assertEqual(select_model(rows, "conservative", now=NOW)["holdings"], [])

    def test_empty_snapshot_returns_no_symbols_or_investment_claims(self):
        import model_portfolios
        from unittest.mock import patch
        snap = {"rows": [], "checked": 0, "universe": 200}
        with patch.object(model_portfolios, "get_research_snapshot", return_value=snap):
            messages, symbols = model_portfolios.run_model("moderate")
        self.assertEqual(symbols, [])
        self.assertIn("No ten-stock model", messages[0])
        self.assertNotIn("Cash reserve:", messages[0])


class UserRequestedDeliveryFieldsTests(unittest.TestCase):
    """Verify every approved-model stock has all six requested fields."""

    def test_all_three_profiles_render_ten_complete_six_field_cards(self):
        from model_portfolios import _illustrative_levels

        fixtures = [candidate(i) for i in range(10)]
        expectation = {
            "conservative": ("3–6 months", 8.0, 20_000_000),
            "moderate": ("1–3 months", 9.0, 10_000_000),
            "aggressive": ("2–8 weeks", 9.5, 5_000_000),
        }
        for profile, (horizon, allocation_pct, reserve) in expectation.items():
            with self.subTest(profile=profile):
                result = select_model(fixtures, profile, now=NOW)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(result["holdings"]), 10)
                self.assertEqual(result["cash_rupees"], reserve)
                messages, symbols = format_model(
                    result, {"checked": 10, "universe": 10}
                )
                self.assertEqual(len(symbols), 10)
                self.assertEqual(len(messages), 4)
                rendered = "\n".join(messages[1:3])
                self.assertEqual(rendered.count("Target ₹"), 10)
                self.assertEqual(rendered.count("SL ₹"), 10)
                self.assertEqual(rendered.count("Timeframe "), 10)
                self.assertEqual(rendered.count("Reference close ₹"), 10)
                self.assertEqual(rendered.count("Allocation ₹"), 10)
                for holding in result["holdings"]:
                    self.assertEqual(holding["timeframe"], horizon)
                    self.assertEqual(holding["allocation_rupees"],
                                     MODEL_CAPITAL_RUPEES * allocation_pct / 100)
                    self.assertLess(holding["stop_loss"],
                                    holding["reference_price"])
                    self.assertGreater(holding["target"],
                                       holding["reference_price"])
                    self.assertEqual(holding["reference_price"],
                                     round(holding["price"], 2))
                    self.assertEqual(_illustrative_levels(holding, profile)["target"],
                                     holding["target"])
                self.assertIn("NOT forecasts", messages[0])

    def test_unverified_price_or_volatility_must_not_publish_levels(self):
        for invalid in (0, -1, float("nan"), float("inf")):
            rows = [candidate(i) for i in range(10)]
            rows[0]["vol"] = invalid
            result = select_model(rows, "conservative", now=NOW)
            self.assertEqual(result["holdings"], [])
            self.assertEqual(result["status"], "insufficient_verified_candidates")
            messages, symbols = format_model(result, {"checked": 10, "universe": 10})
            self.assertEqual(symbols, [])
            self.assertNotIn("Target ₹", " ".join(messages))


if __name__ == "__main__":
    unittest.main()
