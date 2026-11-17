"""Synthetic regression cases; no customer data or Telegram sends."""
import io
import unittest
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace
import sys

import openpyxl
import portfolio_doctor_live as doctor


def xlsx(rows):
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


class DeepRepairTests(unittest.TestCase):
    def test_wilder_rsi14_published_arithmetic_example(self):
        closes = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,
                  45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        self.assertAlmostEqual(doctor._rsi(closes[:15]), 70.4641350211, places=8)
        self.assertAlmostEqual(doctor._rsi(closes), 66.2496185536, places=8)
        self.assertIsNone(doctor._rsi(closes[:14]))
        self.assertEqual(doctor._rsi([100] * 30), 50)
        self.assertEqual(doctor._rsi(list(range(1,31))), 100)
        self.assertEqual(doctor._rsi(list(range(30,0,-1))), 0)

    def test_xlsx_extra_holding_below_old_row_limit_is_rejected(self):
        rows = [["Symbol","Qty","Buy Price"]]
        rows += [["SAMPLE" + str(i), 2, 100] for i in range(5)]
        rows += [[None,None,None]] * 5
        rows += [["EXTRA",1,100]]
        with self.assertRaises(doctor.PortfolioDoctorError) as error:
            doctor.parse_holdings("private.xlsx", xlsx(rows))
        self.assertEqual(error.exception.code, "BAD_INPUT")

    def test_xlsx_five_holdings_with_blank_lines_are_all_retained(self):
        rows = [["Symbol","Qty","Buy Price"]]
        for i in range(5):
            rows += [[None,None,None], ["SAMPLE" + str(i), i+1, 100]]
        holdings = doctor.parse_holdings("private.xlsx", xlsx(rows))
        self.assertEqual(len(holdings), 5)
        self.assertEqual(sum(h["qty"] for h in holdings), 15)

    def test_xlsx_undeclared_dimensions_do_not_hide_rows(self):
        from zipfile import ZipFile, ZIP_DEFLATED
        import re
        original = xlsx([["SAMPLE" + str(i),1,100] for i in range(6)])
        output = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(output,"w",ZIP_DEFLATED) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:C1"', data)
                target.writestr(name,data)
        with self.assertRaises(doctor.PortfolioDoctorError):
            doctor.parse_holdings("private.xlsx",output.getvalue())

    def test_csv_and_typed_five_holdings_preserve_quantity_and_cost(self):
        for delimiter in (" ", ",", ";", "\t"):
            payload = "\n".join(delimiter.join([f"SAMPLE{i}",str(i+1),"100"])
                                for i in range(5)).encode()
            holdings = doctor.parse_holdings("private.csv",payload)
            self.assertEqual(len(holdings),5)
            self.assertEqual(sum(h["qty"] * h["buy"] for h in holdings),1500)

    def test_shared_stooq_fallback_is_disabled_for_doctor(self):
        calls = {}
        def bulk(*args, **kwargs):
            calls.update(kwargs)
            return {}
        fake = SimpleNamespace(bulk_history=bulk)
        with patch.dict(sys.modules,{"market_data":fake}), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy",return_value={}), \
             patch.object(doctor,"recover_fresh_daily_for_doctor",return_value=None):
            with self.assertRaises(doctor.PortfolioDoctorError):
                doctor.price_holdings([{"symbol":"SAMPLEA","qty":1,"buy":100}])
        self.assertEqual(calls["max_fallbacks"],0)


if __name__ == "__main__":
    unittest.main()
