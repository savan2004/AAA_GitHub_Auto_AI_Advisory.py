"""Customer-ready ten-stock CSV flow and exact-page Doctor PDF regression.

All stock names and price series below are synthetic offline fixtures.
Never issue investment recommendations or send Telegram from these tests.
"""
import ast
import copy
import io
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from pypdf import PdfReader

from portfolio_doctor_input import (
    MAX_STOCKS, InputRejected, blank_doctor_csv_template, decode_holdings,
)
from portfolio_doctor_live import IST
from portfolio_doctor_company_research import prepare_company_pages
from portfolio_doctor_report import build_pdf_bytes
from portfolio_doctor_risk_history import calculate_historical_risk


NOW = datetime(2026, 11, 23, 14, 45, tzinfo=IST)
ASOF = "2026-11-20"


def candles(offset):
    dates = pd.bdate_range(end=ASOF, periods=170)
    close = [100 + offset * 24 + i * .11 + (i % 7) * .31 for i in range(170)]
    return pd.DataFrame({
        "Close": close,
        "Open": [x * .998 for x in close],
        "High": [x * 1.01 for x in close],
        "Low": [x * .99 for x in close],
        "Volume": [100000 + i * 30 for i in range(170)],
    }, index=dates)


def stock_record(symbol, frame):
    close = float(frame["Close"].iloc[-1])
    return {
        "symbol": symbol, "qty": 1, "buy": 95, "close": close,
        "date": ASOF, "ema20": close - 4, "ema50": close - 8,
        "rsi14": 57, "high20": close + 3, "low20": close - 15,
        "return20": 2.2,
    }


def texts(pdf):
    return [p.extract_text() or "" for p in
            PdfReader(io.BytesIO(pdf), strict=True).pages]


def handler(name, environment):
    source = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[node], type_ignores=[])), str(source), "exec"), environment)
    return environment[name]


class TenStocksAcceptance(unittest.TestCase):
    def test_allocation_appendix_is_bound_to_holdings_and_profile(self):
        import portfolio_doctor_live as doctor
        symbols = [f"SAMPLE{i:02}" for i in range(10)]
        frames = {s: candles(i) for i, s in enumerate(symbols)}
        rows = [stock_record(s, frames[s]) for s in symbols]
        rows[-1]["exchange"] = "BSE"
        peers = dict(roster_verified=True, roster_observed="2026-11-23",
                     roster_url="official roster fixture", doctor_preferences=("medium", 36),
                     held_industries={s: "Capital Goods" for s in symbols[:-1]})
        report = build_pdf_bytes(rows, peers=peers,
                                 companies=prepare_company_pages(rows, frames, now=NOW),
                                 risk=calculate_historical_risk(rows, frames, now=NOW))
        pages = texts(report)
        self.assertEqual(len(pages), 17)
        self.assertIn("Industrials | current", pages[-1])
        self.assertIn("Unknown / unclassified | current", pages[-1])
        from portfolio_doctor_report import _report_records
        from decimal import Decimal
        normalized = _report_records(rows)
        current_total = sum((r["value"] for r in normalized), Decimal(0))
        self.assertIn(f"Current total: Rs {current_total:,.2f} (100.00%).", pages[-1])
        self.assertIn(f"(Rs {normalized[-1]['value']:,.2f})", pages[-1])
        self.assertIn("Unavailable: no approved research", pages[-1])
        self.assertNotIn("ILLUSTRATIVE REBALANCING", "".join(pages))
        data = ("\n".join([s+",1,95" for s in symbols[:-1]]+["BSE:"+symbols[-1]+",1,95"])).encode()
        child = SimpleNamespace(returncode=0, stdout=report, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=child):
            self.assertEqual(doctor.run_isolated_memory("p.csv", data, preferences=("medium",36)), report)
            for payload, profile in ((data, ("low",6)), (data.replace(b",1,", b",2,"), ("medium",36))):
                with self.assertRaises(doctor.PortfolioDoctorError):
                    doctor.run_isolated_memory("p.csv", payload, preferences=profile)

    def test_model_arithmetic_includes_paise_and_keeps_exchanges_distinct(self):
        from datetime import date
        from decimal import Decimal as D
        from portfolio_doctor_allocation import allocation_review
        from portfolio_doctor_report import _report_records
        from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
        rec = stock_record("SAMPLE01", candles(0))
        rec.update(exchange="BSE", qty=1000, close=100.25)
        other = stock_record("SAMPLE02", candles(1))
        other.update(qty=1, close=50.47)
        rows = _report_records([rec, other])
        session = date.fromisoformat(ASOF)
        def offline_approved(profile, raw, *, latest_session):
            candidate = ApprovedCandidate("SAMPLE01", "IN0000000001", "Capital Goods",
                                          latest_session, D("100"), D("85"))
            return allocate_portfolio(profile, [candidate], expected_session=session), {}
        with patch("msk_ai_builder_automatic.automatic_enabled", return_value=True), \
             patch("portfolio_doctor_live.expected_doctor_session", return_value=session), \
             patch("msk_ai_builder_automatic.approved_plan", side_effect=offline_approved):
            review = allocation_review(rows, {}, ("medium", 36))
            model_pdf = build_pdf_bytes([rec, other], peers={"doctor_preferences": ("medium", 36)})
        model_pages = texts(model_pdf)
        self.assertIn("Modeled cash", model_pages[-2])
        self.assertIn("Current total: Rs 100,300.47 (100.00%).", model_pages[-2])
        self.assertIn("proposed", model_pages[-2])
        self.assertIn("(Rs 15,000.00)", model_pages[-2])
        self.assertIn("ILLUSTRATIVE REBALANCING", model_pages[-1])
        self.assertIn("NSE:SAMPLE01", model_pages[-1])
        self.assertIn("BSE:SAMPLE01", model_pages[-1])
        import portfolio_doctor_live as doctor
        child = SimpleNamespace(returncode=0, stdout=model_pdf, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=child):
            self.assertEqual(doctor.run_isolated_memory("test.csv", b"BSE:SAMPLE01,1000,95\nSAMPLE02,1,95",
                             preferences=("medium", 36)), model_pdf)
        self.assertIn(("BSE","SAMPLE01"), review["stocks"])
        self.assertIn(("NSE","SAMPLE01"), review["proposed"])
        self.assertEqual(review["total"], D("100300.47"))
        self.assertEqual(sum(review["targets"].values())+review["cash"], review["total"])
        self.assertEqual(review["cash"] % 1, D(".47"))

    def test_blank_csv_editable_template_and_ten_positions(self):
        self.assertEqual(MAX_STOCKS, 10)
        template = blank_doctor_csv_template()
        self.assertEqual(template.count(b",,"), 10)
        self.assertTrue(template.startswith(b"Symbol,Qty,Buy Price\r\n"))
        # All blank slots get omitted; this is a fillable ten-row document.
        with self.assertRaises(InputRejected):
            decode_holdings("sample.csv", template)
        rows = ["SAMPLE{:02d},{},{}".format(i, i + 1, 100 + i)
                for i in range(10)]
        payload = ("Symbol,Qty,Buy Price\r\n" +
                   "\r\n".join(rows) + "\r\n").encode()
        actual = decode_holdings("holdings.csv", payload)
        self.assertEqual(len(actual), 10)
        self.assertEqual(actual[-1]["symbol"], "SAMPLE09")
        self.assertEqual(actual[-1]["qty"], 10)
        self.assertEqual(actual[0]["buy"], 100)
        # The template has optional blank slots; 3 holdings still work.
        partial = ("Symbol,Qty,Buy Price\n" + "\n".join(rows[:3]) +
                   "\n" + ",,\n" * 7).encode()
        self.assertEqual(len(decode_holdings("partial.csv", partial)), 3)

    def test_eleventh_row_rejected_and_duplicate_alias_rejected(self):
        items = ["SAMPLE{:02d},1,100".format(i) for i in range(11)]
        with self.assertRaises(InputRejected):
            decode_holdings("list.csv", ("Symbol,Qty,Buy Price\n" +
                            "\n".join(items)).encode())
        with self.assertRaises(InputRejected):
            decode_holdings("list.csv", b"SBI,1,100\nSBIN,1,100")

    def test_ten_holdings_verified_risk_and_sixteen_page_pdf(self):
        names = ["SAMPLE{:02d}".format(i) for i in range(10)]
        frames = {symbol: candles(i) for i, symbol in enumerate(names)}
        records = [stock_record(sym, frames[sym]) for sym in names]
        companies = prepare_company_pages(records, frames, now=NOW)
        self.assertEqual(len(companies), 10)
        self.assertTrue(all(row["status"] == "VERIFIED" for row in companies))
        risk = calculate_historical_risk(records, frames, now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        self.assertEqual(risk["included"], 10)
        report = build_pdf_bytes(records, companies=companies, risk=risk)
        pages = texts(report)
        # Regression: the live parent, not only the renderer, must accept
        # the exact 16-page output for a ten-position request.
        import portfolio_doctor_live as doctor
        payload = ("Symbol,Qty,Buy Price\n" +
                   "\n".join(sym + ",1,95" for sym in names)).encode()
        child = SimpleNamespace(returncode=0, stdout=report, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=child), \
             patch.dict(doctor.os.environ,
                        {"MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED": "0"}):
            self.assertEqual(doctor.run_isolated_memory("holdings.csv", payload),
                             report)
            wrong = payload.replace(b"SAMPLE09", b"SAMPLE99")
            with self.assertRaises(doctor.PortfolioDoctorError) as exc:
                doctor.run_isolated_memory("holdings.csv", wrong)
            self.assertEqual(exc.exception.code, "PDF_FAILED")
            five = b"\n".join(payload.splitlines()[:6])
            with self.assertRaises(doctor.PortfolioDoctorError) as exc:
                doctor.run_isolated_memory("holdings.csv", five)
            self.assertEqual(exc.exception.code, "PDF_FAILED")
        self.assertEqual(len(pages), 16)
        self.assertIn("Page 16 of 16", pages[-1])
        self.assertIn("02 / HOLDING-WISE VERIFIED RESULTS", pages[1])
        self.assertIn("03 / HOLDING-WISE VERIFIED RESULTS", pages[2])
        self.assertIn("04 / NIFTY 500 INDUSTRY-PEER WATCHLIST", pages[3])
        self.assertIn("05 / RISK, SOURCES AND LIMITS", pages[4])
        self.assertIn("06 / HISTORICAL RISK", pages[5])
        for i, sym in enumerate(names):
            self.assertIn(sym + " | Qty", pages[1 if i < 5 else 2])
            self.assertIn("COMPANY RESEARCH: " + sym, pages[6 + i])
        self.assertNotIn("CONTINUED", "\n".join(pages[3:]))
        self.assertIn("Not SEBI-registered investment advice", pages[4])
        self.assertNotIn("BUY NOW", "\n".join(pages))
        self.assertNotIn("health score: ", "\n".join(pages).lower())

    def test_five_positions_still_five_cards_on_one_page(self):
        frames = {s: candles(i) for i, s in enumerate(
            ("SAMPLEA", "SAMPLEB", "SAMPLEC", "SAMPLED", "SAMPLEE"))}
        records = [stock_record(s, f) for s, f in frames.items()]
        pages = texts(build_pdf_bytes(records, companies=prepare_company_pages(
            records, frames, now=NOW), risk=calculate_historical_risk(
            records, frames, now=NOW)))
        self.assertEqual(len(pages), 10)
        self.assertEqual(sum("HOLDING-WISE VERIFIED RESULTS" in p for p in pages), 1)
        self.assertIn("Page 10 of 10", pages[-1])

    def test_private_bot_sends_downloadable_template_without_holdings(self):
        sent = []
        class FakeBot:
            def send_document(self, uid, document, *, caption, timeout, protect_content):
                sent.append((uid, document.name, document.read(), caption, protect_content))
                return SimpleNamespace(message_id=111)
        env = {"bot": FakeBot()}
        sender = handler("_send_doctor_csv_template", env)
        self.assertTrue(sender(900))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "MSK_Portfolio_Doctor_10_Stocks_Template.csv")
        self.assertEqual(sent[0][2], blank_doctor_csv_template())
        self.assertFalse(sent[0][4])
        self.assertNotIn("samplea", sent[0][2].decode().lower())


if __name__ == "__main__":
    unittest.main()
