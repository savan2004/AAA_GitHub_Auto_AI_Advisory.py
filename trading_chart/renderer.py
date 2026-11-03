"""Single interactive self-contained HTML entry point with plotly z-order."""
from html import escape
import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .config import DEFAULT_CONFIG, ChartConfig
from .engine import analyze


def _panel(payload):
    adx = "Insufficient data" if payload["adx"] is None else f"{payload['adx']:.0f} {payload['adx_label']}"
    vol = "Insufficient data" if payload["vol_multiple"] is None else f"{payload['vol_multiple']:.1f}x"
    if payload["vol_multiple"] is not None and payload["vol_label"]:
        vol += " " + payload["vol_label"]
    fields = (
        ("Trend",payload["trend"]),
        ("Pattern",payload["pattern"]),
        ("ADX",adx),
        ("Volume",vol),
        ("Signal",payload["signal"]),
        ("Quality",payload["quality"]),
    )
    return '<aside class="msk-info">' + '<h3>MSK • Technical Research</h3>' + ''.join(
        f'<div><strong>{escape(k)}:</strong><span>{escape(str(v))}</span></div>' for k,v in fields
    ) + '<small>Automated, not investment advice. Await candle close; false signals possible.</small></aside>'


def render_chart(df: pd.DataFrame, cfg: ChartConfig=DEFAULT_CONFIG) -> tuple[str, dict]:
    """Render offline-capable interactive HTML and validated signal JSON payload.

    Input: DataFrame [timestamp, open, high, low, close, volume].
    Returns (html, payload). Invalid prices raise; insufficient bars show WAIT.
    """
    d,payload=analyze(df,cfg)
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.75,.25],
                      vertical_spacing=.035)
    x=d.timestamp
    if len(d)>=cfg.min_bars:
        segment=d.iloc[-min(len(d),cfg.channel_bars):]
        values=segment.close.to_numpy(dtype=float)
        indexes=np.arange(len(values),dtype=float)
        slope,intercept=np.polyfit(indexes,values,1)
        middle=slope*indexes+intercept
        residue=values-middle
        width=max(float(np.std(residue))*cfg.channel_band_stdev,float(segment.atr.iloc[-1])*.5)
        upper=middle+width;lower=middle-width
        fig.add_trace(go.Scatter(x=segment.timestamp,y=upper,mode="lines",name="Trend channel upper",
                                 line=dict(color="rgba(178,157,236,.45)",width=1,dash="dot")),row=1,col=1)
        fig.add_trace(go.Scatter(x=segment.timestamp,y=lower,mode="lines",name="Trend channel lower",
                                 line=dict(color="rgba(178,157,236,.45)",width=1,dash="dot"),
                                 fill="tonexty",fillcolor="rgba(140,114,201,.09)"),row=1,col=1)
    # Channel below candles; candle bodies/wicks visible, EMAs on top.
    fig.add_trace(go.Candlestick(x=x,open=d.open,high=d.high,low=d.low,close=d.close,
                                  name="OHLC",increasing_line_color="#1fbf86",decreasing_line_color="#ef5965",
                                  increasing_fillcolor="#1fbf86",decreasing_fillcolor="#ef5965"),row=1,col=1)
    if len(d)>=cfg.min_bars:
        fig.add_trace(go.Scatter(x=x,y=d.ema20,name="EMA 20",mode="lines",
                                 line=dict(color="#35c7ed",width=1.5)),row=1,col=1)
        fig.add_trace(go.Scatter(x=x,y=d.ema50,name="EMA 50",mode="lines",
                                 line=dict(color="#f9af5a",width=1.5)),row=1,col=1)
    for marker in payload["markers"][-1:]:
        buy=marker["type"]=="BUY"
        fig.add_trace(go.Scatter(x=[d.timestamp.iloc[marker["index"]]],y=[marker["price"]],
                                 mode="markers",name=marker["type"],
                                 marker=dict(symbol="triangle-up" if buy else "triangle-down",
                                             color="#00e79a" if buy else "#ff626c",size=16)),row=1,col=1)
    for anchor in payload["pattern_anchors"][-1:]:
        pos=anchor["index"]
        fig.add_annotation(x=d.timestamp.iloc[pos],y=float(d.high.iloc[pos]),
                           text=f'{anchor["name"]} ({anchor["confidence"]:.0f})',showarrow=True,
                           arrowhead=2,arrowcolor="#dfc8ff",bgcolor="rgba(35,29,51,.86)",
                           bordercolor="#665482",font=dict(size=10,color="#fff"),row=1,col=1)
    bars=["#16b880" if c>=o else "#ea5464" for o,c in zip(d.open,d.close)]
    fig.add_trace(go.Bar(x=x,y=d.volume,name="Volume",marker_color=bars,opacity=.65),row=2,col=1)
    if len(d)>=cfg.min_bars:
        fig.add_trace(go.Scatter(x=x,y=d.avg_volume_prior20,name="Vol MA20 (prior)",
                                 mode="lines",line=dict(color="#ffd17f",width=1)),row=2,col=1)
    fig.update_layout(template=cfg.plots_template,plot_bgcolor="#101827",paper_bgcolor="#101827",
                      font=dict(family="Inter,Arial,sans-serif"),
                      title=dict(text=cfg.chart_title,x=.025,font=dict(size=17)),
                      legend=dict(orientation="h",y=1.06,x=0),
                      height=760,margin=dict(l=62,r=26,t=100,b=30),
                      xaxis_rangeslider_visible=False,hovermode="x unified",showlegend=True)
    fig.update_yaxes(title_text="Price (₹)",row=1,col=1,showgrid=True,gridcolor="#263041")
    fig.update_yaxes(title_text="Volume",row=2,col=1,showgrid=True,gridcolor="#263041")
    # Local embedded Plotly runtime guarantees no CDN is needed for standalone HTML.
    plot=fig.to_html(include_plotlyjs=True,full_html=False,config={"responsive":True,"displaylogo":False})
    panel=_panel(payload)
    html='''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MSK Trading Chart</title><style>
*{box-sizing:border-box}body{margin:0;background:#0c1322;color:#f0f3fa;font-family:Inter,Arial,sans-serif}
.shell{max-width:1450px;margin:0 auto;padding:20px}.wrap{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:18px}
.chart{min-width:0;border-radius:16px;overflow:hidden;border:1px solid #202d44}.msk-info{background:#131e30;border:1px solid #2b3a53;
border-radius:16px;padding:20px;align-self:start;position:sticky;top:20px}.msk-info h3{font-size:16px;margin:0 0 24px}
.msk-info div{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #29344b;padding:15px 0;font-size:14px}
.msk-info strong{color:#a2b3cd}.msk-info span{text-align:right;font-weight:700}.msk-info small{display:block;margin-top:22px;color:#9ba9c1;line-height:1.6}
.intro{color:#8195b4;margin:5px 0 19px;font-size:13px}h1{font-size:20px;font-weight:650;margin:0}
@media(max-width:900px){.wrap{grid-template-columns:1fr}.msk-info{position:relative;top:0}.shell{padding:10px}}
</style></head><body><main class="shell"><h1>MSK • Algorithmic Trading Chart</h1>
<p class="intro">Wilder ADX 14 | EMA 20 / 50 | Prior-20 Volume | Deterministic patterns</p>
<div class="wrap"><div class="chart">'''+plot+'''</div>'''+panel+'''</div></main></body></html>'''
    return html,payload
