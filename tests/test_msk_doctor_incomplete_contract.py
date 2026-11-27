"""No market fetching, Telegram send or live Doctor change: offline diagnostic gate."""
import unittest

from portfolio_doctor_incomplete import (
    encode_incomplete_diagnostic, decode_incomplete_diagnostic,
    private_incomplete_message,
)


class DoctorIncompleteDiagnosticContract(unittest.TestCase):
    def test_only_prevalidated_symbols_and_failure_codes_can_cross_boundary(self):
        errors = {"SBIN": "NO_PRICES", "BHEL": "STALE_PRICES"}
        raw = encode_incomplete_diagnostic(errors)
        self.assertTrue(raw)
        self.assertEqual(decode_incomplete_diagnostic(
            raw, [{"symbol": "SBIN"}, {"symbol": "BHEL"}]), errors)

    def test_never_leak_unknown_holdings_or_provider_errors(self):
        raw = encode_incomplete_diagnostic({"PRIVATEA": "NO_PRICES"})
        self.assertEqual(decode_incomplete_diagnostic(
            raw, [{"symbol": "OTHER"}]), {})
        self.assertEqual(encode_incomplete_diagnostic(
            {"SBIN": "DB_CONNECTION=secret"}), b"")
        self.assertEqual(encode_incomplete_diagnostic(
            {"<script>": "NO_PRICES"}), b"")
        self.assertEqual(decode_incomplete_diagnostic(
            raw + b"\n" + b"x" * 1024, [{"symbol": "PRIVATEA"}]), {})

    def test_customer_notice_escapes_html_and_disclaims_partial_report(self):
        message = private_incomplete_message({"M&M": "STALE_PRICES"})
        self.assertIn("M&amp;M", message)
        self.assertNotIn("<code>M&M</code>", message)
        self.assertIn("NOT A PORTFOLIO REPORT", message)
        self.assertIn("No partial totals", message)
        self.assertNotIn("₹", message)

    def test_invalid_data_never_becomes_a_customer_message(self):
        self.assertIsNone(private_incomplete_message({"SBIN": "broker secret"}))
        self.assertIsNone(private_incomplete_message({}))
        self.assertEqual(encode_incomplete_diagnostic({}), b"")
        self.assertEqual(decode_incomplete_diagnostic(b'{"kind":"other"}',
            [{"symbol": "SBIN"}]), {})
        self.assertEqual(decode_incomplete_diagnostic(
            encode_incomplete_diagnostic({"SBIN":"NO_PRICES"}),
            [{"symbol":"<script>"}]), {})
        self.assertEqual(decode_incomplete_diagnostic(
            encode_incomplete_diagnostic({"SBIN":"NO_PRICES"}),
            [{"symbol":"SBIN"}, {"symbol":"SBIN"}]), {})


if __name__ == "__main__":
    unittest.main()
