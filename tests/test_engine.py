import json
import unittest

import numpy as np
import pandas as pd

from demo.generate_demos import dataset, bull_flag, ohlcv
from trading_chart import analyze, render_chart, validate_data
from trading_chart.config import ChartConfig
from trading_chart.indicators import adx_label, volume_label, calculate
from trading_chart.engine import classify_trend, signal_at
from trading_chart.patterns import Context, select_pattern
from trading_chart.patterns import (bull_flag as BULL, bear_flag as BEAR,
    channel_breakout as CHANNEL, ascending_triangle as ASC, descending_triangle as DESC,
    double_bottom as DBOT, double_top as DTOP,
    consolidation_breakout as CONS, pullback_to_ema as PULL)
from trading_chart.patterns.common import Hit


def shape(kind):
    """Causal fabricated geometry for pattern unit tests, not financial evidence."""
    n=130
    idx=np.arange(n,dtype=float)
    close=100+.25*idx
    volume=np.full(n,120_000.)
    if kind == "bear_flag":
        close[:111]=160+np.sin(idx[:111]*.1)*.4
        close[111:120]=np.linspace(159,139,9)
        close[120:129]=np.linspace(140,143.4,9)
        close[-1]=134
    if kind == "channel_breakout":
        close[:107]=103+np.sin(idx[:107]*.14)
        close[107:129]=110+np.arange(22)*.45
        close[-1]=125
    if kind in ("ascending_triangle", "descending_triangle"):
        close[:101]=110
        close[101:129]=115
        close[-1]=125 if kind=="ascending_triangle" else 95
    if kind in ("double_bottom","double_top"):
        close[:91]=114
        close[91:129]=115
        close[-1]=124 if kind=="double_bottom" else 106
    if kind == "consolidation_breakout":
        close[:117]=100
        close[117:129]=100.15
        close[-1]=106
    if kind == "pullback_to_ema":
        close[:125]=90+idx[:125]*.36
        close[125:129]=close[124]+np.array([-.4,-.5,.1,.5])
        close[-1]=close[124]+4
    df=ohlcv(close,volume,spread=.5)
    df.loc[n-1,"volume"]=400_000
    if kind=="channel_breakout":
        for j in range(107,129):
            mid=110+(j-107)*.45
            df.loc[j,["open","close","high","low"]]=[mid-.1,mid+.1,mid+2.0,mid-2.0]
    if kind=="ascending_triangle":
        for j in range(101,129):
            t=j-101
            h=120 if t in (4,11,18,25) else 117.8
            lo=103+.45*t+(0 if t in (2,9,16,23) else 1.8)
            df.loc[j,["open","close","high","low"]]=[115,115,h,lo]
    if kind=="descending_triangle":
        for j in range(101,129):
            t=j-101
            h=125-.4*t+(1.8 if t in (2,9,16,23) else 0)
            lo=108 if t in (4,11,18,25) else 110
            df.loc[j,["open","close","high","low"]]=[112,112,h,lo]
    if kind=="double_bottom":
        for j in range(92,129):
            df.loc[j,["open","close","high","low"]]=[112,112,117,109]
        for j,l,v in [(98,104.0,110_000),(111,104.5,150_000)]:
            df.loc[j,"low"]=l;df.loc[j,"volume"]=v
        df.loc[103,"high"]=120
    if kind=="double_top":
        for j in range(92,129):
            df.loc[j,["open","close","high","low"]]=[117,117,121,112]
        for j,h,v in [(98,128,175_000),(111,127.6,100_000)]:
            df.loc[j,"high"]=h;df.loc[j,"volume"]=v
        df.loc[103,"low"]=108
    if kind=="consolidation_breakout":
        for j in range(85,117):
            df.loc[j,["open","close","high","low"]]=[100,100,108,92]
        for j in range(117,129):
            df.loc[j,["open","close","high","low"]]=[100,100.15,100.5,99.5]
    if kind=="pullback_to_ema":
        d=calculate(df)
        at=128
        df.loc[at,"low"]=min(df.low.iloc[at], float(d.ema20.iloc[at]) )
    df.loc[n-1,"high"]=max(float(df.loc[n-1,"high"]),float(df.loc[n-1,"close"])+.5)
    df.loc[n-1,"low"]=min(float(df.loc[n-1,"low"]),float(df.loc[n-1,"close"])-.5)
    return df


class ChartTests(unittest.TestCase):
    def test_adx_thresholds_exact(self):
        self.assertEqual([adx_label(x) for x in (19.99,20,24.99,25,35,35.01)],
                         ["Weak","Trend building","Trend building","Strong","Strong","Very strong"])

    def test_volume_thresholds_exact(self):
        self.assertEqual([volume_label(x) for x in (1.49,1.5,1.99,2,2.99,3)],
                         ["","Above normal","Above normal","High","High","Explosive"])

    def test_chart_three_demos_all_fields_present_and_credible_trend(self):
        for kind,trend in (("uptrend","Uptrend"),("sideways","Sideways"),("downtrend","Downtrend")):
            d,p=analyze(dataset(kind))
            self.assertEqual(p["trend"],trend)
            self.assertIn(p["pattern"],["No clean pattern","Channel Breakout","Bull Flag","Bear Flag",
                         "Double Bottom","Double Top","Pullback to EMA","Consolidation Breakout",
                         "Ascending Triangle","Descending Triangle"])
            self.assertIsNotNone(p["adx"])
            self.assertIn(p["signal"],("BUY","SELL","WAIT"))
            for field in ("trend","pattern","adx","adx_label","vol_multiple","vol_label","signal",
                          "quality","markers","pattern_anchors"):
                self.assertIn(field,p)
            json.dumps(p)

    def test_insufficient_data_does_not_emit_triggers(self):
        d,p=analyze(dataset("uptrend", n=49))
        self.assertEqual(p["status"],"Insufficient data")
        self.assertEqual(p["signal"],"WAIT")
        self.assertEqual(p["markers"],[])
        self.assertEqual(p["pattern"],"No clean pattern")

    def test_bull_flag_cleanliness_over_75(self):
        d,p=analyze(bull_flag())
        self.assertEqual(p["pattern"],"Bull Flag")
        self.assertGreater(p["cleanliness"],75)

    def test_choppy_random_walk_not_force_fit(self):
        _,p=analyze(dataset("sideways"))
        self.assertEqual(p["pattern"],"No clean pattern")
        self.assertEqual(p["signal"],"WAIT")

    def test_invalid_bars_rejected(self):
        sample=dataset("uptrend",n=70)
        sample.loc[0,"high"]=0
        with self.assertRaises(ValueError):
            validate_data(sample)
        sample=dataset("uptrend",n=70)
        sample.loc[3,"volume"]=-1
        with self.assertRaises(ValueError):
            validate_data(sample)
        sample=dataset("uptrend",n=70)
        sample.loc[10,"close"]=float("nan")
        with self.assertRaises(ValueError):
            validate_data(sample)

    def test_render_html_payload_info_box_standalone(self):
        html,p=render_chart(dataset("sideways",n=62))
        self.assertIn("<strong>Trend:</strong>",html)
        self.assertIn("<strong>Pattern:</strong>",html)
        self.assertIn("<strong>ADX:</strong>",html)
        self.assertIn("<strong>Volume:</strong>",html)
        self.assertIn("<strong>Signal:</strong>",html)
        self.assertIn("<strong>Quality:</strong>",html)
        self.assertIn("plotly.js",html.lower())
        self.assertTrue(len(html)>100_000)


class EachPatternTests(unittest.TestCase):
    def test_each_detector_is_rejecting_random_walk(self):
        d=calculate(dataset("sideways"))
        ctx=Context(d)
        for name,det in [("Bull Flag",BULL.detect),("Bear Flag",BEAR.detect),
                         ("Channel Breakout",CHANNEL.detect),("Ascending Triangle",ASC.detect),
                         ("Descending Triangle",DESC.detect),("Double Bottom",DBOT.detect),
                         ("Double Top",DTOP.detect),("Consolidation Breakout",CONS.detect),
                         ("Pullback to EMA",PULL.detect)]:
            with self.subTest(name=name):
                self.assertIsNone(det(ctx,len(d)-1))

    def test_positive_patterns(self):
        cases=[("bull_flag",BULL.detect,bull_flag,"Bull Flag"),
               ("bear_flag",BEAR.detect,lambda:shape("bear_flag"),"Bear Flag"),
               ("channel",CHANNEL.detect,lambda:shape("channel_breakout"),"Channel Breakout"),
               ("ascending",ASC.detect,lambda:shape("ascending_triangle"),"Ascending Triangle"),
               ("descending",DESC.detect,lambda:shape("descending_triangle"),"Descending Triangle"),
               ("bottom",DBOT.detect,lambda:shape("double_bottom"),"Double Bottom"),
               ("top",DTOP.detect,lambda:shape("double_top"),"Double Top"),
               ("consolidation",CONS.detect,lambda:shape("consolidation_breakout"),"Consolidation Breakout"),
               ("ema",PULL.detect,lambda:shape("pullback_to_ema"),"Pullback to EMA")]
        for label,det,make,name in cases:
            with self.subTest(label=label):
                d=calculate(make())
                p=det(Context(d),len(d)-1)
                self.assertIsNotNone(p,label)
                if p is not None:
                    self.assertEqual(p.name,name)
                    self.assertGreaterEqual(p.confidence,60)


if __name__=="__main__":
    unittest.main()

class SignalQualityTests(unittest.TestCase):
    def test_high_buy_and_sell_require_all_gates(self):
        from unittest.mock import patch
        from trading_chart.engine import signal_at
        d=calculate(dataset("uptrend",n=170))
        i=len(d)-1
        ctx=Context(d)
        for direction,trend,action in (("BUY","Uptrend","BUY"),("SELL","Downtrend","SELL")):
            p=Hit("Channel Breakout",120,89.,direction,i,True)
            d.loc[i,"adx"]=29.
            d.loc[i,"vol_multiple"]=2.4
            with patch("trading_chart.engine._cross_recent",return_value=True):
                self.assertEqual(signal_at(d,i,ctx,trend,p,ctx.cfg),(action,"High"))
            with patch("trading_chart.engine._cross_recent",return_value=False):
                self.assertEqual(signal_at(d,i,ctx,trend,p,ctx.cfg),("WAIT","Weak"))

    def test_medium_and_weak_wait(self):
        from unittest.mock import patch
        d=calculate(dataset("uptrend",n=170))
        i=len(d)-1
        ctx=Context(d)
        p=Hit("Pullback to EMA",115,68.,"BUY",i,True)
        d.loc[i,"adx"]=22.
        d.loc[i,"vol_multiple"]=1.7
        with patch("trading_chart.engine._cross_recent",return_value=False):
            self.assertEqual(signal_at(d,i,ctx,"Uptrend",p,ctx.cfg),("BUY","Medium"))
        d.loc[i,"adx"]=19.
        self.assertEqual(signal_at(d,i,ctx,"Sideways",None,ctx.cfg),("WAIT","Weak"))
