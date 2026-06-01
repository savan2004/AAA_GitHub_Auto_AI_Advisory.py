"""
chart_integration.py — Integration bridge for gen_smart_stock_chart.py with main bot
Purpose: Provides clean API to generate and send technical charts via Telegram
Features:
  - Async chart generation with caching
  - Direct symbol scanning or AI-optimized picker
  - PNG output to Telegram or file
  - Error handling with fallback text analysis
"""

import os
import sys
import subprocess
import tempfile
import logging
import time
from pathlib import Path
from threading import Thread
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Chart generation paths
CHART_SCRIPT = "gen_smart_stock_chart.py"
CHART_OUTPUT_DIR = "output"
CHART_CACHE_TTL = 3600  # 1 hour cache for same symbol


class ChartGenerator:
    """Manages technical chart generation lifecycle."""
    
    def __init__(self, script_path: str = CHART_SCRIPT, output_dir: str = CHART_OUTPUT_DIR):
        self.script_path = script_path
        self.output_dir = output_dir
        self.cache = {}
        os.makedirs(output_dir, exist_ok=True)
    
    def generate(self, symbol: Optional[str] = None, company_name: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        """
        Generate technical chart for a stock.
        
        Args:
            symbol: NSE symbol (e.g., 'HDFCBANK.NS'). If None, auto-scans best crossover.
            company_name: Human-readable company name (ignored if symbol is None)
        
        Returns:
            (success: bool, message: str, png_path: Optional[str])
        """
        
        # Check cache
        if symbol:
            cache_key = symbol.upper().replace(".NS", "")
            if cache_key in self.cache:
                cached_data = self.cache[cache_key]
                if time.time() - cached_data["ts"] < CHART_CACHE_TTL:
                    return True, cached_data["meta"], cached_data["path"]
        
        try:
            # Build command
            cmd = [sys.executable, self.script_path]
            if symbol and company_name:
                cmd.extend([symbol, company_name])
            
            logger.info(f"[Chart] Generating: {symbol or 'auto-scan'}")
            
            # Run subprocess with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                text=True,
                cwd=os.path.dirname(os.path.abspath(self.script_path)) or "."
            )
            
            if result.returncode != 0:
                err_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"[Chart] Generation failed: {err_msg}")
                return False, f"❌ Chart generation failed: {err_msg}", None
            
            # Parse output
            output_lines = result.stdout.strip().split("\n")
            png_path = None
            meta_text = ""
            
            for line in output_lines:
                if line.startswith("OUTPUT:"):
                    png_path = line.split(":", 1)[1].strip()
                elif line.startswith("META:"):
                    meta_parts = line.split(":", 1)[1].strip().split("|")
                    if len(meta_parts) >= 8:
                        sym, name, signal, score, ltp, sl, t1, t2 = meta_parts[:8]
                        meta_text = (
                            f"📊 <b>{sym} — {name}</b>\n"
                            f"🎯 Signal: <b>{signal}</b>  ({score})\n"
                            f"💰 Entry: ₹{ltp}\n"
                            f"🛑 SL: ₹{sl}  |  T1: ₹{t1}  |  T2: ₹{t2}"
                        )
            
            if not png_path or not os.path.exists(png_path):
                logger.warning(f"[Chart] PNG not found: {png_path}")
                return False, "❌ Chart PNG generation returned no file.", None
            
            # Cache result
            if symbol:
                self.cache[cache_key] = {
                    "path": png_path,
                    "meta": meta_text,
                    "ts": time.time()
                }
            
            logger.info(f"[Chart] Success: {png_path}")
            return True, meta_text, png_path
        
        except subprocess.TimeoutExpired:
            return False, "❌ Chart generation timed out (>60s). Try again later.", None
        except FileNotFoundError:
            return False, f"❌ Chart script not found: {self.script_path}", None
        except Exception as e:
            logger.error(f"[Chart] Error: {e}")
            return False, f"❌ Unexpected error: {str(e)}", None
    
    def send_to_telegram(self, bot, chat_id: int, symbol: Optional[str] = None, company_name: Optional[str] = None):
        """
        Generate chart and send to Telegram (async-safe).
        Use inside executor.submit() to avoid blocking.
        """
        success, meta_text, png_path = self.generate(symbol, company_name)
        
        try:
            if success and png_path:
                # Send chart image
                with open(png_path, "rb") as f:
                    bot.send_photo(
                        chat_id,
                        f,
                        caption=f"<b>📈 Technical Analysis</b>\n\n{meta_text}",
                        parse_mode="HTML"
                    )
            else:
                # Fallback: send text analysis
                bot.send_message(chat_id, meta_text or "❌ Chart generation unavailable right now.")
        except Exception as e:
            logger.error(f"[Chart] Telegram send failed: {e}")
            bot.send_message(chat_id, f"⚠️ Could not send chart: {e}")


# Global instance
_chart_gen = None

def init_chart_generator(script_path: str = CHART_SCRIPT, output_dir: str = CHART_OUTPUT_DIR) -> ChartGenerator:
    """Initialize the global chart generator."""
    global _chart_gen
    _chart_gen = ChartGenerator(script_path, output_dir)
    logger.info("[Chart] Generator initialized")
    return _chart_gen

def get_chart_generator() -> ChartGenerator:
    """Get the global chart generator (init first!)."""
    global _chart_gen
    if _chart_gen is None:
        _chart_gen = init_chart_generator()
    return _chart_gen


# ── Integration helpers for main.py ──────────────────────────────────────────────

def chart_command_handler(bot, executor, chat_id: int, symbol: str, company_name: str = ""):
    """
    Send technical chart via Telegram.
    Call from main.py handlers with executor:
    
    Example:
        executor.submit(chart_command_handler, bot, executor, message.chat.id, "RELIANCE", "Reliance Industries")
    """
    gen = get_chart_generator()
    def _run():
        gen.send_to_telegram(bot, chat_id, symbol, company_name or symbol)
    executor.submit(_run)


def chart_autoscan_handler(bot, executor, chat_id: int):
    """
    Scan Nifty 200 for best crossover and send chart.
    Call from main.py with executor.
    """
    gen = get_chart_generator()
    def _run():
        gen.send_to_telegram(bot, chat_id)  # No args = auto-scan
    executor.submit(_run)
