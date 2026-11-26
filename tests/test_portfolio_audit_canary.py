"""Private Portfolio Audit canary: real code, synthetic-only offline evidence."""
import ast
import copy
import io
import logging
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfReader

import portfolio_doctor_live as doctor
from portfolio_audit_report import IST, SyntheticProvider
from portfolio_audit_bridge import build_verified_audit


NOW = datetime(2026, 11, 19, 10, 0, tzinfo=IST)
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _function(name, env):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == name)
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[node], type_ignores=[])), str(MAIN), "exec"), env)
    return env[name]


def _message(uid):
    return SimpleNamespace(
        chat=SimpleNamespace(id=uid, type="private"),
        from_user=SimpleNamespace(id=uid),
        text="/audit")


class PortfolioAuditCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = SyntheticProvider()
        cls.holdings = [
            {"symbol": "FCL", "qty": 40000, "buy": 15},
            {"symbol": "SCI", "qty": 150, "buy": 140.5},
            {"symbol": "DLF", "qty": 50, "buy": 458},
        ]
        cls.frames = {h["symbol"]:
                      cls.provider.stock_history(h["symbol"]).frame
                      for h in cls.holdings}
        cls.recs = [
            {"symbol": h["symbol"], "date": "2026-11-18",
             "close": float(cls.frames[h["symbol"]]["Close"].iloc[-1]),
             "price_source": "NSE official daily file"}
            for h in cls.holdings
        ]
        # A real Telegram account never receives synthetic data. The fixture
        # feeds a hard-coded offline source into the same validator.
        with patch("market_data.bulk_history", return_value=cls.frames), \
             patch("market_data.index_history",
                   side_effect=lambda symbol, *a, **kw:
                   cls.provider.index_history(symbol).frame), \
             patch("portfolio_doctor_peers.official_nifty500_industries",
                   return_value={}):
            cls.report = build_verified_audit(
                cls.holdings, cls.recs, cls.frames, now=NOW,
                extras=cls.provider, budget_seconds=58)

    def test_12_pages_and_no_synthetic_demo_in_production_report(self):
        pdf = PdfReader(io.BytesIO(self.report), strict=True)
        self.assertEqual(len(pdf.pages), 12)
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("PORTFOLIO DASHBOARD", text)
        self.assertIn("AUD-", text)
        self.assertIn("FCL", text)
        self.assertIn("SCI", text)
        self.assertIn("DLF", text)
        self.assertNotIn("SYNTHETIC DEMO", text)
        self.assertNotIn("health score", text.lower())

    def test_strict_parent_accepts_audit_only_in_new_release_gate(self):
        result = SimpleNamespace(returncode=0, stdout=self.report, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=result):
            self.assertEqual(doctor.run_isolated_memory(
                "typed.csv", b"FCL 40000 15", timeout=150, audit=True), self.report)
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory(
                    "typed.csv", b"FCL 40000 15", timeout=150)
            self.assertEqual(err.exception.code, "PDF_FAILED")

    def test_poor_price_crosscheck_refuses_generation(self):
        recs = [dict(x) for x in self.recs]
        recs[0]["close"] += 1
        with self.assertRaises(doctor.PortfolioDoctorError) as err:
            build_verified_audit(self.holdings, recs, self.frames, now=NOW,
                                 extras=self.provider, budget_seconds=1)
        self.assertEqual(err.exception.code, "STALE_PRICES")

    def test_audit_command_keeps_only_expiring_private_mode(self):
        notices = []
        env = {
            "threading": threading, "time": time,
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_running": set(),
            "_portfolio_doctor_waiting": {},
            "_portfolio_audit_mode": {},
            "_builder_form": {},
            "_doctor_allowed": lambda m: m.chat.type == "private",
            "safe_send": lambda uid, msg: notices.append((uid, msg)),
        }
        start = _function("portfolio_audit_start", env)
        start(_message(101))
        self.assertTrue(env["_portfolio_doctor_waiting"][101] > time.monotonic())
        self.assertEqual(env["_portfolio_audit_mode"][101],
                         env["_portfolio_doctor_waiting"][101])
        self.assertIn("10-14 page", notices[-1][1])
        self.assertNotIn("FCL", str(env["_portfolio_audit_mode"]))

    def test_telegram_handoff_requests_isolated_audit_only_after_explicit_optin(self):
        class FakeMarkup:
            def __init__(self, row_width=2): self.buttons = []
            def add(self, *buttons): self.buttons.extend(buttons)

        class FakeBot:
            def __init__(self): self.received = []
            def send_document(self, uid, handle, **kw):
                self.received.append((uid, handle.name, handle.read(), kw))
                return SimpleNamespace(
                    message_id=1234, document=SimpleNamespace(file_id="telegram-file"))

        bot = FakeBot()
        notices = []
        env = {
            "threading": threading, "time": time,
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_running": set(),
            "_portfolio_doctor_waiting": {101: time.monotonic()+180},
            "_portfolio_audit_mode": {101: time.monotonic()+180},
            "_portfolio_doctor_slots": threading.BoundedSemaphore(2),
            "_doctor_allowed": lambda m: True,
            "safe_send": lambda uid, msg: notices.append((uid,msg)),
            "bot": bot, "executor": SimpleNamespace(submit=lambda fn: fn()),
            "types": SimpleNamespace(
                InlineKeyboardMarkup=FakeMarkup,
                InlineKeyboardButton=lambda name, callback_data:
                    SimpleNamespace(text=name, callback_data=callback_data)),
            "logger": logging.getLogger("audit-fixture"),
        }
        handler = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          return_value=self.report) as runner:
            handler(_message(101), _typed_payload=b"FCL 40000 15")
        runner.assert_called_once_with(
            "typed_holdings.csv", b"FCL 40000 15", timeout=150, audit=True)
        self.assertEqual(len(bot.received), 1)
        self.assertEqual(bot.received[0][1], "MSK_Portfolio_Audit.pdf")
        self.assertTrue(bot.received[0][2].startswith(b"%PDF-"))
        self.assertEqual(bot.received[0][3]["protect_content"], True)
        self.assertEqual(env["_portfolio_audit_mode"], {})
        self.assertEqual(env["_portfolio_doctor_running"], set())


if __name__ == "__main__":
    unittest.main()



class AuditEvidenceRepairTests(unittest.TestCase):
    def raw(self):
        from portfolio_doctor_advanced import normalize_fundamentals
        return normalize_fundamentals('SAMPLE', 'Capital Goods', {
            '_source':'Screener.in', '_statement_scope':'consolidated',
            '_source_url':'https://www.screener.in/company/SAMPLE/consolidated/',
            '_retrieved_utc':'2026-11-19T04:30:00Z',
            'years':[{'label':'Mar 2026','revenue':100,'net_profit':10}],
            'quarters':[{'label':'Sep 2026','revenue':30,'net_profit':-2,'opm':12},
                        {'label':'Dec 2030','revenue':999,'net_profit':999}],
            'shareholding':[{'label':'Sep 2026','promoters':51,'fii':12,'dii':20}],
        }, today=NOW.date())

    def test_real_parser_schema_transports_quarters_and_ownership(self):
        raw=self.raw()
        self.assertEqual(len(raw['quarterly']),1)
        self.assertEqual(raw['quarterly'][0]['profit'],-2)
        self.assertEqual(raw['quarterly'][0]['period'],'2026-09-30')
        self.assertEqual(raw['ownership'][0]['promoter'],51)
        self.assertFalse(raw['quarterly'][0]['issuer_reconciled'])

    def test_default_provider_preserves_metric_metadata(self):
        from portfolio_audit_report import MSKRepoProvider
        with patch('portfolio_doctor_advanced.collect_fundamentals_no_holdings_cache',return_value=self.raw()):
            fund=MSKRepoProvider().fundamentals('SAMPLE')
        fact=fund['metrics']['net_profit']
        self.assertEqual(fact['scope'],'consolidated')
        self.assertEqual(fact['unit'],'INR Cr')
        self.assertEqual(fact['basis'],'FY')
        self.assertIn('/SAMPLE/',fact['source_url'])
        self.assertEqual(len(fund['quarterly']),1)
        self.assertEqual(len(fund['ownership']),1)

    def test_comparison_requires_same_period_scope_and_basis(self):
        from portfolio_audit_report import _financial_flags
        fact={'value':10,'period':'2026-03-31','source':'Secondary',
              'scope':'consolidated','basis':'FY','unit':'INR Cr'}
        profit=dict(fact,value=20)
        def flags(cfo, earnings, **extra):
            return _financial_flags('SAMPLE',dict(metrics={
                'operating_cash_flow':cfo,'net_profit':earnings},**extra))
        self.assertTrue(flags(fact,profit))
        for key,value in [('period','2025-03-31'),('scope','standalone'),('basis','TTM'),('unit','INR')]:
            self.assertFalse(flags(dict(fact,**{key:value}),profit))
        self.assertFalse(flags(fact,profit,is_lender=True))

    def test_displayed_benchmark_window_starts_at_100(self):
        import pandas as pd
        from portfolio_audit_report import _display_benchmark_window
        frame=pd.DataFrame({'Portfolio':range(100,600),'Nifty 50':range(200,700)})
        result=_display_benchmark_window(frame)
        self.assertEqual(len(result),260)
        self.assertEqual(result.iloc[0].tolist(),[100,100])
        self.assertAlmostEqual(result.iloc[-1,0],599/340*100)
        self.assertEqual(frame.iloc[0,0],100)

    def test_same_page_collector_reads_ownership_and_records_url(self):
        from portfolio_doctor_advanced import _screener_same_page_evidence
        response=SimpleNamespace(ok=True,text='data-table')
        with patch('research_data.parse_years',return_value=[{'label':'Mar 2026'}]), \
             patch('research_data.parse_quarters',return_value=[]), \
             patch('research_data.parse_shareholding',return_value=[{'label':'Sep 2026','promoters':50}]), \
             patch('research_data.detect_layout',return_value='corporate'):
            extras,_=_screener_same_page_evidence('SAMPLE',get=lambda *a,**kw:response)
        self.assertEqual(extras['shareholding'][0]['promoters'],50)
        self.assertTrue(extras['_source_url'].endswith('/consolidated/'))

    def test_fresh_subprocess_renders_under_read_only_guard(self):
        import os
        import subprocess
        import sys
        import tempfile
        # Fixed labelled synthetic test only; no production provider or account.
        code = '''
import sys, logging
logging.disable(logging.CRITICAL)
import portfolio_doctor_live as doctor
doctor.enforce_worker_read_only()
from portfolio_audit_report import create_report, SyntheticProvider, IST
from datetime import datetime
result = create_report([{'symbol':'FCL','qty':2,'buy':15}],
    provider=SyntheticProvider(), now=datetime(2026,11,19,10,tzinfo=IST))
sys.stdout.buffer.write(result.pdf)
'''
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,HOME=folder,MPLCONFIGDIR=folder,
                     XDG_CACHE_HOME=folder,PYTHONDONTWRITEBYTECODE='1',MSK_RESEARCH_ONLY='1')
            result=subprocess.run([sys.executable,'-c',code],cwd=MAIN.parent,
                                  env=env,capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr.decode()[-1000:])
            pages=PdfReader(io.BytesIO(result.stdout),strict=True).pages
            self.assertEqual(len(pages),10)
            self.assertIn('SYNTHETIC DEMO',' '.join(page.extract_text() for page in pages))
            self.assertEqual(list(Path(folder).rglob('*')),[])
