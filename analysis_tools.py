"""Additional Analysis Tools — Advanced Stock Metrics & Comparisons"""
import pandas as pd
import numpy as np
from technical_indicators import calc_rsi, calc_ema, calc_macd, calc_atr
from data_engine import get_hist, get_info

def get_bollinger_analysis(sym):
    """Tool 1: Bollinger Band Breakout Analysis"""
    try:
        df = get_hist(sym, "6mo")
        if df is None or len(df) < 20:
            return "❌ Insufficient data"
        close = df["Close"]
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        ltp = close.iloc[-1]
        bb_width = ((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_mid.iloc[-1] * 100) if bb_mid.iloc[-1] else 0
        position = ((ltp - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) * 100) if (bb_upper.iloc[-1] - bb_lower.iloc[-1]) > 0 else 50
        return f"📊 Bollinger Bands:\nUpper: ₹{bb_upper.iloc[-1]:.2f} | Mid: ₹{bb_mid.iloc[-1]:.2f} | Lower: ₹{bb_lower.iloc[-1]:.2f}\nBand Width: {bb_width:.1f}% | Position: {position:.0f}%"
    except Exception as e:
        return f"❌ Bollinger error: {str(e)[:50]}"

def get_momentum_analysis(sym):
    """Tool 2: Momentum Strength (Rate of Change)"""
    try:
        df = get_hist(sym, "3mo")
        if df is None or len(df) < 20:
            return "❌ Insufficient data"
        close = df["Close"]
        roc_10 = ((close.iloc[-1] - close.iloc[-11]) / close.iloc[-11] * 100) if len(close) > 10 else 0
        roc_20 = ((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] * 100) if len(close) > 20 else 0
        roc_50 = ((close.iloc[-1] - close.iloc[-51]) / close.iloc[-51] * 100) if len(close) > 50 else 0
        momentum = "🔥 STRONG" if roc_10 > 5 else "📈 Positive" if roc_10 > 0 else "📉 Negative" if roc_10 > -5 else "❄️ WEAK"
        return f"🎯 Momentum (ROC):\n10-day: {roc_10:+.2f}% | 20-day: {roc_20:+.2f}% | 50-day: {roc_50:+.2f}%\nSignal: {momentum}"
    except Exception as e:
        return f"❌ Momentum error: {str(e)[:50]}"

def get_volatility_analysis(sym):
    """Tool 3: Historical Volatility & Breakout Risk"""
    try:
        df = get_hist(sym, "6mo")
        if df is None or len(df) < 20:
            return "❌ Insufficient data"
        close = df["Close"]
        returns = close.pct_change()
        vol_20 = returns.rolling(20).std() * np.sqrt(252) * 100  # Annualized
        vol_50 = returns.rolling(50).std() * np.sqrt(252) * 100
        vol_current = vol_20.iloc[-1]
        vol_avg = vol_20.mean()
        vol_level = "🔴 HIGH" if vol_current > vol_avg * 1.2 else "🟢 LOW" if vol_current < vol_avg * 0.8 else "🟡 NORMAL"
        return f"📊 Volatility Analysis:\n20-day (Ann): {vol_current:.2f}% | 50-day Avg: {vol_avg:.2f}%\nLevel: {vol_level}"
    except Exception as e:
        return f"❌ Volatility error: {str(e)[:50]}"

def get_adx_trend_strength(sym):
    """Tool 4: ADX (Trend Strength) Analysis"""
    try:
        df = get_hist(sym, "6mo")
        if df is None or len(df) < 14:
            return "❌ Insufficient data"
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        plus_dm = (high.diff() > 0) & (high.diff() > low.diff().abs())
        minus_dm = (low.diff().abs() > high.diff()) & (low.diff().abs() > 0)
        tr = np.maximum(high.diff(), np.maximum(close.shift(1).sub(low).abs(), high.sub(low)))
        atr_14 = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).sum() / atr_14
        minus_di = 100 * minus_dm.rolling(14).sum() / atr_14
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(14).mean()
        adx_val = adx.iloc[-1]
        trend = "💪 VERY STRONG" if adx_val > 40 else "📈 STRONG" if adx_val > 25 else "⚖️ MODERATE" if adx_val > 20 else "🌊 WEAK"
        return f"📊 Trend Strength (ADX):\nADX: {adx_val:.2f} | +DI: {plus_di.iloc[-1]:.2f} | -DI: {minus_di.iloc[-1]:.2f}\nTrend: {trend}"
    except Exception as e:
        return f"❌ ADX error: {str(e)[:50]}"

def get_support_resistance(sym):
    """Tool 5: Support & Resistance Levels"""
    try:
        df = get_hist(sym, "3mo")
        if df is None or len(df) < 20:
            return "❌ Insufficient data"
        high = df["High"]
        low = df["Low"]
        ltp = df["Close"].iloc[-1]
        resistance = high.rolling(window=20).max().iloc[-1]
        support = low.rolling(window=20).min().iloc[-1]
        pivot = (resistance + support + ltp) / 3
        r1 = (2 * pivot) - support
        s1 = (2 * pivot) - resistance
        distance_r = ((resistance - ltp) / ltp * 100) if ltp else 0
        distance_s = ((ltp - support) / ltp * 100) if ltp else 0
        return f"🎯 Support & Resistance:\nR2: ₹{resistance:.2f} (+{distance_r:.2f}%)\nPivot: ₹{pivot:.2f}\nS2: ₹{support:.2f} (-{distance_s:.2f}%)\nR1: ₹{r1:.2f} | S1: ₹{s1:.2f}"
    except Exception as e:
        return f"❌ S&R error: {str(e)[:50]}"

def get_volume_analysis(sym):
    """Tool 6: Volume Trend & Confirmation"""
    try:
        df = get_hist(sym, "3mo")
        if df is None or len(df) < 20:
            return "❌ Insufficient data"
        volume = df["Volume"]
        close = df["Close"]
        vol_avg = volume.rolling(20).mean()
        vol_current = volume.iloc[-1]
        price_change = close.pct_change().iloc[-1] * 100
        vol_ratio = (vol_current / vol_avg.iloc[-1]) if vol_avg.iloc[-1] > 0 else 1
        vol_signal = "🔥 HIGH" if vol_ratio > 1.5 else "⬆️ ABOVE AVG" if vol_ratio > 1.0 else "⬇️ BELOW AVG"
        confirmation = "✅ CONFIRMED" if (price_change > 0 and vol_ratio > 1.0) or (price_change < 0 and vol_ratio > 1.0) else "⚠️ WEAK"
        return f"📊 Volume Analysis:\nCurrent: {vol_current:,.0f} | 20-day Avg: {vol_avg.iloc[-1]:,.0f}\nRatio: {vol_ratio:.2f}x | Signal: {vol_signal}\nPrice Confirmation: {confirmation}"
    except Exception as e:
        return f"❌ Volume error: {str(e)[:50]}"

def get_earnings_yield(sym):
    """Tool 7: Earnings Yield vs Bond Yield"""
    try:
        info = get_info(sym) or {}
        pe = info.get("trailingPE")
        if not pe or pe <= 0:
            return "❌ PE data unavailable"
        earnings_yield = (1 / pe) * 100
        bond_yield = 6.5  # Approximate GOI 10Y yield
        yield_diff = earnings_yield - bond_yield
        attractiveness = "🎯 ATTRACTIVE" if yield_diff > 2 else "⚠️ FAIR" if yield_diff > 0 else "❌ UNATTRACTIVE"
        return f"💰 Earnings Yield Analysis:\nEarnings Yield: {earnings_yield:.2f}% | Bond Yield: {bond_yield:.2f}%\nSpread: {yield_diff:+.2f}%\nValuation: {attractiveness}"
    except Exception as e:
        return f"❌ Earnings yield error: {str(e)[:50]}"

def get_dividend_analysis(sym):
    """Tool 8: Dividend Yield & Growth"""
    try:
        info = get_info(sym) or {}
        div_yield = info.get("dividendYield")
        trailing_annual_div = info.get("trailingAnnualDividendRate")
        div_payout = info.get("payoutRatio")
        if not div_yield:
            return "❌ Dividend data unavailable"
        div_yield_pct = div_yield * 100
        risk_level = "🟢 SAFE" if div_payout and div_payout < 50 else "🟡 MODERATE" if div_payout and div_payout < 75 else "🔴 RISKY"
        return f"💵 Dividend Analysis:\nYield: {div_yield_pct:.2f}% | Annual/Share: ₹{trailing_annual_div if trailing_annual_div else 'N/A'}\nPayout Ratio: {div_payout*100 if div_payout else 'N/A'}%\nSustainability: {risk_level}"
    except Exception as e:
        return f"❌ Dividend error: {str(e)[:50]}"

def get_valuation_multiples(sym):
    """Tool 9: Complete Valuation Multiples"""
    try:
        info = get_info(sym) or {}
        pe = info.get("trailingPE")
        pb = info.get("priceToBook")
        ps = info.get("priceToSalesTrailing12Months")
        fcf_yield = info.get("freeCashflowYield")
        pe_status = "🟢 CHEAP" if pe and pe < 15 else "🟡 FAIR" if pe and pe < 20 else "🔴 EXPENSIVE"
        pb_status = "🟢 CHEAP" if pb and pb < 1.5 else "🟡 FAIR" if pb and pb < 3 else "🔴 EXPENSIVE"
        return f"📊 Valuation Multiples:\nP/E: {pe if pe else 'N/A'} {pe_status}\nP/B: {pb if pb else 'N/A'} {pb_status}\nP/S: {ps if ps else 'N/A'}\nFCF Yield: {fcf_yield*100 if fcf_yield else 'N/A'}%"
    except Exception as e:
        return f"❌ Valuation error: {str(e)[:50]}"

def get_peer_comparison(sym):
    """Tool 10: Industry Peer Comparison (Stub)"""
    try:
        # This requires additional data source; stub implementation
        return f"🔍 Peer Comparison (Under Development):\nCompare {sym} with industry peers using Sector Average PE, ROE, Debt/Equity.\nFeature coming soon."
    except Exception as e:
        return f"❌ Peer comparison error: {str(e)[:50]}"
