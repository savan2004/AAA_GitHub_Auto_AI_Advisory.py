"""Offline full-PDF bridge: token auth, data quality and research integrity."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from flask import Flask
import internal_report_bridge as bridge

class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.register_blueprint(bridge.bp)
        self.cli=self.app.test_client()
        self.secret="m"*52
        self.patch=patch.dict(os.environ,{"MSK_REPORT_BRIDGE_KEY":self.secret})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def headers(self):
        return {"X-MSK-Report-Key":self.secret}

    def inputs(self):
        now=datetime.now(timezone(timedelta(hours=5,minutes=30)))
        series=[SimpleNamespace(date=lambda dt=now:dt.date()) for _ in range(225)]
        years=[{"revenue":100+i*25,"net_profit":8+i} for i in range(4)]
        quarters=[{"revenue":40+i*3,"net_profit":4+i} for i in range(5)]
        return {"df":SimpleNamespace(index=series,__len__=lambda :225),
                "stm":{"years":years,"quarters":quarters,"freshness":{"status":"FRESH"}}}

    def valid_inputs(self):
        d=self.inputs()
        class Eod:
            index=[datetime.now(timezone(timedelta(hours=5,minutes=30)))]*225
            def __len__(self):return 225
        d["df"]=Eod()
        return d

    def test_request_authentication_and_symbol_safety(self):
        self.assertEqual(self.cli.post("/api/internal/full-research-pdf",json={"symbol":"RELIANCE"}).status_code,401)
        self.assertEqual(self.cli.post("/api/internal/full-research-pdf",json={"symbol":"../evil"},headers=self.headers()).status_code,400)
        with patch.dict(os.environ,{"MSK_REPORT_BRIDGE_KEY":"short"}):
            self.assertEqual(self.cli.post("/api/internal/full-research-pdf",json={"symbol":"RELIANCE"},headers=self.headers()).status_code,503)

    def test_years_quarters_prices_freshness_fail_closed(self):
        for key in ("annual","quarter","stale","price"):
            d=self.valid_inputs()
            if key=="annual":d["stm"]["years"]=d["stm"]["years"][:2]
            if key=="quarter":d["stm"]["quarters"]=d["stm"]["quarters"][:1]
            if key=="stale":d["stm"]["freshness"]["status"]="STALE"
            if key=="price":d["df"]=list(range(12))
            ok,why=bridge.validate_full_inputs(d)
            self.assertFalse(ok,key)
            self.assertTrue(why)

    def test_pdf_minimum_page_gate_blocks_three_page_snapshot(self):
        self.assertEqual(bridge.approx_pdf_pages(b"%PDF-1.4\n"+b"/Type /Page\n"*3),3)
        self.assertEqual(bridge.approx_pdf_pages(b"%PDF-1.4\n"+b"/Type /Pages\n"*9),0)

    def test_route_rejects_three_page_report_even_with_verified_data(self):
        m=ModuleType("report_generator")
        m.collect_inputs=lambda s:self.valid_inputs()
        m.assemble=lambda inp,use_ai=False:{"sc":{"insufficient_evidence":False},
                                         "ch":{"tech_price":"price.png","tech_momentum":"momentum.png"}}
        def build(r,out):
            Path(out).write_bytes(b"%PDF-1.4\n"+b"/Type /Page\n"*3 + b"x"*50000)
        m.build_pdf=build
        with patch.dict(sys.modules,{"report_generator":m}):
            response=self.cli.post("/api/internal/full-research-pdf",json={"symbol":"RELIANCE"},headers=self.headers())
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.get_json()["error"],"full_report_document_integrity_failed")

    def test_positive_full_report_returns_attachment_with_page_count(self):
        m=ModuleType("report_generator")
        m.collect_inputs=lambda s:self.valid_inputs()
        m.assemble=lambda inp,use_ai=False:{"sc":{"insufficient_evidence":False},
                                         "ch":{"tech_price":"price.png","tech_momentum":"momentum.png"}}
        def build(r,out):
            Path(out).write_bytes(b"%PDF-1.4\n"+b"/Type /Page\n"*9+b"x"*50000)
        m.build_pdf=build
        with patch.dict(sys.modules,{"report_generator":m}):
            response=self.cli.post("/api/internal/full-research-pdf",json={"symbol":"RELIANCE"},headers=self.headers())
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers["X-MSK-Report-Pages"],"9")
        self.assertIn("attachment",response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"],"no-store")

if __name__=="__main__":unittest.main()
