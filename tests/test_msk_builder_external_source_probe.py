"""No-network Finstack adapter contract and non-publication tests."""
import contextlib
from datetime import date
from decimal import Decimal
import io
import json
import sys
import unittest
from unittest.mock import patch

from msk_builder_external_source_probe import (
    ProbeRejected, quarter_observation, eod_observation, main,
)

Q = date(2026, 9, 30)


class Frame:
    def __init__(self, rows, attrs=None):
        self.rows = rows
        self.attrs = {"source": "nse"} if attrs is None else attrs

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


def quarter_frame(**overrides):
    rec = {
        "period_end": "2026-09-30", "revenue": 210,
        "net_profit": 13, "symbol": "MCX",
        "units": "INR crore (EPS in INR)", "basis": "as filed (XBRL)",
    }
    rec.update(overrides)
    return Frame([rec])


def eod_frame(**overrides):
    rec = {"date": "2026-09-30", "symbol": "MCX", "close": 1234.5}
    rec.update(overrides)
    return Frame([rec])


class SourceProbeContract(unittest.TestCase):
    def test_read_only_quarter_and_unknown_filing_basis(self):
        calls = []
        def fetch(*args, **kwargs):
            calls.append((args, kwargs))
            return quarter_frame()
        result = quarter_observation("MCX", Q, provider=fetch)
        self.assertEqual(calls, [
            (("india_company_financials", "MCX"),
             {"statement": "quarterly", "consolidated": True})
        ])
        self.assertEqual(result["period"], "2026-09-30")
        self.assertEqual(Decimal(result["revenue_cr"]), Decimal(210))
        self.assertEqual(result["reported_basis"], "UNCONFIRMED")
        self.assertEqual(result["source_verification"], "UNVERIFIED_EXTERNAL_CANDIDATE")
        self.assertEqual(result["publication_status"], "WITHHELD")

    def test_exact_source_units_symbol_and_period(self):
        cases = [
            (quarter_frame(period_end="2026-06-30"), "PERIOD_NOT_UNIQUE_OR_MISSING"),
            (quarter_frame(symbol="WRONG"), "ISSUER_IDENTITY_MISMATCH"),
            (quarter_frame(units="USD"), "SOURCE_UNITS_UNCONFIRMED"),
            (quarter_frame(net_profit=float("nan")), "VALUE_INVALID"),
            (quarter_frame(revenue=-3), "VALUE_INVALID"),
            (Frame([quarter_frame().rows[0]] * 2), "PERIOD_NOT_UNIQUE_OR_MISSING"),
            (Frame([{"period_end": "2026-09-30", "revenue": 123}]), "SOURCE_FIELDS_MISSING"),
            (Frame(quarter_frame().rows, attrs={}), "SOURCE_PROVENANCE_MISSING"),
        ]
        for frame, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ProbeRejected, code):
                quarter_observation("MCX", Q, provider=lambda *a, f=frame, **kw: f)

    def test_untrusted_provider_exception_body_not_exposed(self):
        def bad(*args, **kwargs):
            raise RuntimeError("api_key=supersecret")
        with self.assertRaisesRegex(ProbeRejected, "^FINSTACK_FETCH_FAILED$") as caught:
            quarter_observation("MCX", Q, provider=bad)
        self.assertNotIn("supersecret", str(caught.exception))

    def test_read_only_eod_exact_session(self):
        called=[]
        def fetch(*a, **kw):
            called.append((a,kw))
            return eod_frame()
        result=eod_observation("MCX", Q, provider=fetch)
        self.assertEqual(called[0][0], ("india_daily_prices", "MCX"))
        self.assertEqual(called[0][1], {"start":"2026-09-30", "end":"2026-09-30"})
        self.assertEqual(Decimal(result["close_rupees"]),Decimal("1234.5"))
        self.assertEqual(result["publication_status"], "WITHHELD")
        self.assertEqual(result["source_verification"], "UNVERIFIED_EXTERNAL_CANDIDATE")

    def test_eod_never_uses_wrong_day_or_invalid_price(self):
        for frame, error in (
            (eod_frame(date="2026-09-29"), "SESSION_NOT_UNIQUE_OR_MISSING"),
            (eod_frame(close=0), "VALUE_INVALID"),
            (eod_frame(symbol="OTHER"), "ISSUER_IDENTITY_MISMATCH"),
            (Frame(eod_frame().rows*2), "SESSION_NOT_UNIQUE_OR_MISSING"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(ProbeRejected,error):
                eod_observation("MCX", Q, provider=lambda *a,f=frame, **k:f)

    def test_no_third_party_import_at_module_load(self):
        self.assertNotIn("finstack", sys.modules)
        with patch("msk_builder_external_source_probe.importlib.import_module",
                   side_effect=ImportError("not installed")):
            with self.assertRaisesRegex(ProbeRejected, "FINSTACK_NOT_INSTALLED"):
                quarter_observation("MCX", Q)

    def test_source_cli_never_issues_customer_approval(self):
        output = io.StringIO()
        with patch("msk_builder_external_source_probe.importlib.import_module",
                   side_effect=ImportError("bad_secret")), contextlib.redirect_stdout(output):
            code=main(["quarter","MCX","2026-09-30"])
        self.assertEqual(code, 1)
        data=json.loads(output.getvalue())
        self.assertEqual(data["publication_status"], "WITHHELD")
        self.assertEqual(data["status"], "FINSTACK_NOT_INSTALLED")
        self.assertNotIn("bad_secret",output.getvalue())

    def test_invalid_symbol_no_provider_request(self):
        with self.assertRaisesRegex(ProbeRejected, "SYMBOL_INVALID"):
            quarter_observation("MCX;DROP", Q, provider=lambda *a,**k:None)


if __name__ == "__main__":
    unittest.main()
