"""
SK AUTO AI - Market Analysis Engine
Stock reports, market scans, option trades
"""

import json
import re
from datetime import datetime
import yfinance as yf
import pandas as pd

from config import (
    OPENAI_API_KEY,
    AI_ENABLED,
    LARGE_CAPS,
    MID_CAPS,
    SMALL_CAPS,
)
from utils import (
    calculate_rsi,
    calculate_ema,
    calculate_pivots,
    calculate_volatility,
    compute_asi_score,
    get_asi_verdict,
    get_confidence,
    get_trend_direction,
)

try:
    from openai import OpenAI
    if AI_ENABLED and OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        client = None
except ImportError:
    client = None


def find_symbol(query: str) -> str:
    """
    Find NSE symbol from company name.
