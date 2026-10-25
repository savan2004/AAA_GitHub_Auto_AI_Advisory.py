"""Offline adversarial tests of deterministic Indian stock fundamental audit."""
import math
import unittest
from datetime import datetime, timezone
from fundamental_audit import _symbol, audit_metrics, format_audit, audit_stock


class FundamentalAuditTests(unittest.TestCase):
    def test_zero_debt_is_valid(self):
        result = audit_metrics({"de": 0, "eps": 0, "roe": 0, "pe": None})
        self.assertEqual(result["metrics"]["de"], 0)
        self.assertEqual(result["metrics"]["eps"], 0)
        self.assertIn("Debt/Equity", [x for x in
                      ["Debt/Equity"] if "de" in result["metrics"]])

    def test_negative_eps_is_not_discarded(self):
        result = audit_metrics({"eps": -12.5, "roe": -20})
        self.assertEqual(result["metrics"]["eps"], -12.5)
        self.assertTrue(any("Negative EPS" in n for n in result["notes"]))

    def test_nonfinite_and_boolean_values_rejected(self):
        a = audit_metrics({"pe": math.nan, "roe": math.inf,
                           "eps": True, "de": "0.55", "mcap": -4})
        self.assertEqual(a["metrics"], {})
        self.assertEqual(len(a["warnings"]), 5)

    def test_zero_pe_is_not_valid_price_multiple(self):
        a = audit_metrics({"pe": 0, "pb": 0})
        self.assertNotIn("pe", a["metrics"])
        self.assertNotIn("pb", a["metrics"])

    def test_mixed_period_pe_eps_conflict(self):
        a = audit_metrics({"pe": 35, "eps": -2})
        self.assertTrue(any("reporting periods may differ" in x
                            for x in a["warnings"]))

    def test_debt_equity_ambiguous_units(self):
        a = audit_metrics({"de": 82.2})
        self.assertTrue(any("unit ambiguous" in x for x in a["warnings"]))
        self.assertNotIn("cheap", str(a).lower())

    def test_extreme_roe_warns_without_false_buy_signal(self):
        a = audit_metrics({"roe": 285})
        self.assertTrue(any("Extreme ROE" in x for x in a["warnings"]))
        self.assertNotIn("buy", str(a).lower())

    def test_invalid_symbol_blocks_fetch(self):
        def forbidden(*args):
            raise AssertionError("should not fetch invalid ticker")
        self.assertIn("Invalid", audit_stock("../../secrets", fetcher=forbidden))
        self.assertIsNone(_symbol("INFY/?q=1"))

    def test_symbol_canonicalized(self):
        seen = []
        def fetch(ticker):
            seen.append(ticker)
            return {"pe": 12.5}
        audit_stock(" infy ", fetcher=fetch)
        self.assertEqual(seen, ["INFY"])

    def test_data_source_is_not_mislabeled_as_verified(self):
        report = format_audit("INFY", {"pe": 18, "eps": 30},
                              retrieved_at=datetime(2026, 10, 25, tzinfo=timezone.utc))
        self.assertIn("UNVERIFIED", report)
        self.assertIn("NOT the quote/filing timestamp", report)
        self.assertIn("not an investment score", report)
        self.assertNotIn("LIVE PRICE", report)

    def test_missing_data_explicit(self):
        report = format_audit("INFY", {})
        self.assertIn("No usable fundamental figures", report)
        self.assertIn("Missing:", report)

    def test_crore_units_correct(self):
        report = format_audit("INFY", {"mcap": 20_000_000, "rev": 40_000_000})
        self.assertIn("Market Cap: ₹2.00 Cr", report)
        self.assertIn("Revenue: ₹4.00 Cr", report)

    def test_provider_timeout_graceful(self):
        def broken(_):
            raise TimeoutError("private credentials should never be echoed")
        report = audit_stock("INFY", fetcher=broken)
        self.assertIn("providers unavailable", report)
        self.assertNotIn("credentials", report)

    def test_source_name_cannot_inject_newline(self):
        report = format_audit("INFY", {"name": "INFY\nVERIFIED", "pe": 30})
        self.assertNotIn("\nVERIFIED", report)

    def test_missing_metrics_are_not_fabricated(self):
        audit = audit_metrics({"pe": 23})
        self.assertEqual(audit["coverage_count"], 1)
        self.assertEqual(audit["coverage_total"], 7)
        self.assertNotIn("roe", audit["metrics"])

    def test_report_bounded_for_extreme_input(self):
        audit = format_audit("INFY", {"name": "X" * 200,
            "pe": 250, "pb": 250, "roe": 250, "eps": -2,
            "de": 99, "mcap": 1000, "rev": 200000})
        self.assertLessEqual(len(audit), 3800)

    def test_validation_does_not_alter_source_snapshot(self):
        source = {"pe": float("nan"), "de": 0, "eps": -10}
        audit_metrics(source)
        self.assertEqual(source["de"], 0)
        self.assertEqual(source["eps"], -10)


if __name__ == "__main__":
    unittest.main()
