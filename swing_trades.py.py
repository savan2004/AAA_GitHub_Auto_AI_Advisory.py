# portfolio.py - Portfolio suggestions with CFA-style scoring

import yfinance as yf
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

def score_stock(symbol: str) -> Dict:
    """Score a stock from 0-10 based on fundamentals"""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        hist = ticker.history(period="6mo")
        
        if hist.empty:
            return None
        
        close = hist['Close']
        latest = close.iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1]
        
        score = 5.0
        
        # Trend
        if latest > ema200:
            score += 1.5
        else:
            score -= 1.0
        
        # PE ratio
        pe = info.get('trailingPE', 25)
        if pe and pe < 20:
            score += 1.5
        elif pe and pe > 30:
            score -= 1.0
        
        # ROE
        roe = info.get('returnOnEquity', 0.1) * 100
        if roe > 15:
            score += 1.5
        elif roe < 8:
            score -= 1.0
        
        # PB ratio
        pb = info.get('priceToBook', 2)
        if pb < 2:
            score += 0.5
        elif pb > 4:
            score -= 0.5
        
        # Market cap
        mcap = info.get('marketCap', 0)
        if mcap > 50000e7:
            score += 0.5
        elif mcap < 1000e7:
            score -= 0.5
        
        # Dividend
        div = info.get('dividendYield', 0)
        if div and div > 0.02:
            score += 0.5
        
        score = max(0, min(10, score))
        
        rating = "Strong Buy" if score >= 8 else "Buy" if score >= 6 else "Hold" if score >= 4 else "Avoid"
        
        return {
            "symbol": symbol,
            "score": round(score, 1),
            "rating": rating,
            "mcap": mcap,
            "sector": info.get('sector', 'Other')
        }
        
    except Exception as e:
        logger.error(f"Score error for {symbol}: {e}")
        return None

def suggest_portfolio(risk_profile: str = "moderate") -> List[Dict]:
    """Generate portfolio based on risk profile"""
    candidates = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN",
        "BHARTIARTL", "KOTAKBANK", "LT", "WIPRO", "HCLTECH", "ASIANPAINT",
        "MARUTI", "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC"
    ]
    
    scored = []
    for sym in candidates:
        data = score_stock(sym)
        if data and data["score"] >= 4:
            scored.append(data)
    
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    if risk_profile == "conservative":
        filtered = [s for s in scored if s["mcap"] > 10000e7][:6]
    elif risk_profile == "aggressive":
        filtered = [s for s in scored if s["score"] >= 6][:8]
    else:
        filtered = [s for s in scored if s["score"] >= 5][:7]
    
    if not filtered:
        return []
    
    total_score = sum(s["score"] for s in filtered)
    for s in filtered:
        s["allocation"] = round((s["score"] / total_score) * 100, 1)
    
    return filtered

def format_portfolio(portfolio: List[Dict], risk_profile: str) -> str:
    """Format portfolio for display"""
    if not portfolio:
        return "❌ No suitable stocks found for this risk profile."
    
    text = f"💼 <b>Portfolio ({risk_profile.capitalize()} Risk)</b>\n\n"
    
    for item in portfolio:
        text += f"• {item['symbol']} – <b>{item['score']}/10</b> ({item['rating']})\n"
        text += f"  Allocation: {item['allocation']}% | {item.get('sector','N/A')}\n\n"
    
    text += "⚠️ Educational purpose only."
    return text

def get_portfolio_suggestion(risk_profile: str) -> str:
    """Main function to get portfolio suggestion"""
    portfolio = suggest_portfolio(risk_profile)
    return format_portfolio(portfolio, risk_profile)