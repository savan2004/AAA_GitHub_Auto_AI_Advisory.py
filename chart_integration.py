"""
chart_integration.py — Integration bridge for gen_smart_stock_chart.py
Fixes in this version:
  1. self._period used before assigned — now passed directly in cmd build
  2. period arg correctly forwarded to subprocess
  3. send_to_telegram accepts period arg
  4. Telegram caption capped at 1020 chars
  5. Fallback message sends proper text on failure
"""

import os, sys, subprocess, logging, time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CHART_SCRIPT     = "gen_smart_stock_chart.py"
CHART_OUTPUT_DIR = "output"
CHART_CACHE_TTL  = 3600   # 1 hour per symbol


class ChartGenerator:

    def __init__(self, script_path=CHART_SCRIPT, output_dir=CHART_OUTPUT_DIR):
        self.script_path = script_path
        self.output_dir  = output_dir
        self.cache       = {}
        os.makedirs(output_dir, exist_ok=True)

    def generate(
        self,
        symbol:       Optional[str] = None,
        company_name: Optional[str] = None,
        period:       Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Returns (success, meta_text, png_path).
        period: one of 1mo/3mo/6mo/1y/2y — forwarded to subprocess.
        """
        # ── Cache check ───────────────────────────────────────────────────────
        cache_key = None
        if symbol:
            cache_key = symbol.upper().replace(".NS","") + (period or "")
            cached    = self.cache.get(cache_key)
            if cached and time.time() - cached["ts"] < CHART_CACHE_TTL:
                logger.info(f"[Chart] Cache hit: {cache_key}")
                return True, cached["meta"], cached["path"]

        try:
            # ── Build subprocess command ──────────────────────────────────────
            # FIX: period passed directly here — no self._period needed
            script_abs = os.path.abspath(self.script_path)
            cmd = [sys.executable, script_abs]
            if symbol and company_name:
                cmd.extend([symbol, company_name])
                if period and period in {"1mo","3mo","6mo","1y","2y"}:
                    cmd.append(period)

            cwd = os.path.dirname(script_abs)
            logger.info(f"[Chart] Running: {' '.join(cmd[-3:])} cwd={cwd}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=90,
                text=True,
                cwd=cwd,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if result.returncode != 0:
                # Surface the real error from stderr/stdout
                err = stderr or stdout
                # Strip yfinance noise — find the [ERROR] line
                for line in err.splitlines():
                    if "[ERROR]" in line:
                        err = line
                        break
                logger.error(f"[Chart] Failed (rc={result.returncode}): {err[:200]}")
                return False, f"Chart failed: {err[:200]}", None

            # ── Parse OUTPUT and META lines ───────────────────────────────────
            png_path  = None
            meta_text = ""
            for line in stdout.splitlines():
                if line.startswith("OUTPUT:"):
                    png_path = line.split(":", 1)[1].strip()
                elif line.startswith("META:"):
                    parts = line.split(":", 1)[1].strip().split("|")
                    if len(parts) >= 8:
                        sym, name, signal, score, ltp, sl, t1, t2 = parts[:8]
                        # Clean signal text for display
                        is_wait = "WAIT" in signal.upper()
                        meta_text = (
                            f"<b>{sym} — {name}</b>\n"
                            f"Signal: <b>{signal}</b>  Score: {score}\n"
                            f"Entry: ₹{ltp}\n"
                        )
                        if not is_wait and float(sl) > 0:
                            meta_text += (
                                f"SL: ₹{sl}  |  T1: ₹{t1}  |  T2: ₹{t2}"
                            )
                        else:
                            meta_text += "⏸ No-Trade Zone — wait for ≥12/20 score"

            if not png_path or not os.path.exists(png_path):
                logger.warning(f"[Chart] PNG missing: {png_path!r}")
                return False, "Chart PNG not found — try again.", None

            # ── Cache ─────────────────────────────────────────────────────────
            if cache_key:
                self.cache[cache_key] = {
                    "path": png_path,
                    "meta": meta_text,
                    "ts":   time.time(),
                }

            logger.info(f"[Chart] OK: {png_path}")
            return True, meta_text, png_path

        except subprocess.TimeoutExpired:
            logger.error("[Chart] Timeout >90s")
            return False, "Chart timed out (>90s). Try again or use /chart SYMBOL for a single stock.", None
        except FileNotFoundError as e:
            logger.error(f"[Chart] Script not found: {e}")
            return False, f"Chart script not found: {self.script_path}", None
        except Exception as e:
            logger.error(f"[Chart] Unexpected: {e}", exc_info=True)
            return False, f"Unexpected error: {e}", None

    def send_to_telegram(
        self,
        bot,
        chat_id:      int,
        symbol:       Optional[str] = None,
        company_name: Optional[str] = None,
        period:       Optional[str] = None,
    ):
        """Generate chart and send to Telegram. Run inside executor."""
        success, meta_text, png_path = self.generate(symbol, company_name, period)

        try:
            if success and png_path and os.path.exists(png_path):
                with open(png_path, "rb") as f:
                    caption = f"📈 Technical Analysis\n\n{meta_text}"
                    # Telegram hard limit = 1024 chars
                    if len(caption) > 1020:
                        caption = caption[:1017] + "…"
                    bot.send_photo(
                        chat_id, f,
                        caption=caption,
                        parse_mode="HTML",
                    )
            else:
                # Fallback — readable error, not raw exception text
                msg = meta_text or "⚠️ Chart unavailable right now. Try again in a moment."
                if "403" in msg or "allowlist" in msg.lower():
                    msg = "⚠️ Market data temporarily blocked. Try again in 1–2 minutes."
                bot.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[Chart] Telegram send failed: {e}")
            try:
                bot.send_message(chat_id, "⚠️ Could not send chart. Please try again.")
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────────────────────────
_chart_gen: Optional[ChartGenerator] = None

def get_chart_generator() -> ChartGenerator:
    global _chart_gen
    if _chart_gen is None:
        _chart_gen = ChartGenerator()
        logger.info("[Chart] Generator initialized")
    return _chart_gen
