# Technical Chart Integration Guide

## Overview
This guide shows how to integrate `gen_smart_stock_chart.py` with the main Telegram bot (`main.py`).

---

## Files Involved

| File | Purpose |
|------|---------|
| `gen_smart_stock_chart.py` | Generates PNG technical charts with technical analysis |
| `chart_integration.py` | **NEW** — Integration bridge with error handling & caching |
| `main.py` | Main Telegram bot (to be updated with chart commands) |

---

## Quick Start

### 1. Initialize Chart Generator (in main.py)

Add this import and initialization at the top of `main.py`:

```python
from chart_integration import init_chart_generator, chart_command_handler, chart_autoscan_handler

# At startup (in __main__ block):
init_chart_generator(
    script_path="gen_smart_stock_chart.py",
    output_dir="output"
)
```

### 2. Add Chart Command Handler

Add this to `main.py` (in the handlers section, around line 785):

```python
@bot.message_handler(commands=["chart"])
def cmd_chart(message):
    """Generate technical chart for a symbol."""
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(
            message.chat.id,
            "Usage: <code>/chart SYMBOL [Company Name]</code>\n"
            "Example: <code>/chart RELIANCE Reliance Industries</code>\n"
            "Or for auto-scan: <code>/chart scan</code>"
        )
        return
    
    sym = parts[1].upper().replace(".NS", "")
    
    if sym == "SCAN":
        safe_send(message.chat.id, "⏳ Scanning Nifty 200 for best crossover…")
        chart_autoscan_handler(bot, executor, message.chat.id)
    else:
        company_name = " ".join(parts[2:]) if len(parts) > 2 else sym
        safe_send(message.chat.id, f"⏳ Generating chart for {sym}…")
        chart_command_handler(bot, executor, message.chat.id, sym, company_name)
```

### 3. Update Main Menu (Optional)

Add chart button to the main keyboard in `main_keyboard()`:

```python
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "📈 Chart")  # NEW: 📈 Chart
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "💼 Portfolio")
    kb.add("📰 News", "📈 Status")
    return kb

@bot.message_handler(func=lambda m: m.text == "📈 Chart")
def chart_button(message):
    safe_send(
        message.chat.id,
        "📈 <b>Technical Chart Generator</b>\n\n"
        "Type:\n"
        "  <code>/chart RELIANCE</code> — for specific stock\n"
        "  <code>/chart scan</code> — auto-find best crossover"
    )
```

---

## Usage Examples

### From Telegram Bot

```
/chart HDFCBANK
→ Generates chart for HDFC Bank

/chart TCS Tata Consultancy Services
→ Generates chart with custom company name

/chart scan
→ Auto-scans Nifty 200, finds best EMA/SMA crossover, generates chart
```

### Directly in Python

```python
from chart_integration import get_chart_generator

gen = get_chart_generator()

# Direct generation
success, meta, png_path = gen.generate("RELIANCE", "Reliance Industries")
if success:
    print(f"Chart saved: {png_path}")
    print(meta)

# Auto-scan (no symbol)
success, meta, png_path = gen.generate()
```

---

## Output Format

### Text Metadata (sent with chart)

```
📊 RELIANCE — Reliance Industries
🎯 Signal: STRONG BUY  (+5/5)
💰 Entry: ₹2850.25
🛑 SL: ₹2640.50  |  T1: ₹3100.75  |  T2: ₹3350.00
```

### PNG File

- **Location:** `output/smart_stock_chart.png`
- **Resolution:** 150 DPI (high quality)
- **Contains:** Candlestick chart + EMA/SMA + RSI + MACD + Elliott Waves + Fibonacci levels
- **Size:** ~50-200 KB

---

## Features

### Caching
- Charts cached for 1 hour per symbol
- Automatic cache invalidation on TTL expiry
- Prevents redundant API calls

### Error Handling
- Subprocess timeout: 60 seconds
- Graceful fallback to text analysis if chart generation fails
- Detailed error messages for debugging

### Performance
- Runs in background thread (doesn't block bot)
- Parallel processing with executor
- Cache reduces latency for repeated queries

---

## Configuration

Adjust in `chart_integration.py`:

```python
CHART_CACHE_TTL = 3600  # 1 hour — change to cache longer/shorter
CHART_SCRIPT = "gen_smart_stock_chart.py"  # Script path
CHART_OUTPUT_DIR = "output"  # Output directory
```

Or via environment variables (future enhancement):

```bash
export CHART_CACHE_TTL=7200
export CHART_OUTPUT_DIR=/tmp/charts
```

---

## Dependencies

Already in `requirements.txt`:
- `yfinance` — stock data
- `pandas`, `numpy` — data processing
- `matplotlib` — chart rendering
- `mplfinance` — candlestick charts

No additional packages needed.

---

## Troubleshooting

### "Chart script not found"
- Ensure `gen_smart_stock_chart.py` is in the working directory
- Or pass full path: `init_chart_generator(script_path="/path/to/gen_smart_stock_chart.py")`

### "Chart generation timed out"
- Symbol data unavailable or slow API
- Try again in a moment
- Check internet connectivity

### "PNG not found"
- Stock symbol doesn't exist or no 1-year history available
- Verify NSE symbol (e.g., RELIANCE.NS, HDFCBANK.NS)

### "Empty response" from chart generation
- Check if `gen_smart_stock_chart.py` is executable
- Review subprocess error logs

---

## Integration Checklist

- [ ] Copy `chart_integration.py` to repo root
- [ ] Import in `main.py`: `from chart_integration import ...`
- [ ] Initialize in `__main__`: `init_chart_generator()`
- [ ] Add `/chart` command handler (paste code from Step 2 above)
- [ ] (Optional) Add "📈 Chart" button to main menu
- [ ] Test: `/chart RELIANCE` or `/chart scan`
- [ ] Monitor logs for any errors

---

## Production Checklist

- [ ] Chart output directory has write permissions
- [ ] Ensure 500+ MB free disk space for temp files
- [ ] Set up log rotation (charts can be large)
- [ ] Monitor subprocess execution time
- [ ] Cache cleanup via cron job (optional):
  ```bash
  find output/ -name "*.png" -mtime +7 -delete  # Remove >7 days old
  ```

---

## API Reference

### ChartGenerator class

```python
class ChartGenerator:
    def __init__(self, script_path: str, output_dir: str)
    def generate(symbol: Optional[str], company_name: Optional[str]) -> Tuple[bool, str, Optional[str]]
    def send_to_telegram(bot, chat_id: int, symbol: Optional[str], company_name: Optional[str])
```

### Helper Functions

```python
init_chart_generator(script_path, output_dir) -> ChartGenerator
get_chart_generator() -> ChartGenerator
chart_command_handler(bot, executor, chat_id, symbol, company_name)
chart_autoscan_handler(bot, executor, chat_id)
```

---

## Future Enhancements

- [ ] Add chart customization (timeframe, indicators)
- [ ] Batch chart generation for screener results
- [ ] Cloud storage integration (AWS S3)
- [ ] Chart history tracking (compare over time)
- [ ] Premium chart styles (dark mode, custom themes)

---

## Support

For issues:
1. Check bot logs: `grep -i chart logs/bot.log`
2. Enable debug mode: `export LOG_LEVEL=DEBUG`
3. Test directly: `python gen_smart_stock_chart.py RELIANCE "Reliance Industries"`

