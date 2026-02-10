"""
╔═══════════════════════════════════════════════════════════════════╗
║   ADVANCED ASI TRADING BOT - PRODUCTION GRADE v2.4                ║
║   Features: Options Strategies | Multibagger Scanner | Research   ║
║   Author: SAVAN KOTAK                      ║
║   Dual-Engine Redundancy: Primary & Secondary AI Engines          ║
║   Auto-Troubleshooting: AI-Powered Error Resolution & GitHub Updates ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import telebot
from telebot import types
import google.generativeai as genai
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
    """Single AI engine using Gemini"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro')
        self.flash_model = genai.GenerativeModel('gemini-1.5-flash')
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
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
            raise  # Re-raise to trigger fallback
    
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
        self.primary = AIEngine(Config.GEMINI_KEY_PRIMARY)
        self.secondary = AIEngine(Config.GEMINI_KEY_SECONDARY)
        self.current_engine = "primary"  # Track which is active
    
    def _switch_engine(self):
        """Switch to secondary if primary fails"""
        if self.current_engine == "primary":
            self.current_engine = "secondary"
            logger.warning("🔄 Switching to Secondary AI Engine due to primary failure.")
        else:
            logger.error("❌ Both AI Engines failed. Functionality limited.")
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
        try:
            return self.primary.generate_research_report(symbol, price, market_data)
        except Exception:
            self._switch_engine()
            try:
                return self.secondary.generate_research_report(symbol, price, market_data)
            except Exception as e:
                return f"⚠️ Both AI engines unavailable: {str(e)}"
    
    def quick_signal(self, symbol: str, price: float) -> str:
        try:
            return self.primary.quick_signal(symbol, price)
        except Exception:
            self._switch_engine()
            try:
                return self.secondary.quick_signal(symbol, price)
            except Exception as e:
                return f"⚠️ Both AI engines unavailable: {str(e)}"
    
    def analyze_multibagger(self, fundamentals: Dict) -> Dict:
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

# (Unchanged from previous version - same as v2.3)

# ═══════════════════════════════════════════════════════════════════
# 5. TELEGRAM BOT INTERFACE WITH DUAL ENGINE
# ═══════════════════════════════════════════════════════════════════

class TradingBot:
    """Telegram Bot for interacting with the trading system"""
    
    def __init__(self):
        self.bot = telebot.TeleBot
