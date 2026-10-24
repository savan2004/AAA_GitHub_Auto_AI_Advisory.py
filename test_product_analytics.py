"""Offline product regressions. No Telegram or market network calls."""
import ast
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_breadth import calculate_breadth, completed_prices, format_breadth, IST
from portfolio_analytics import positions, totals, fundamental_view, exposure, format_card, annual_cagr, consecutive_periods
import portfolio_report as pr


def frame(n=300, up=True):
    idx = pd.bdate_range(end='2026-10-23',periods=n)
    close = np.linspace(100,200,n) if up else np.linspace(200,100,n)
    return pd.DataFrame(dict(Open=close,High=close+1,Low=close-1,Close=close,Volume=1000),index=idx)

NOW = datetime(2026,10,24,18,tzinfo=IST)

class BreadthTests(unittest.TestCase):
    def test_participation_denominators_and_short_history(self):
        f=frame();down=frame(up=False);short=frame(30)
        r=calculate_breadth({'UP':f,'DOWN':down,'SHORT':short,'STALE':f.iloc[:-1]},
                            ['UP','DOWN','SHORT','STALE','MISSING'],f,now=NOW)
        self.assertEqual((r['advances'],r['declines'],r['valid']),(2,1,3))
        self.assertEqual(r['coverage'],60)
        self.assertEqual(r['ma200']['count'],2)
        self.assertEqual(r['ma200']['pct'],50)
        self.assertEqual(r['ma20']['count'],3)
        self.assertEqual(r['high_low_count'],2)
        self.assertEqual((r['highs'],r['lows']),(1,1))
        self.assertEqual(r['trend_count'],3)
        self.assertIn('Insufficient coverage',r['interpretation'])

    def test_partial_session_excluded_before_buffer(self):
        f=frame();now=datetime(2026,10,23,15,45,tzinfo=IST)
        self.assertEqual(completed_prices(f,now).index[-1].date(),date(2026,10,22))
        self.assertEqual(completed_prices(f,now.replace(hour=16)).index[-1].date(),date(2026,10,23))

    def test_missing_common_session_and_invalid_close_excluded(self):
        f=frame();bad=f.copy();bad.iloc[-1,bad.columns.get_loc('Close')]=np.nan
        r=calculate_breadth({'BAD':bad,'OLD':f.iloc[:-1]},['BAD','OLD'],f,now=NOW)
        self.assertEqual(r['valid'],0)
        self.assertIsNone(r['ma20']['pct'])

    def test_divergence_and_stale_session(self):
        f=frame();d=frame(up=False)
        r=calculate_breadth({'A':d,'B':d,'C':f},['A','B','C'],f,now=NOW)
        self.assertIn('Index rising',r['interpretation'])
        old=calculate_breadth({'A':f},['A'],f,now=NOW+timedelta(days=15))
        self.assertTrue(old['stale']);self.assertIn('Stale',old['interpretation'])

    def test_five_session_fixed_cohort_and_html(self):
        f=frame();hole=f.copy();hole.iloc[-4,hole.columns.get_loc('Close')]=np.nan
        r=calculate_breadth({'A':f,'B':hole},['A','B'],f,{'A':'A&B'},now=NOW)
        self.assertEqual(r['trend_count'],1)
        self.assertEqual(r['trend_delta'],0)
        r.update(source='Sample <test>',fetched='now')
        self.assertIn('Sample &lt;test&gt;',format_breadth(r))

    def test_adjusted_cache_separated(self):
        import market_data as md
        md._cache.clear()
        with patch.object(md,'_download_chunk',return_value={'A.NS':frame()}) as fetch:
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=True)
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=True)
            self.assertEqual(fetch.call_count,1)
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=False)
            self.assertEqual(fetch.call_count,2)
        md._cache.clear()


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.fv=dict(fin=False,roe=18,roce=20,de=.2,ocf_pat=1,rev_cagr3=12,pat_cagr3=15,q_pat_yoy=12,pe=25)
        self.stm={'source':'Fixture','years':[{'label':'Mar 2026'}],
                  'quarters':[{'label':'Jun 2026'}]}
    def test_dated_cagr_matches_statement_endpoints(self):
        ys=[{'label':f'Mar {y}','revenue':v} for y,v in zip(range(2023,2027),[100,110,120,133.1])]
        self.assertEqual(annual_cagr(ys,'revenue',3),10)
        ys[1]['label']='Mar 2023'
        self.assertIsNone(annual_cagr(ys,'revenue',3))
        ys[1]['label']='Mar 2024';ys[0]['revenue']=0
        self.assertIsNone(annual_cagr(ys,'revenue',3))
    def test_quarter_comparisons_require_consecutive_dates(self):
        self.assertTrue(consecutive_periods([{'label':'Sep 2025'},{'label':'Dec 2025'},{'label':'Mar 2026'},{'label':'Jun 2026'}],3))
        self.assertFalse(consecutive_periods([{'label':'Sep 2025'},{'label':'Jan 2026'}],3))
    def test_missing_prices_never_create_zero_returns(self):
        rows=positions([{'symbol':'A','qty':10,'buy':100},{'symbol':'B','qty':10,'buy':200}],{'A':{'price':200,'date':'2026-10-23'}})
        t=totals(rows)
        self.assertFalse(t['complete']);self.assertEqual(t['pnl'],1000)
        self.assertEqual(t['cost'],1000);self.assertEqual(t['all_cost'],3000)
        self.assertIsNone(rows[1]['pnl']);self.assertIsNone(rows[1]['weight_pct'])
        self.assertIn('Totals incomplete',format_card(rows))
    def test_current_value_weights(self):
        rows=positions([{'symbol':'A','qty':1,'buy':100},{'symbol':'B','qty':1,'buy':100}],{'A':{'price':300},'B':{'price':100}})
        self.assertEqual(rows[0]['weight_pct'],75)
        self.assertEqual(exposure(rows)['buckets']['Not analysed'],100)
    def test_nonfinite_prices_and_costs(self):
        for price in [float('nan'),float('inf'),-1,0,True]:
            row=positions([{'symbol':'A','qty':1,'buy':100}],{'A':{'price':price}})[0]
            self.assertFalse(row['priced'])
        with self.assertRaises(ValueError):positions([{'symbol':'A','qty':1,'buy':float('nan')}],{})
    def test_fundamental_view_independent_of_price_trend(self):
        r=fundamental_view(self.fv,self.stm,today=date(2026,10,24))
        self.assertEqual(r['view'],'Sound fundamentals on available measures')
        self.assertNotIn('technical',r)
    def test_stale_missing_or_future_statements_block_positive_view(self):
        for stm in [{},{'years':[{'label':'Mar 2023'}],'quarters':[{'label':'Jun 2026'}]},
                    {'years':[{'label':'Mar 2026'}],'quarters':[{'label':'Dec 2027'}]}]:
            self.assertEqual(fundamental_view(self.fv,stm,today=date(2026,10,24))['view'],'Insufficient verified information')
    def test_lenders_never_use_industrial_leverage_rules(self):
        self.fv.update(fin=True,de=100,ocf_pat=-100,roce=None,pb=2)
        r=fundamental_view(self.fv,self.stm,today=date(2026,10,24))
        self.assertEqual(r['view'],'Insufficient verified information')
        self.assertFalse(any('Debt/equity' in x or 'cash conversion' in x for x in r['risks']))
        self.assertIn('asset-quality',r['valuation'])
    def test_fundamental_risks_not_hidden_by_growth(self):
        self.fv['ocf_pat']=.2
        r=fundamental_view(self.fv,self.stm,today=date(2026,10,24))
        self.assertEqual(r['view'],'Fundamental concerns')
    def test_parser_rejects_nan_and_excess_rows(self):
        rows,issues=pr.parse_text('A,10,nan\nB,inf,100')
        self.assertFalse(rows);self.assertEqual(len(issues),2)
        rows,issues=pr.parse_rows([['A',1,100]]*51)
        self.assertFalse(rows);self.assertIn('silently',issues[0])
    def test_plain_text_symbol_x_is_not_a_separator(self):
        rows,issues=pr.parse_text('AXISBANK 10 100')
        self.assertFalse(issues);self.assertEqual(rows[0]['symbol'],'AXISBANK')
        self.assertIsNone(pr.clean_symbol('ABC.BO'))
    def test_analysis_growth_uses_statement_periods_not_vendor_cagr(self):
        years=[{'label':f'Mar {y}','revenue':v,'net_profit':v/10,'ocf':v/10,'roce':20,'opm':10,'borrowings':10,'networth':100}
               for y,v in zip(range(2023,2027),[100,110,121,133.1])]
        stm={'source':'Fixture','years':years,'quarters':[{'label':l,'revenue':100,'net_profit':10} for l in ['Jun 2025','Sep 2025','Dec 2025','Mar 2026','Jun 2026']]}
        with patch('fundamentals.get_fundamentals',return_value={'roe':18,'pe':20}),patch('research_data.get_statements',return_value=stm),patch('research_data.get_screener_extras',return_value={'growth':{'Compounded Sales Growth':{'3 Years':99}}}),patch('nifty500_collector.SECTOR_STOCKS',{'IT':['QA']}):
            review,sector=pr._analyse('QA',None)
        self.assertEqual(review['fv']['rev_cagr3'],10)
        self.assertEqual(sector,'IT')
    def test_duplicate_lots_and_xlsx(self):
        rows,issues=pr.parse_text('Symbol,Qty,Buy Price\nABC,2,100\nABC,3,200')
        self.assertEqual(rows[0]['qty'],5);self.assertEqual(rows[0]['buy'],160)
        import openpyxl
        wb=openpyxl.Workbook();wb.active.append(['Symbol','Qty','Buy Price']);wb.active.append(['ABC',2,100])
        b=io.BytesIO();wb.save(b)
        got,issues=pr.parse_file('holdings.xlsx',b.getvalue())
        self.assertEqual(got[0]['symbol'],'ABC');self.assertFalse(issues)
    def test_stale_holdings_excluded_against_benchmark_session(self):
        f=frame()
        with patch('market_data.index_history',return_value=f),patch('market_data.bulk_history',return_value={'A':f.iloc[:-1],'B':f}):
            rows,_=pr.price_holdings([{'symbol':'A','qty':1,'buy':100},{'symbol':'B','qty':1,'buy':100}])
        self.assertFalse(rows[0]['priced']);self.assertTrue(rows[1]['priced'])
    def test_job_attempts_all_holdings_even_unpriced_and_handles_failure(self):
        holdings=[{'symbol':s,'qty':1,'buy':100} for s in ['A','B','C']]
        rows=positions(holdings,{'A':{'price':100}})
        rv=fundamental_view(self.fv,self.stm,today=date(2026,10,24));rv.update(fv=self.fv,technical='Mixed')
        with tempfile.TemporaryDirectory() as d,patch.object(pr,'price_holdings',return_value=(rows,{})),patch.object(pr,'_analyse',side_effect=[(rv,'IT'),ValueError('failure'),(rv,'IT')]) as analyse,patch.object(pr,'build_summary_pdf'):
            out=pr.run_job(holdings,d)
            self.assertEqual(analyse.call_count,3)
            self.assertEqual(len(out['notes']),1)
            self.assertEqual(len(out['recs']),3)

class IntegrationTests(unittest.TestCase):
    def test_portfolio_remove_does_not_deadlock_and_save_is_atomic(self):
        # Extract class without constructing or starting the Telegram client.
        source=pathlib.Path('main.py').read_text()
        tree=ast.parse(source)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PortfolioManager')
        ns=dict(os=os,json=json,threading=threading,tempfile=tempfile,logger=__import__('logging').getLogger('test'))
        exec(compile(ast.Module(body=[cls],type_ignores=[]),'main.py','exec'),ns)
        with tempfile.TemporaryDirectory() as d:
            m=ns['PortfolioManager'](os.path.join(d,'holdings.json'))
            m.add(1,'ABC',2,100)
            result=[];t=threading.Thread(target=lambda:result.append(m.remove(1,'ABC')),daemon=True)
            t.start();t.join(2)
            self.assertFalse(t.is_alive());self.assertEqual(result,[True])
            self.assertEqual(json.loads(pathlib.Path(m._file).read_text()),{'1':{}})

class DeliveryTests(unittest.TestCase):
    def runtime(self):
        from unittest.mock import MagicMock
        source=pathlib.Path('main.py').read_text()
        funcs=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ('_run_portfolio_review','_private_portfolio')]
        ns=dict(time=__import__('time'),tempfile=tempfile,html=__import__('html'),
                logger=__import__('logging').getLogger('test'),bot=MagicMock(),
                safe_send=MagicMock(),_send_product_text=MagicMock(),
                _portfolio_slot=threading.BoundedSemaphore(1))
        for f in funcs: f.decorator_list=[]
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'main.py','exec'),ns)
        return ns
    def test_private_delivery_and_temp_cleanup_even_on_send_failure(self):
        from types import SimpleNamespace
        for fail in (False,True):
            ns=self.runtime(); paths=[]
            self.assertFalse(ns['_private_portfolio'](SimpleNamespace(chat=SimpleNamespace(id=-1,type='group'))))
            def fake_job(holdings,directory,**kwargs):
                path=os.path.join(directory,'review.pdf');pathlib.Path(path).write_bytes(b'%PDF-fixture')
                paths.append(path);return {'summary':path}
            if fail: ns['bot'].send_document.side_effect=RuntimeError('send failed')
            ns['_portfolio_slot'].acquire()
            with patch.object(pr,'run_job',side_effect=fake_job):
                ns['_run_portfolio_review'](123,[{'symbol':'ABC','qty':1,'buy':100}])
            self.assertFalse(os.path.exists(paths[0]))
            self.assertTrue(ns['_portfolio_slot'].acquire(blocking=False))
            ns['_portfolio_slot'].release()
    def test_invalid_upload_is_not_partially_analysed(self):
        from types import SimpleNamespace
        ns=self.runtime();ns['bot'].get_file.return_value=SimpleNamespace(file_path='fixture.csv')
        ns['bot'].download_file.return_value=b'Symbol,Qty,Buy Price\nABC,2,100\nBAD,nan,200'
        ns['_portfolio_slot'].acquire()
        with patch.object(pr,'run_job') as job:
            ns['_run_portfolio_review'](123,document=SimpleNamespace(file_id='fake',file_name='fixture.csv'))
            job.assert_not_called()
        self.assertTrue(ns['_send_product_text'].called)
    def test_pdf_contains_evidence_and_missing_price_disclosure(self):
        from pypdf import PdfReader
        rows=positions([{'symbol':'DEMO','qty':1,'buy':100}],{})
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'TELEGRAM_TOKEN':'123:offline'}):
            path=pr.build_summary_pdf(rows,os.path.join(d,'review.pdf'))
            text=' '.join(p.extract_text() for p in PdfReader(path).pages)
            self.assertIn('Total portfolio value and P&L are unknown',text)
            self.assertIn('fundamental analysis unavailable',text)
            self.assertIn('Method, limitations and sources',text)

if __name__=='__main__':unittest.main()
