"""Regression contract: keep original MSK dark PNG layout, update only data."""
import contextlib
import io
import os
from pathlib import Path
import re
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from legacy_chart_quant import compute_chart_evidence, formatted_rows, draw_signal_rows, draw_price_evidence

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    i=np.arange(180)
    prices=100+0.3*i+np.sin(i*.25)*3
    prev=np.r_[prices[0]-.3, prices[:-1]]
    v=np.full(len(i), 120_000.)
    v[-1]=3*v[-2]
    return pd.DataFrame({
        "Open":prev,"High":np.maximum(prev,prices)+1,
        "Low":np.minimum(prev,prices)-1,"Close":prices,
        "Volume":v,
    },index=pd.date_range("2026-01-01",periods=len(i),freq="B",tz="UTC"))


class OldTemplateQuantTests(unittest.TestCase):
    def test_six_fields_and_volume_display(self):
        data,p=compute_chart_evidence(fixture())
        rows=formatted_rows(p)
        self.assertEqual([a for a,_ in rows],
                         ["Trend","Pattern","ADX","Volume","Signal","Quality"])
        self.assertIn(rows[4][1],("BUY","SELL","WAIT"))
        self.assertEqual(rows[3][1], f"{p['vol_multiple']:.1f}x Explosive")
        fig,(ax1,ax2)=plt.subplots(1,2)
        draw_signal_rows(ax2,p)
        draw_price_evidence(ax1,data,p)
        fig.canvas.draw()
        plt.close(fig)

    def test_absent_data_is_not_a_trade_signal(self):
        frame=fixture().iloc[:32]
        d,p=compute_chart_evidence(frame)
        self.assertEqual(p["signal"],"WAIT")
        self.assertEqual(p["status"],"Insufficient data")
        self.assertEqual(formatted_rows(p)[0][1],"Insufficient data")
        self.assertEqual(len(d),32)

    def test_original_v7_chart_design_unchanged(self):
        source=(ROOT/"gen_smart_stock_chart.py").read_text("utf-8")
        # All original visual layout contracts must still exist.
        for anchor in (
            'BG_DARK    = "#131722"',
            'mpf.plot(', 'volume=True, panel_ratios=(5, 1.2, 2, 1.8)',
            'fw, fh  = fig.get_size_inches()',
            'fig.set_size_inches(fw + panel_w, fh)',
            'fig.add_axes([ratio + 0.004, 0.0',
            "52-WEEK RANGE",
            'fig.savefig(OUT_FILE, dpi=160',
            '"AutoAiAdvisory (SK)  v7.0"',
        ):
            self.assertIn(anchor,source,anchor)
        self.assertIn('draw_signal_rows(sa, quant_payload)',source)
        self.assertIn('draw_price_evidence(ax0, quant_df, quant_payload)',source)
        self.assertIn('vol_s.shift(1).rolling(20',source)
        # Old simplistic double-top annotations no longer default.
        self.assertIn('MSK_CHART_SHOW_LEGACY_DECORATIONS',source)

    def test_true_png_smoke_with_mocked_data_source(self):
        """Run exactly the legacy rendering script, never the new HTML renderer."""
        import yfinance as yf
        frame=fixture()
        class FakeTicker:
            @property
            def info(self):
                return {
                    "longName":"Demonstration Synthetic Research Co.",
                    "sector":"Technology","marketCap":10000000000,
                    "trailingPE":20, "fiftyTwoWeekHigh":200.,
                    "fiftyTwoWeekLow":75.,
                }
        def download(*a,**kw):
            return frame.copy()
        path=ROOT/"gen_smart_stock_chart.py"
        oldcwd=Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                output=io.StringIO()
                with patch.object(yf,"download",side_effect=download), \
                     patch.object(yf,"Ticker",side_effect=lambda _:FakeTicker()), \
                     patch.object(sys,"argv",[str(path),"MSKDEMO.NS","Demo Synthetic Co.","6mo"]), \
                     contextlib.redirect_stdout(output):
                    runpy.run_path(str(path),run_name="__main__")
                generated=list((Path(temporary)/"output").glob("chart_MSKDEMO_*.png"))
                self.assertEqual(len(generated),1,output.getvalue())
                from PIL import Image
                with Image.open(generated[0]) as img:
                    self.assertGreater(img.width,1500)
                    self.assertGreater(img.height,850)
                    print(f"OLD_TEMPLATE_PNG_OK size={img.width}x{img.height} bytes={generated[0].stat().st_size}")
            finally:
                os.chdir(oldcwd)


if __name__=="__main__":
    unittest.main()
