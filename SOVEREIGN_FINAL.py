"""
╔═══════════════════════════════════════════════════════════════════╗
║   ADVANCED ASI TRADING BOT - PRODUCTION GRADE v2.7                ║
║   Features: Options Strategies | Multibagger Scanner | Research   ║
║   Author: Enhanced for Professional Trading                       ║
║   Dual-Engine Redundancy: Primary & Secondary AI Engines          ║
║   Auto-Troubleshooting: AI-Powered Error Resolution & GitHub Updates ║
║   Updated: Enhanced AI Report Prompt for Detailed Advisory Output ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import telebot
from telebot import types
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import lru_cache
import logging
import json
import subprocess
import sys
from typing import Dict, List, Tuple, Optional
import threading
import traceback

# Conditional imports with error handling
try:
    import pyotp
except ImportError:
    print("❌ pyotp module not found. Please install it using: pip install pyotp")
    pyotp = None

try:
    from SmartApi import SmartConnect
except ImportError:
    print("❌ SmartApi module not found. Please install it using: pip install SmartApi-python")
    SmartConnect = None

try:
    import git
except ImportError:
    print("❌ git module not found. Please install it using: pip install GitPython")
    git = None

try:
    import google.genai as genai  # Updated to new package
except ImportError:
    print("❌ google.genai module not found. Please install it using: pip install google-genai")
    genai = None

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
    
    # Bot & AI Keys (Load from environment variables) - Now supports two Gemini keys for redundancy
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    GEMINI_KEY_PRIMARY = os.getenv("GEMINI_API_KEY_PRIMARY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")
    GEMINI_KEY_SECONDARY = os.getenv("GEMINI_API_KEY_SECONDARY", "YOUR_SECONDARY_GEMINI_KEY")  # Add a second key
    
    # GitHub Configuration for Auto-Updates
    GITHUB_REPO_PATH = os.getenv("GITHUB_REPO_PATH", "/path/to/your/repo")  # Local path to the cloned repo
    GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
    GITHUB_COMMIT_MESSAGE = "Auto-fix: Resolved error via Backhand AI"
    
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
        if SmartConnect is None:
            logger.error("SmartConnect not available. Please install SmartApi-python.")
            self.api = None
            return
        self.api = SmartConnect(api_key=Config.API_KEY)
        self.session_token = None
        self.session_expiry = None
        self.login()
    
    def login(self) -> bool:
        """Auto-login with TOTP"""
        if pyotp is None or self.api is None:
            logger.error("pyotp or SmartConnect not available.")
            return False
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
        if self.api is None:
            return None
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
        if self.api is None:
            return None
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
# 3. AI ENGINE WITH GEMINI (DUAL-ENGINE REDUNDANCY)
# ═══════════════════════════════════════════════════════════════════

class AIEngine:
    """Single AI engine using updated google.genai"""
    
    def __init__(self, api_key: str):
        if genai is None:
            raise ImportError("google.genai not available")
        self.api_key = api_key
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro')
        self.flash_model = genai.GenerativeModel('gemini-1.5-flash')
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
        """Deep research report with technical analysis - Enhanced for detailed advisory output"""
        prompt = f"""
         **SK AUTO AI ADVISORY**
        
        Asset: {symbol}
        Current Price: ₹{price}
        Date: {datetime.now().strftime('%d-%b-%Y')} | Time: {datetime.now().strftime('%H:%M')}(IST Live)
        
        Generate a comprehensive professional trading advisory report in the exact format below. Use Indian market context. Be data-driven and professional. Include all requested enhancements.
        
        🚀 **SK AUTO AI ADVISORY** 🚀
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📅 **DATE:** {datetime.now().strftime('%d-%b-%Y')} | ⏰ **TIME:** {datetime.now().strftime('%H:%M')}(IST Live)
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🏷 **SYMBOL:** {symbol} | [Full Company Name if available]
        🏛 **ASI RANK:** [Calculate 0-100 based on fundamentals/technicals, e.g., 85/100 (High Confidence)]
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        💰 **LTP:** ₹{price} | 📊 **RSI:** [Current RSI value, e.g., 55.66]
        📈 **TREND:** [BEARISH/BULLISH/NEUTRAL] | 52wk High: [Value] | 52wk Low: [Value] | Trend Pattern: [e.g., Descending Triangle, if possible via pattern finder]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🎯 **VERDICT:** [HOLD/WAIT/BUY/SELL] (Time Frame: [e.g., Short-term 3-6 months])
        🚀 **Short term UPSIDE:** [5-20% up or down] (Time frame: 3-6 Months)
        **Long Term UPSIDE:** [20-100% up or down] (1-3 Years)
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📦 **FUNDAMENTAL LEVELS**
        - Market Cap: [Value Cr] | Sector: [Sector Name]
        - P/E Ratio: [Value]x | ROE: [Value]% | Shareholding Pattern: [Promoter %, FII %, etc.] | Best Value: [Intrinsic value estimate]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🏗 **DEEP TECHNICAL LEVELS**
        SMA 20: [Value] | SMA 50: [Value] | SMA 200: [Value]
        🔴 R3: [Value] | R2: [Value]
        🔴 R1: [Value] | 🟢 PP: [Value]
        🟢 S1: [Value] | S2: [Value] | S3: [Value]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🧠 **COMPANY INFORMATION**
        ✅ **POSITIVE:**
        - [List key positives, including sector strengths, company overview]
        ❌ **NEGATIVE:**
        - [List key negatives, including sector risks, company weaknesses]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📰 **LATEST NEWS:**
        1. [Top News 1 - Summarize based on recent data]
        2. [Top News 2]
        3. [Top News 3]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📝 **CONCLUSION:**
        [Brief summary, e.g., "{symbol} is consolidating. Wait for direction."]
        ⚠️ **RISK:** [Key risks, e.g., Volatility and sector news may impact targets.]
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _AI AUTO ADVISORY - [Add New Smart Tag Lines, e.g., "Invest Wisely, Trade Smartly!"]
        """
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"AI Report Error: {str(e)}")
            raise
    
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
            raise
    
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
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Multibagger Analysis Error: {str(e)}")
            raise

class DualAIEngine:
    """Dual-engine wrapper for redundancy: Primary and Secondary AI Engines"""
    
    def __init__(self):
        try:
            self.primary = AIEngine(Config.GEMINI_KEY_PRIMARY)
            self.secondary = AIEngine(Config.GEMINI_KEY_SECONDARY)
            self.current_engine = "primary"
        except ImportError:
            logger.error("AI Engines not available due to missing google.genai")
            self.primary = None
            self.secondary = None
    
    def _switch_engine(self):
        """Switch to secondary if primary fails"""
        if self.current_engine == "primary" and self.secondary:
            self.current_engine = "secondary"
            logger.warning("🔄 Switching to Secondary AI Engine due to primary failure.")
        else:
            logger.error("❌ Both AI Engines failed or unavailable.")
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
        if not self.primary:
            return "⚠️ AI engines unavailable"
        try:
            return self.primary.generate_research_report(symbol, price, market_data)
        except Exception:
            self._switch_engine()
            try:
                return self.secondary.generate_research_report(symbol, price, market_data)
            except Exception as e:
                return f"⚠️ Both AI engines unavailable: {str(e)}"
    
    def quick_signal(self, symbol: str, price: float) -> str:
        if not self.primary:
            return "⚠️ AI engines unavailable"
        try:
            return self.primary.quick_signal(symbol, price)
        except Exception:
            self._switch_engine()
            try:
                return self.secondary.quick_signal(symbol, price)
            except Exception as e:
                return f"⚠️ Both AI engines unavailable: {str(e)}"
    
    def analyze_multibagger(self, fundamentals: Dict) -> Dict:
        if not self.primary:
            return {}
        try:
            return self.primary.analyze_multibagger(fundamentals)
        except Exception:
            self._switch_engine()
            try:
                return self.secondary.analyze_multibagger(fundamentals)
            except Exception as e:
                logger.error(f"Multibagger Analysis Error on both engines: {str(e)}")
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
        """Iron Condor: Sell OTM call+put, Buy further OTM call
