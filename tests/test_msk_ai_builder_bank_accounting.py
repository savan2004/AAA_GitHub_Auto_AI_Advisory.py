"""Strict NSE BANKING and excise comparability contracts; NOT approval evidence.

Synthetic HTML mimics source-observed row names and source arithmetic, but
contains fake company identity. Real-company reconciliation remains the
separately gated weekly public-source rehearsal.
"""
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from msk_ai_builder_evidence import EvidenceError, normalize_secondary
from msk_ai_builder_filings import DownloadedFiling, check_url, extract_quarter
from msk_ai_builder_quarter_check import check_latest_quarter
from test_msk_ai_builder_filings import MEMBER, END, NOW, URL, fixture
from test_msk_ai_builder_evidence import fixture as secondary_fixture

BANK_URL = URL.replace("INTEGRATED_FILING_INDAS_", "INTEGRATED_FILING_BANKING_")
BANK_PROFIT = "Net Profit (loss) after taxes minority interest and share of profit (loss) of associates"


def table(rows):
    return "<table>" + "".join(
        "<tr>" + "".join("<td>{}</td>".format(cell) for cell in row) + "</tr>"
        for row in rows) + "</table>"


def banking_fixture(interest=("55,99,191.00","1,08,23,276.00"),
                    other=("32,16,927.00","59,61,764.00"),
                    income=("88,16,118.00","1,67,85,040.00"),
                    gross=("17,31,187.00","33,52,161.00"),
                    associate=("7,699.00","14,347.00"),
                    minority=("1,05,568.00","1,89,184.00"),
                    attributable=("16,33,318.00","31,77,324.00")):
    def financial_row(name, values, prefix=""):
        return (prefix, name) + tuple(values)
    head = table([
        ("NSE Symbol", MEMBER.symbol), ("ISIN", MEMBER.isin),
        ("Name of company", "Fictional Bank"),
        ("Description of presentation currency", "INR"),
        ("Nature of report standalone or consolidated", "Consolidated"),
        ("Reporting Type", "Quarterly"),
        ("Level of rounding used in financial results", "Lakhs"),
        ("Date of board meeting when results were approved", "12-11-2026"),
    ])
    income_table = table([
        ("Particulars","3 months/ 6 months ended (dd-mm-yyyy)",
         "Year to date figures for current period ended (dd-mm-yyyy)"),
        ("A","Date of start of reporting period","01-07-2026","01-04-2026"),
        ("B","Date of end of reporting period","30-09-2026","30-09-2026"),
        ("C","Whether results are audited or unaudited","Unaudited","Unaudited"),
        ("D","Nature of report standalone or consolidated","Consolidated","Consolidated"),
        financial_row("Total interest earned", interest),
        financial_row("Other income", other, "2"),
        financial_row("Total income", income, "3"),
        financial_row("Net profit (loss) for the period", gross, "14"),
        financial_row("Share of profit (loss) of associates", associate, "15"),
        financial_row("Profit (loss) of minority interest", minority, "16"),
        financial_row(BANK_PROFIT, attributable, "17"),
    ])
    return (head + income_table).encode()


def excise_fixture(*, duty="2,26,000.00", ordinary="3,00,000.00",
                   subtotal="5,26,000.00"):
    text = fixture().decode("utf-8")
    text = text.replace("1,23,456.78", "10,00,000.00")
    text = text.replace("999999.99", "25,00,000.00")
    heading = '<tr><td>16</td><td>Total profit (loss) for period</td>'
    extra = "".join(
        "<tr><td>1</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(*row)
        for row in (
          ("Excise Duty", duty, "4,64,200.00"),
          ("Other Expenses", ordinary, "6,00,000.00"),
          ("Total other expenses", subtotal, "10,64,200.00"),
        ))
    assert heading in text
    return text.replace(heading, extra + heading).encode()


class BankFilingAccountingTests(unittest.TestCase):
    def extract_bank(self, body=None):
        return extract_quarter(
            DownloadedFiling(BANK_URL, body if body is not None else banking_fixture(), NOW),
            MEMBER, expected_end=END, now=NOW, scope="consolidated")

    def test_icici_observed_bank_labels_and_full_arithmetic(self):
        self.assertEqual(check_url(BANK_URL), BANK_URL)
        record = self.extract_bank()
        self.assertEqual(record.revenue_cr, Decimal("55991.91"))
        self.assertEqual(record.profit_cr, Decimal("17388.86"))
        self.assertEqual(record.revenue_definition, "Total interest earned")
        self.assertIn("before minority", record.profit_definition)
        self.assertIsNone(record.excise_cr)

    def test_federal_observed_bank_amounts_and_reconciled_associates(self):
        record = self.extract_bank(banking_fixture(
            interest=("8,38,284.00", "16,24,442.00"),
            other=("1,52,057.00", "2,60,210.00"),
            income=("9,90,341.00", "18,84,652.00"),
            gross=("1,56,406.00","2,86,167.00"),
            associate=("415.00", "903.00"),
            minority=("6,956.00", "11,596.00"),
            attributable=("1,49,865.00","2,75,474.00")))
        self.assertEqual(record.revenue_cr, Decimal("8382.84"))
        self.assertEqual(record.profit_cr, Decimal("1568.21"))

    def test_malformed_bank_source_is_never_selected(self):
        clean = banking_fixture()
        for source, altered, code in [
            (b"55,99,191.00", b"54,99,191.00", "BANK_INCOME_RECONCILIATION"),
            (b"31,77,324.00", b"32,77,324.00", "BANK_PROFIT_RECONCILIATION"),
            (b"Total interest earned", b"Interest earned", "FILING_FINANCIAL_LAYOUT"),
            (b"Net profit (loss) for the period", b"Total profit (loss) for period",
             "FILING_FINANCIAL_LAYOUT"),
            (b"Consolidated", b"Standalone", "SCOPE_OR_CURRENCY"),
        ]:
            with self.subTest(code=code, source=source):
                with self.assertRaisesRegex(EvidenceError, code):
                    self.extract_bank(clean.replace(source, altered))
        with self.assertRaises(EvidenceError):
            check_url(BANK_URL.replace("BANKING", "BANKING_FAKE"))

    def test_bank_reconciles_correct_vendor_fields_and_never_publishes(self):
        data = secondary_fixture()
        data["_statement_scope"] = "consolidated"
        data["_source_url"] = "https://www.screener.in/company/FIXTURE/consolidated/"
        data["quarters"][-1].update(revenue=55992, net_profit=17389)
        snapshot = normalize_secondary("FIXTURE", data, now=NOW)
        listing = Mock(return_value={"totalCount":1,"data":[{
            "symbol":"FIXTURE","qe_Date":"30-SEP-2026",
            "consolidated":"Consolidated",
            "broadcast_Date":"12-Nov-2026 17:22:00",
            "revised_Date":None, "ixbrl":BANK_URL}]})
        download = Mock(return_value=DownloadedFiling(BANK_URL,banking_fixture(),NOW))
        evidence = check_latest_quarter(MEMBER,snapshot,listing=listing,download=download,
                                        clock=lambda:NOW)
        self.assertEqual(evidence["status"], "LATEST_QUARTER_MATCHED_ONLY")
        self.assertEqual(evidence["publication_status"], "WITHHELD")
        self.assertEqual(evidence["issuer_revenue_cr"], "55991.91")
        self.assertEqual(evidence["issuer_profit_cr"], "17388.86")
        self.assertEqual(evidence["revenue_comparison_basis"],
                         "BANK_TOTAL_INTEREST_EARNED")

    def test_excise_filing_is_reconciled_but_never_overwrites_issuer_revenue(self):
        doc = DownloadedFiling(URL, excise_fixture(), NOW)
        record = extract_quarter(doc, MEMBER,expected_end=END,now=NOW)
        self.assertEqual(record.revenue_cr, Decimal("10000"))
        self.assertEqual(record.excise_cr, Decimal("2260"))
        self.assertEqual(record.profit_cr, Decimal("-23.4567"))
        data = secondary_fixture()
        data["_statement_scope"] = "standalone"
        data["_source_url"] = "https://www.screener.in/company/FIXTURE/"
        data["quarters"][-1].update(revenue=7740, net_profit=-23)
        snapshot=normalize_secondary("FIXTURE",data,now=NOW)
        listing=Mock(return_value={"totalCount":1,"data":[{
            "symbol":"FIXTURE","qe_Date":"30-SEP-2026",
            "consolidated":"Standalone","broadcast_Date":"12-Nov-2026 17:22:00",
            "revised_Date":None,"ixbrl":URL}]})
        download=Mock(return_value=doc)
        result=check_latest_quarter(MEMBER,snapshot,listing=listing,
                                    download=download,clock=lambda:NOW)
        self.assertEqual(result["issuer_revenue_cr"], "10000")
        self.assertEqual(result["secondary_revenue_cr"], "7740")
        self.assertEqual(result["reconciled_comparable_revenue_cr"], "7740")
        self.assertEqual(result["disclosed_excise_cr"], "2260")
        self.assertEqual(result["revenue_comparison_basis"],
                         "NET_OF_DISCLOSED_ISSUER_EXCISE")
        self.assertEqual(result["publication_status"], "WITHHELD")
        changed=replace(snapshot,quarterly=snapshot.quarterly[:-1]+(
            replace(snapshot.quarterly[-1],revenue_cr=Decimal("7790")),))
        with self.assertRaisesRegex(EvidenceError,"VALUE_MISMATCH"):
            check_latest_quarter(MEMBER,changed,listing=listing,
                                 download=download,clock=lambda:NOW)

    def test_excise_requires_both_columns_and_exact_source_subtotals(self):
        for kw in ({"duty":"2,36,000.00"},
                   {"ordinary":"3,10,000.00"},
                   {"subtotal":"5,36,000.00"}):
            with self.subTest(kw=kw), self.assertRaisesRegex(
                    EvidenceError,"EXCISE_EXPENSE_RECONCILIATION"):
                extract_quarter(DownloadedFiling(URL,excise_fixture(**kw),NOW),
                                MEMBER,expected_end=END,now=NOW)
        absent=extract_quarter(DownloadedFiling(URL,
                excise_fixture().replace(b"Excise Duty",b"Fuel Expense"),NOW),
                MEMBER,expected_end=END,now=NOW)
        # Missing excise must never authorize an unsupported net-revenue offset.
        self.assertIsNone(absent.excise_cr)


if __name__=="__main__":
    unittest.main()
