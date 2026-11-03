"""Realistic, uncrowded real-OHLC candlestick example for MSK.

This script uses REAL Yahoo Finance NSE trading-session OHLCV, never synthetic
samples. Raises errors rather than silently generating a fake trading chart.
Only a read-only illustrative research image; no trade execution or bot send.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_chart.engine import analyze
from trading_chart.config import DEFAULT_CONFIG

GREEN="#2DD4BF"
RED="#FB7185"
GOLD="#FBBF24"
BLUE="#60A5FA"
TEXT="#F1F5F9"
MUTED="#9BA8BC"
BACKGROUND="#0B1120"
PANEL="#111A2B"
GRID="#263449"
BORDER="#34445F"
CYAN="#5DD1E4"


def _finite(v) -> float | None:
    try:
        a=float(v)
        return a if math.isfinite(a) else None
    except (TypeError, ValueError):
        return None


def market_data(symbol="RELIANCE.NS"):
    import yfinance as yf
    # Yahoo's session-aware yfinance client is also used by MSK's 401 repair.
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="9mo", interval="1d",
                            auto_adjust=False, actions=False, timeout=15)
    if history is None or len(history) < 110:
        raise RuntimeError("Yahoo Finance returned fewer than 110 real daily bars")
    if isinstance(history.columns, pd.MultiIndex):
        history.columns = history.columns.get_level_values(0)
    cols=["Open","High","Low","Close","Volume"]
    if not set(cols).issubset(set(history.columns)):
        raise RuntimeError("Yahoo Finance missing one of the required OHLCV columns")
    h=history[cols].copy().dropna()
    h=h[(h.Open>0)&(h.High>0)&(h.Low>0)&(h.Close>0)&(h.Volume>=0)]
    if len(h) < 110:
        raise RuntimeError("Insufficient valid Yahoo Finance price+volume candles")
    # Yahoo dates for .NS denote LOCAL market sessions. Converting a
    # midnight IST index to UTC changes the date to the preceding day.
    # Preserve the NSE session calendar DATE, not the UTC instant.
    h_idx = pd.DatetimeIndex(h.index)
    session_dates = (h_idx.tz_convert("Asia/Kolkata").date
                     if h_idx.tz is not None else h_idx.date)
    if session_dates[-1] < (datetime.now(timezone.utc).date()-pd.Timedelta(days=8)):
        raise RuntimeError("Yahoo data unexpectedly stale; refusing realistic current chart")
    d=pd.DataFrame({
        "timestamp":pd.to_datetime(session_dates,utc=True),
        "open":h.Open.astype(float).values,
        "high":h.High.astype(float).values,
        "low":h.Low.astype(float).values,
        "close":h.Close.astype(float).values,
        "volume":h.Volume.astype(float).values,
    })
    q,payload=analyze(d)
    if payload["status"] != "OK":
        raise RuntimeError("Causal quant analysis lacked sufficient verified bars")
    return q,payload


def _tag(ax, x, y, label, color, align="left"):
    ax.annotate(label, (x,y), fontsize=9.3, color=color, va="center",
                ha=align, xytext=(4 if align=="left" else -4,0),
                textcoords="offset points", weight="bold",
                bbox=dict(facecolor=BACKGROUND, edgecolor=color, linewidth=0.8,
                          boxstyle="round,pad=0.31", alpha=0.95),zorder=11)


def make_chart(data,payload,out,company="Reliance Industries",symbol="RELIANCE.NS",visible_bars=82):
    n=len(data)
    start=n-min(visible_bars,n)
    d=data.iloc[start:].reset_index(drop=True)
    count=len(d)
    fmt=lambda v: f"{v:,.2f}"
    last=float(d.close.iloc[-1])
    previous=float(d.close.iloc[-2])
    chg=(last/previous-1)*100
    accent=GREEN if chg>=0 else RED
    adx=payload["adx"]
    vol=payload["vol_multiple"]
    vol_label=payload.get("vol_label") or ""
    trend=payload["trend"]
    pattern=payload.get("pattern") or "No clean pattern"
    signal=payload["signal"]
    quality=payload["quality"]
    asof=pd.Timestamp(d.timestamp.iloc[-1]).strftime("%d %b %Y")
    fetch=datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # Figure layout: price action is ~70% of total height; candles large enough
    # to inspect visually; volume and RSI/MACD not squeezed into thin sub-panels.
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10})
    fig=plt.figure(figsize=(19.2,10.8),dpi=180,facecolor=BACKGROUND)
    ax=fig.add_axes([0.055,0.29,0.690,0.56],facecolor=PANEL)
    vax=fig.add_axes([0.055,0.145,0.690,0.13],sharex=ax,facecolor=PANEL)
    side=fig.add_axes([0.778,0.118,0.203,0.735],facecolor=PANEL)
    side.set_xlim(0,1);side.set_ylim(0,1);side.axis("off")

    for a in (ax,vax):
        for spine in a.spines.values():spine.set_color(BORDER)
        a.grid(True,axis="y",alpha=.55,color=GRID,lw=.65)
        a.tick_params(colors=MUTED,labelsize=10,length=0,pad=8)
    ax.set_ylabel("PRICE  (INR)",color=MUTED,fontsize=9,labelpad=12)
    vax.set_ylabel("VOLUME",color=MUTED,fontsize=9,labelpad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f"₹{x:,.0f}"))
    vax.yaxis.set_major_formatter(FuncFormatter(
        lambda x,pos:f"{x/1e6:.1f}M" if x>=1e6 else f"{x/1e3:.0f}K"))
    plt.setp(ax.get_xticklabels(),visible=False)
    positions=list(range(0,count,10))
    # Never draw consecutive x-axis tick labels at the right edge;
    # replace the previous tick when fewer than six sessions apart.
    if count-1 not in positions:
        if positions and count-1-positions[-1] < 6:
            positions[-1] = count-1
        else:
            positions.append(count-1)
    vax.set_xticks(positions)
    vax.set_xticklabels([pd.Timestamp(d.timestamp.iloc[i]).strftime("%d %b")
                        for i in positions],fontsize=10,color=MUTED)
    ax.set_xlim(-2,count+4)
    top=float(d.high.max());low=float(d.low.min());span=max(top-low,last*.012)
    ax.set_ylim(low-span*.16,top+span*.20)

    # One candle = true session O,H,L,C; no colorizing from indicators.
    bar_colors=[]
    for i,row in d.iterrows():
        positive=row.close>=row.open
        color=GREEN if positive else RED
        bar_colors.append(color)
        ax.vlines(i,row.low,row.high,color=color,lw=1.35,zorder=4)
        body_low=min(row.open,row.close)
        body_height=max(abs(row.close-row.open),span*.0008)
        ax.add_patch(Rectangle((i-.34,body_low),.68,body_height,
                               facecolor=color,edgecolor=color,linewidth=.6,
                               zorder=5,alpha=.96))

    xs=np.arange(count)
    ax.plot(xs,d.ema20.values,color=GOLD,lw=2,label=f"EMA 20   {d.ema20.iloc[-1]:,.1f}",zorder=7)
    ax.plot(xs,d.ema50.values,color=BLUE,lw=2,label=f"EMA 50   {d.ema50.iloc[-1]:,.1f}",zorder=7)
    # Last 42 trading sessions, linear-regression channel: visually subtle.
    bars=min(DEFAULT_CONFIG.channel_bars,count)
    reg_x=np.arange(count-bars,count)
    cc=d.close.values[-bars:]
    a,b=np.polyfit(np.arange(bars),cc,1)
    center=a*np.arange(bars)+b
    stdev=float(np.std(cc-center))
    atr=float(d.atr.iloc[-1])
    width=max(stdev*DEFAULT_CONFIG.channel_band_stdev,atr*DEFAULT_CONFIG.channel_atr_floor)
    upper=center+width;lower=center-width
    ax.plot(reg_x,upper,color=CYAN,lw=1,alpha=.58,ls="--",label="Regression channel")
    ax.plot(reg_x,lower,color=CYAN,lw=1,alpha=.58,ls="--")
    ax.fill_between(reg_x,lower,upper,color=CYAN,alpha=.035,zorder=1)

    candles=payload.get("markers") or []
    for marker in candles[-1:]:
        idx=int(marker["index"])-start
        if 0<=idx<count:
            direction=marker["type"]
            y=d.low.iloc[idx] if direction=="BUY" else d.high.iloc[idx]
            colour=GREEN if direction=="BUY" else RED
            ax.scatter([idx],[y],s=135,c=[colour],
                       marker="^" if direction=="BUY" else "v",
                       edgecolor=BACKGROUND,lw=1.2,zorder=14)
            ax.annotate(direction,(idx,y),xytext=(0,-19 if direction=="BUY" else 18),
                        textcoords="offset points",weight="bold",ha="center",
                        color=colour,fontsize=9,zorder=15)
    anchors=payload.get("pattern_anchors") or []
    for anchor in anchors[-1:]:
        ix=int(anchor["index"])-start
        if 0<=ix<count:
            y=float(d.low.iloc[ix]) if anchor.get("direction")=="BUY" else float(d.high.iloc[ix])
            color=GREEN if anchor.get("direction")=="BUY" else RED
            ax.annotate(f"{anchor['name']} • {anchor['confidence']:.0f}/100",
                        (ix,y),textcoords="offset points",
                        xytext=(0,25 if color==GREEN else -27),fontsize=8.5,
                        ha="center",color=color,
                        bbox=dict(boxstyle="round,pad=.35",fc=BACKGROUND,
                                  ec=color,alpha=.95,lw=.8),zorder=16)
    # Mark latest price, no falsely plotted trade marker.
    ax.axhline(last,ls=":",color=accent,alpha=.55,lw=1)
    _tag(ax,count+1.1,last,f"₹{fmt(last)}",accent)
    legend=ax.legend(loc="upper left",bbox_to_anchor=(.01,.99),frameon=True,
                     facecolor=BACKGROUND,edgecolor=BORDER,labelcolor=TEXT,
                     ncol=3,fontsize=9.8,borderpad=.65)
    legend.get_frame().set_alpha(.93)

    volumes=d.volume.values
    vax.bar(xs,volumes,width=.7,color=bar_colors,alpha=.65,zorder=4)
    volume20=d.avg_volume_prior20.values
    vax.plot(xs,volume20,color="#C4B5FD",lw=1.2,alpha=.9,zorder=5)
    if vol is not None and vol>=2:
        vax.bar([count-1],[volumes[-1]],width=.75,color=GOLD,alpha=.9,zorder=7)
        vax.annotate(f"{vol:.1f}x",(count-1,volumes[-1]),textcoords="offset points",
                     xytext=(0,9),ha="center",color=GOLD,weight="bold",fontsize=9)
    vax.set_ylim(0,max(np.max(volumes),np.max(volume20[np.isfinite(volume20)]) if np.isfinite(volume20).any() else 1)*1.22)

    # Sidebar keeps exact six-field contract, font minimum 12px at 180 DPI.
    side.add_patch(FancyBboxPatch((.015,.015),.97,.97,boxstyle="round,pad=.008,rounding_size=.025",
                                  edgecolor=BORDER,facecolor=PANEL,lw=1.1))
    s_color=GREEN if signal=="BUY" else RED if signal=="SELL" else GOLD
    side.add_patch(FancyBboxPatch((.065,.856),.87,.105,boxstyle="round,pad=.01,rounding_size=.03",
                                  facecolor=BACKGROUND,edgecolor=s_color,lw=1.1))
    side.text(.50,.909,f"{signal}  /  {quality.upper()}",color=s_color,
              size=18,fontweight="bold",ha="center",va="center")
    side.text(.08,.814,"SIGNAL READOUT",color=MUTED,size=10,weight="bold")
    labels=[
        ("TREND",trend),
        ("PATTERN",pattern),
        ("ADX 14",f"{adx:.0f}  {payload['adx_label']}" if adx is not None else "Unavailable"),
        ("VOLUME",f"{vol:.1f}x  {vol_label}" if vol is not None else "Unavailable"),
        ("SIGNAL",signal),
        ("QUALITY",quality),
    ]
    for i,(label,value) in enumerate(labels):
        y=.751-i*.115
        side.plot([.075,.925],[y+.046,y+.046],color=BORDER,lw=.65,alpha=.75)
        side.text(.085,y+.014,label,color=MUTED,size=9.2,weight="bold",va="center")
        color=s_color if label in ("SIGNAL","QUALITY") else TEXT
        if label=="PATTERN" and len(value)>18:
            side.text(.92,y-.019,value[:26],color=color,size=10.2,va="center",ha="right",weight="bold")
        else:
            side.text(.92,y-.019,value,color=color,size=12.2,va="center",ha="right",weight="bold")
    side.text(.50,.038,"No signal without confirmation",ha="center",
              va="center",color=MUTED,size=8.7)

    fig.text(.055,.925,"MSK  |  MARKET STRUCTURE",color=TEXT,
             fontsize=25,weight="bold")
    fig.text(.055,.883,f"{company}    •    NSE: {symbol}    •    DAILY (1D)",
             color=MUTED,fontsize=12.6)
    fig.text(.744,.925,f"₹{fmt(last)}",color=accent,fontsize=23,weight="bold",ha="right")
    fig.text(.744,.888,f"{chg:+.2f}%  •  Close  {asof}",
             color=accent,fontsize=11.5,ha="right")
    fig.text(.055,.080,"REAL NSE HISTORICAL OHLCV   •   Source: Yahoo Finance / yfinance   •   Raw daily candles (unadjusted)",
             color=MUTED,fontsize=9)
    fig.text(.055,.054,f"Fetched {fetch}   |   Chart is educational; research signals are unbacktested and not trading advice",
             color=MUTED,fontsize=8.5)
    fig.text(.71,.50,"MSK RESEARCH",ha="right",color=TEXT,
             alpha=.055,rotation=32,fontsize=42,weight="bold")

    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(out,dpi=180,facecolor=BACKGROUND,
                bbox_inches="tight",pad_inches=.19)
    plt.close(fig)
    return dict(symbol=symbol,company=company,asof=asof,
                last_close=round(last,2),trend=trend,pattern=pattern,
                adx=adx,adx_label=payload["adx_label"],vol_multiple=vol,
                vol_label=vol_label,signal=signal,quality=quality,
                candle_count=count,feed="Yahoo Finance NSE / yfinance 0.2.61",
                fetched_utc=fetch,notes="Real market chart, unverified quant signal accuracy")


def main():
    output=ROOT/"msk_real_chart_example"
    output.mkdir(exist_ok=True)
    print("MSK_REAL_CHART_START",flush=True)
    q,p=market_data()
    result=make_chart(q,p,output/"reliance_professional_candles.png")
    (output/"reliance_professional_candles.json").write_text(
        json.dumps(result,indent=2),encoding="utf-8")
    print("REAL_CHART_JSON",json.dumps(result,sort_keys=True),flush=True)
    print("MSK_REAL_CHART_END",flush=True)


if __name__=="__main__":
    main()
