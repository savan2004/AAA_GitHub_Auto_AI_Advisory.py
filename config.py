"""
SK AUTO AI - Configuration Module
Handles all environment variables and constants
"""

import os
from dotenv import load_dotenv

load_dotenv()

# =============== TELEGRAM CONFIG ===============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN not found in .env or environment")

# =============== OPENAI CONFIG ===============
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_ENABLED = bool(OPENAI_API_KEY)

# =============== RENDER CONFIG ===============
PORT = int(os.getenv("PORT", "10000"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# =============== ASI SCORING WEIGHTS ===============
ASI_WEIGHTS = {
    "trend": 30,
    "momentum": 20,
    "valuation": 10,
    "quality": 10,
    "risk_reward": 10,
    "volatility": 5,
}

# =============== STOCK CATEGORIES ===============
LARGE_CAPS = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY"]
MID_CAPS = ["BAJFINANCE", "MARUTI", "SHREECEM", "DMART", "PIDILITIND"]
SMALL_CAPS = ["NYKAA", "POLYCAB", "METROPOLIS", "CAMS", "AFFLE"]

# =============== TECHNICAL INDICATORS ===============
RSI_PERIOD = 14
EMA_50_PERIOD = 50
EMA_200_PERIOD = 200
VOLATILITY_WINDOW = 20

# =============== ASI THRESHOLDS ===============
ASI_STRONG_BUY = 75
ASI_BUY_HOLD = 55
ASI_WAIT = 35

print("✅ Configuration loaded successfully")
