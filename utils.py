"""
SK AUTO AI - Technical Analysis Utilities
Reusable functions for RSI, Pivots, Volatility, ASI scoring
"""

import pandas as pd
from config import (
    RSI_PERIOD,
    EMA_50_PERIOD,
    EMA_200_PERIOD,
    VOLATILITY_WINDOW,
    ASI_WEIGHTS,
    ASI_STRONG_BUY,
    ASI_BUY_HOLD,
    ASI_WAIT,
)


def calculate_rsi(series: pd.Series, period: int = RSI_PERIOD) -> float:
    """
    Calculate Relative Strength Index (RSI).
    Returns: Float between 0-100
    """
    if len(series) < period + 1:
        return 50.0
    
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        
        rs = avg_gain / (avg_loss.replace(0, 1e-9))
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi.iloc[-1])
    except Exception as e:
        print(f"⚠️ RSI calculation error: {e}")
        return 50.0


def calculate_ema(series: pd.Series, period: int) -> float:
    """
    Calculate Exponential Moving Average.
    Returns: Float price value
    """
    try:
        return float(series.ewm(span=period).mean().iloc[-1])
    except Exception:
        return float(series.iloc[-1])


def calculate_pivots(high: float, low: float, close: float) -> tuple:
    """
    Calculate Pivot Points (PP, R1, S1, R2, S2, R3, S3).
    Returns: Tuple of 7 float values
    """
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    
    return pp, r1, s1, r2, s2, r3, s3


def calculate_volatility(df: pd.DataFrame, window: int = VOLATILITY_WINDOW) -> float:
    """
    Calculate 20-day rolling volatility.
    Returns: Float percentage
    """
    if len(df) < window:
        return 0.0
    
    try:
        volatility = df["Close"].pct_change().rolling(window).std().iloc[-1] * 100
        return float(volatility) if volatility else 0.0
    except Exception:
        return 0.0


def compute_asi_score(
    ltp: float,
    ema_50: float,
    ema_200: float,
    rsi: float,
    pe: float,
    roe: float,
    upside_pct: float,
    volatility: float = None,
) -> int:
    """
    Calculate Advanced Sovereign Intelligence (ASI) Score (0-100).
    
    Components:
    - Trend (30 pts): Price vs EMAs
    - Momentum (20 pts): RSI levels
    - Valuation (10 pts): PE ratio
    - Quality (10 pts): ROE
    - Risk-Reward (10 pts): Upside potential
    - Volatility (±5 pts): Stability
    
    Returns: Integer 0-100
    """
    score = 0
    
    # TREND COMPONENT (0-30 pts)
    if ltp > ema_200:
        score += 30
    elif ltp > ema_50:
        score += 15
    
    # MOMENTUM COMPONENT (0-20 pts)
    if 45 <= rsi <= 60:
        score += 20
    elif (40 <= rsi < 45) or (60 < rsi <= 70):
        score += 10
    elif rsi > 70:
        score += 5
    
    # VALUATION COMPONENT (0-10 pts)
    if pe and pe > 0:
        if pe < 15:
            score += 10
        elif 15 <= pe <= 25:
            score += 5
    
    # QUALITY COMPONENT (0-10 pts)
    if roe and roe > 0:
        if roe >= 18:
            score += 10
        elif 12 <= roe < 18:
            score += 5
    
    # RISK-REWARD COMPONENT (0-10 pts)
    if upside_pct >= 10:
        score += 10
    elif 5 <= upside_pct < 10:
        score += 5
    elif 2 <= upside_pct < 5:
        score += 2
    
    # VOLATILITY ADJUSTMENT (±5 pts)
    if volatility is not None:
        if volatility > 5:
            score -= 5
        elif volatility > 3.5:
            score -= 2
        elif volatility < 1:
            score -= 3
    
    return max(0, min(score, 100))


def get_asi_verdict(asi: int) -> str:
    """
    Get trading verdict based on ASI score.
    Returns: String emoji + verdict
    """
    if asi >= ASI_STRONG_BUY:
        return "📈 STRONG BUY"
    elif asi >= ASI_BUY_HOLD:
        return "✅ BUY/HOLD"
    elif asi >= ASI_WAIT:
        return "⏸️ WAIT"
    else:
        return "🔻 AVOID"


def get_confidence(asi: int) -> str:
    """Get confidence level based on ASI."""
    if asi >= ASI_STRONG_BUY:
        return "High"
    elif asi >= ASI_BUY_HOLD:
        return "Moderate"
    else:
        return "Low"


def get_trend_direction(ltp: float, ema_50: float, ema_200: float) -> str:
    """Determine trend direction."""
    if ltp > ema_200:
        return "BULLISH"
    elif ltp > ema_50:
        return "NEUTRAL"
    else:
        return "BEARISH"
