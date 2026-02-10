"""
╔═══════════════════════════════════════════════════════════════════╗
║   ADVANCED ASI TRADING BOT - PRODUCTION GRADE v2.1                ║
║   Features: Options Strategies | Multibagger Scanner | Research   ║
║   Author: Enhanced for Professional Trading                       ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import pyotp
import telebot
from telebot import types
from SmartApi import SmartConnect
import google.generativeai as genai
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import lru_cache
import logging
import json
from typing import Dict, List, Tuple, Optional
import threading

# ═══════════════════════════════════════════════════════════════════
# 1. CONFIGURATION & SECURITY
# ═══════════════════════════════════════════════════════════════════

class Config:
    """Centralized configuration management with environment variables for security"""
    # Angel One Credentials (Load from environment variables)
    API_KEY = os.getenv("ANGEL_API_KEY", "C4FHABYE3VUS2JUDB3BAYU44VQ")
    CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "K62380885")
    CLIENT_PIN = os.getenv("ANGEL_CLIENT_PIN", "5252")
    TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "C4FHABYE3VUS2JUDB3BAYU44VQ")
    
    # Bot & AI Keys (Load from environment variables)
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    GEMINI_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")  # Updated with provided key
    
    # Trading Parameters
    CACHE_DURATION = 300  # 5 minutes
    MAX_RETRIES = 3
    TIMEOUT = 30
    SESSION_REFRESH_BUFFER = 300  # 5 minutes before expiry

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('asi_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 2. SMART API WRAPPER WITH AUTO-RECONNECT
# ═══════════════════════════════════════════════════════════════════

class SmartAPIManager:
    """Enhanced SmartAPI with session management and thread safety"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, 'api'):
            return  # Already initialized
        self.api = SmartConnect(api_key=Config.API_KEY)
        self.session_token = None
        self.session_expiry = None
        self.login()
    
    def login(self) -> bool:
        """Auto-login with TOTP"""
        try:
            totp_code = pyotp.TOTP(Config.TOTP_SECRET).now()
            response = self.api.generateSession(
                Config.CLIENT_ID, 
                Config.CLIENT_PIN, 
                totp_code
            )
            
            if response['status']:
                self.session_token = response['data']['jwtToken']
                self.session_expiry = datetime.now() + timedelta(hours=6)
                logger.info("✅ SmartAPI Session Initialized")
                return True
            else:
                logger.error(f"❌ Login Failed: {response.get('message')}")
                return False
                
        except Exception as e:
            logger.error(f"⚠️ Login Exception: {str(e)}")
            return False
    
    def ensure_session(self):
        """Check and refresh session if needed"""
        if not self.session_expiry or datetime.now() >= (self.session_expiry - timedelta(seconds=Config.SESSION_REFRESH_BUFFER)):
            logger.info("🔄 Session expired or expiring soon, refreshing...")
            self.login()
    
    @lru_cache(maxsize=128)
    def get_ltp(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        """Get Last Traded Price with auto-reconnect and caching"""
        self.ensure_session()
        
        for attempt in range(Config.MAX_RETRIES):
            try:
                response = self.api.ltpData(exchange, symbol, token)
                
                if response['status']:
                    return response['data']['ltp']
                elif response.get('errorCode') == 'AG8001':
                    self.login()
                    continue
                else:
                    logger.warning(f"⚠️ LTP Error: {response.get('message')}")
                    return None
                    
            except Exception as e:
                logger.error(f"⚠️ Attempt {attempt + 1} failed: {str(e)}")
                if attempt == Config.MAX_RETRIES - 1:
                    return None
                time.sleep(1)
        
        return None
    
    def get_option_chain(self, symbol: str, expiry: str) -> Optional[pd.DataFrame]:
        """Fetch option chain data with error handling"""
        self.ensure_session()
        try:
            response = self.api.getOptionChain(symbol, expiry)
            if response['status']:
                return pd.DataFrame(response['data'])
            return None
        except Exception as e:
            logger.error(f"Option Chain Error: {str(e)}")
            return None

# ═══════════════════════════════════════════════════════════════════
# 3. AI ENGINE WITH GEMINI
# ═══════════════════════════════════════════════════════════════════

class AIEngine:
    """Advanced AI analysis using Gemini with error handling"""
    
    def __init__(self):
        genai.configure(api_key=Config.GEMINI_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-pro')
        self.flash_model = genai.GenerativeModel('gemini-1.5-flash')
    
    def generate_research_report(self, symbol: str, price: float, 
                                 market_data: Dict) -> str:
        """Deep research report with technical analysis"""
        prompt = f"""
         **ADVANCED RESEARCH REPORT**
        
        Asset: {symbol}
        Current Price: ₹{price}
        Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
        
        Generate a comprehensive professional trading report with:
        
        1. **Market Overview** (50 words)
           - Current trend analysis
           - Key support/resistance levels
        
        2. **Technical Indicators Analysis** (100 words)
           - RSI, MACD, Moving Averages interpretation
           - Momentum and volume analysis
           - Chart patterns identified
        
        3. **Price Targets** (50 words)
           - Short-term targets (1-3 days)
           - Medium-term targets (1-2 weeks)
           - Stop-loss recommendations
        
        4. **Risk Assessment** (50 words)
           - Volatility analysis
           - Key risk factors
           - Risk-reward ratio
        
        5. **Trading Strategy** (50 words)
           - Entry points
           - Exit strategy
           - Position sizing recommendations
        
        6. **Tomorrow's Prediction** (30 words)
           - Expected price range
           - Probability-weighted forecast
           - Key levels to watch
        
        Use Indian market context. Be data-driven and professional.
        """
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"AI Report Error: {str(e)}")
            return "⚠️ AI analysis temporarily unavailable. Please try again."
    
    def quick_signal(self, symbol: str, price: float) -> str:
        """Fast signal generation"""
        prompt = f"""
         Quick Trade Signal for {symbol} at ₹{price}
        
        Provide concise analysis (max 100 words):
        - Buy/Sell/Hold recommendation
        - Key technical indicator (one only)
        - Target price for tomorrow
        - Stop-loss level
        
        Format: Professional, actionable, data-focused.
        """
        
        try:
            response = self.flash_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Quick Signal Error: {str(e)}")
            return f"Quick analysis unavailable: {str(e)}"
    
    def analyze_multibagger(self, fundamentals: Dict) -> Dict:
        """Multibagger stock analysis with 1:20 risk-reward"""
        prompt = f"""
        Analyze this stock for multibagger potential (1:20 risk-reward ratio):
        
        Data: {json.dumps(fundamentals, indent=2)}
        
        Evaluate:
        1. Growth potential (0-10 score)
        2. Financial health (0-10 score)
        3. Market position (0-10 score)
        4. Risk factors (list top 3)
        5. Potential return timeline (months)
        6. Entry price recommendation
        7. Target price (20x return)
        8. Stop-loss level
        
        Return as JSON with these exact keys.
        """
        
        try:
            response = self.model.generate_content(prompt)
            # Parse JSON from response
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Multibagger Analysis Error: {str(e)}")
            return {}

# ═══════════════════════════════════════════════════════════════════
# 4. OPTIONS STRATEGY CALCULATOR
# ═══════════════════════════════════════════════════════════════════

class OptionsCalculator:
    """Advanced options strategy calculations with validation"""
    
    @staticmethod
    def calculate_payoff(strategy: str, spot: float, strikes: List[float], 
                        premiums: List[float]) -> Dict:
        """Calculate payoff for various strategies with input validation"""
        if not strikes or not premiums or len(strikes) != len(premiums):
            return {'error': 'Invalid strikes or premiums provided'}
        
        # Price range for payoff calculation
        price_range = np.linspace(spot * 0.85, spot * 1.15, 100)
        
        strategies = {
            'bull_call_spread': OptionsCalculator._bull_call_spread,
            'bear_put_spread': OptionsCalculator._bear_put_spread,
            'iron_condor': OptionsCalculator._iron_condor,
            'butterfly': OptionsCalculator._butterfly,
            'straddle': OptionsCalculator._straddle,
            'strangle': OptionsCalculator._strangle,
            'call_ratio_spread': OptionsCalculator._call_ratio_spread,
            'put_ratio_spread': OptionsCalculator._put_ratio_spread,
            'jade_lizard': OptionsCalculator._jade_lizard,
            'reverse_iron_condor': OptionsCalculator._reverse_iron_condor
        }
        
        if strategy in strategies:
            try:
                return strategies[strategy](spot, strikes, premiums, price_range)
            except Exception as e:
                logger.error(f"Strategy calculation error for {strategy}: {str(e)}")
                return {'error': f'Calculation failed: {str(e)}'}
        else:
            return {'error': 'Strategy not found'}
    
    @staticmethod
    def _bull_call_spread(spot, strikes, premiums, price_range):
        """Bull Call Spread: Buy lower strike call, Sell higher strike call"""
        if len(strikes) < 2 or len(premiums) < 2:
            raise ValueError("Bull Call Spread requires 2 strikes and 2 premiums")
        buy_strike, sell_strike = strikes[0], strikes[1]
        buy_premium, sell_premium = premiums[0], premiums[1]
        
        net_premium = buy_premium - sell_premium
        payoffs = []
        
        for price in price_range:
            buy_payoff = max(price - buy_strike, 0) - buy_premium
            sell_payoff = -(max(price - sell_strike, 0) - sell_premium)
            payoffs.append(buy_payoff + sell_payoff)
        
        max_profit = (sell_strike - buy_strike) - net_premium
        max_loss = net_premium
        breakeven = buy_strike + net_premium
        
        return {
            'name': 'Bull Call Spread',
            'max_profit': max_profit,
            'max_loss': max_loss,
            'breakeven': breakeven,
            'payoffs': payoffs,
            'price_range': price_range.tolist(),
            'recommendation': 'Use when moderately bullish'
        }
    
    @staticmethod
    def _iron_condor(spot, strikes, premiums, price_range):
        """Iron Condor: Sell OTM call+put, Buy further OTM call+put"""
        if len(strikes) < 4 or len(premiums) < 4:
            raise ValueError("Iron Condor requires 4 strikes and 4 premiums")
        # strikes = [buy_put, sell_put, sell_call, buy_call]
        bp, sp, sc, bc = strikes
        bp_prem, sp_prem, sc_prem, bc_prem = premiums
        
        net_credit = sp_prem + sc_prem - bp_prem - bc_prem
        payoffs = []
        
        for price in price_range:
            put_spread = -(max(sp - price, 0) - sp_prem) + (max(bp - price, 0) - bp_prem)
            call_spread = -(max(price - sc, 0) - sc_prem) + (max(price - bc, 0) - bc_prem)
            payoffs.append(put_spread + call_spread)
        
        max_profit = net_credit
        max_loss = (sp - bp) - net_credit
        
        return {
            'name': 'Iron Condor',
            'max_profit': max_profit,
            'max_loss': max_loss,
            'breakeven_lower': sp - net_credit,
            'breakeven_upper': sc + net_credit,
            'payoffs': payoffs,
            'price_range': price_range.tolist(),
            'recommendation': 'Best for low volatility, range-bound markets'
        }
    
    @staticmethod
    def _butterfly(spot, strikes, premiums, price_range):
        """Long Butterfly: Buy 1 low, Sell 2 mid, Buy 1 high"""
        if len(strikes) < 3 or len(premiums) < 3:
            raise ValueError("Butterfly requires 3 strikes and 3 premiums")
        low, mid, high = strikes
        low_prem, mid_prem, high_prem = premiums
        
        net_debit = low_prem - 2*mid_prem + high_prem
        payoffs = []
        
        for price in price_range:
            p1 = max(price - low, 0) - low_prem
            p2 = -2*(max(price - mid, 0) - mid_prem)
            p3 = max(price - high, 0) - high_prem
            payoffs.append(p1 + p2 + p3)
        
        max_profit = (mid - low) - net_debit
        max_loss = net_debit
        
        return {
            'name': 'Butterfly Spread',
            'max_profit': max_profit,
            'max_loss': max_loss,
            'breakeven': mid,
            'payoffs': payoffs,
            'price_range': price_range.tolist(),
            'recommendation': 'Profit when price stays near middle strike'
        }
    
    @staticmethod
    def _straddle(spot, strikes, premiums, price_range):
        """Long Straddle: Buy ATM call + ATM put"""
        if len(strikes) < 1 or len(premiums) < 2:
            raise ValueError("Straddle requires 1 strike and 2 premiums")
        strike = strikes[0]
        call_prem, put_prem = premiums
        
        total_premium = call_prem + put_prem
        payoffs = []
        
        for price in price_range:
            call_payoff = max(price - strike, 0) - call_prem
            put_payoff = max(strike - price, 0) - put_prem
            payoffs.append(call_payoff + put_payoff)
        
        return {
            'name': 'Long Straddle',
            'max_profit': 'Unlimited',
            'max_loss': total_premium,
            'breakeven_upper': strike + total_premium,
            'breakeven_lower': strike - total_premium,
            'payoffs': payoffs,
            'price_range': price_range.tolist(),
            'recommendation': 'Use when expecting high volatility'
        }
    
    @staticmethod
    def _strangle(spot, strikes, premiums, price_range):
        """Long Strangle: Buy OTM call + OTM put"""
        if len(strikes) < 2 or len(premiums) < 2:
            raise ValueError("Strangle requires 2 strikes and 2 premiums")
        put_strike, call_strike = strikes
        call_prem, put_prem = premiums
        
        total_premium = call_prem + put_prem
        payoffs = []
        
        for price in price_range:
            call_payoff = max(price - call_strike, 0) - call_prem
            put_payoff = max(put_strike - price, 0) - put_prem
            payoffs.append(call_payoff + put_payoff)
        
        return {
            'name': 'Long Strangle',
            'max_profit': 'Unlimited',
            'max_loss': total_premium,
            'breakeven_upper': call_strike + total_premium,
            'breakeven_lower': put_strike - total_premium,
            'payoffs': payoffs,
            'price_range': price_range.tolist(),
            'recommendation': 'Use when expecting high volatility but cheaper than straddle'
        }
    
    # Placeholder implementations for other strategies (can be expanded)
    @staticmethod
    def _bear_put_spread(spot, strikes, premiums, price_range):
        return {'error': 'Bear Put Spread not implemented yet'}
    
    @staticmethod
    def _call_ratio_spread(spot, strikes, premiums, price_range):
        return {'error': 'Call Ratio Spread not implemented yet'}
    
    @staticmethod
    def _put_ratio_spread(spot, strikes, premiums, price_range):
        return {'error': 'Put Ratio Spread not implemented yet'}
    
    @staticmethod
    def _jade_lizard(spot, strikes, premiums, price_range):
        return {'error': 'Jade Lizard not implemented yet'}
    
    @staticmethod
    def _reverse_iron_condor(spot, strikes, premiums, price_range):
        return {'error': 'Reverse Iron Condor not implemented yet'}

# ═══════════════════════════════════════════════════════════════════
# 5. TELEGRAM BOT INTERFACE
# ═══════════════════════════════════════════════════════════════════

class TradingBot:
    """Telegram Bot for interacting with the trading system"""
    
    def __init__(self):
        self.bot = telebot.TeleBot(Config.TELEGRAM_TOKEN)
        self.api_manager = SmartAPIManager()
        self.ai_engine = AIEngine()
        self.options_calc = OptionsCalculator()
        self.setup_handlers()
    
    def setup_handlers(self):
        @self.bot.message_handler(commands=['start'])
        def start(message):
            markup
