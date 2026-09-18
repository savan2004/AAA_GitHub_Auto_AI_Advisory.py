# Insert just before the "News fetch" section in ai_engine.py

def structured_investment_outlook(
    symbol: str,
    company_name: str,
    ltp: float,
    day_high: float,
    day_low: float,
    y_high: float,
    y_low: float,
    trend_signal: str,
    rsi_14: float,
    macd_value: float,
    ema20: float,
    ema50: float,
    ema200: float,
    bb_lower: float,
    bb_upper: float,
    market_cap,
    pe_ttm,
    pe_forward,
    price_to_book,
    roe_percent,
    dividend_yield,
    debt_equity,
    nifty_outperformance_pts,
    news_bullets: str,
) -> str:
    """
    Generate a structured, markdown-only Indian equity investment outlook using
    the exact prompt and anomaly-handling logic requested for the advisory system.
    """
    if not ai_available():
        return "⚠️ No AI key set. Add GROQ_API_KEY/GEMINI_API_KEY/OPENAI_KEY to enable structured report generation."

    def _safe_val(value, fallback: str = "N/A"):
        if value is None or value == "" or value == "None":
            return fallback
        if isinstance(value, str):
            value = value.strip()
            return value if value else fallback
        return str(value)

    def _fmt_float(value, digits: int = 2):
        try:
            return f"{float(value):,.{digits}f}"
        except Exception:
            return _safe_val(value)

    prompt = f'''You are an expert Automated Equity Research System specialized in the Indian Stock Markets (NSE & BSE). Your role is to ingest raw technical indicators, fundamental valuation ratios, and news headlines for any arbitrary Indian ticker, and compile a highly structured, professional, and actionable Investment Outlook Report.

### RAW SYSTEM VARIABLE INGESTION:
- [Ticker Name]: {company_name} (NSE/BSE: {symbol})
- [Price Context]: Last Traded Price: Rs. {_safe_val(ltp)}, Day Range: {_safe_val(day_high)} - {_safe_val(day_low)}, 52-Week Range: {_safe_val(y_high)} - {_safe_val(y_low)}
- [Technical Array]: Trend Signal: {_safe_val(trend_signal)}, RSI(14): {_safe_val(rsi_14)}, MACD: {_safe_val(macd_value)}, Exponential Moving Averages (EMA 20: {_safe_val(ema20)}, EMA 50: {_safe_val(ema50)}, EMA 200: {_safe_val(ema200)}), Bollinger Bands: {_safe_val(bb_lower)} - {_safe_val(bb_upper)}
- [Fundamental Array]: Market Cap: {_safe_val(market_cap)} Cr, TTM P/E: {_safe_val(pe_ttm)}, Forward P/E: {_safe_val(pe_forward)}, Price-to-Book (P/B): {_safe_val(price_to_book)}, ROE: {_safe_val(roe_percent)}%, Dividend Yield: {_safe_val(dividend_yield)}%, Debt-to-Equity: {_safe_val(debt_equity)}
- [Relative Benchmark]: Outperformance vs Nifty 50 over 1-year: {_safe_val(nifty_outperformance_pts)} points
- [Raw Scraped News Headlines]:
{news_bullets or 'No material news headlines available.'}

### CORE ANALYTICAL LOGIC BRANCHES:
You must strictly account for anomalies in the incoming data stream:
1. If any Fundamental Metric (like Forward P/E or Debt/Equity) is passed as "N/A" or "Null", do not hallucinate metrics. Explicitly state in that sub-section that a true assessment is constrained due to incomplete raw financial listings.
2. Cross-reference Technicals vs News: If Technicals read "BULLISH/OVERBOUGHT" but recent scraped news outlines structural drops (e.g., "Net Profit down 50%"), explicitly highlight this 'Momentum vs Fundamental Divergence' as a high-risk warning.

### OUTPUT EXPECTED FORMAT (MARKDOWN ONLY):

---
## 📈 Technical Structure & Momentum Spectrum
- **Trend Diagnostics:** Interpret the price tracking relative to the EMA 20, 50, and 200. State if the stock is structurally healthy or in a breakdown.
- **Oscillator Readings:** Evaluate the RSI({_safe_val(rsi_14)}). Clearly state if the stock is overextended (Overbought > 70), neutral, or oversold (< 30), and what this means for near-term entry windows.
- **Volatility Parameters:** Analyze the width of the Bollinger Bands and whether a price squeeze or expansion is taking place.

---
## 🔎 Fundamental Quality & Valuation Framework
- **Pricing Multiple Assessment:** Critique the TTM P/E of {_safe_val(pe_ttm)} and Price-to-Book of {_safe_val(price_to_book)}. Determine if the equity commands an unjustified premium or represents value relative to typical micro/mid/large-cap distributions.
- **Balance Sheet Health:** Cross-check the Return on Equity (ROE) against the corporate Debt-to-Equity profile to gauge financial leverage performance.

---
## 📰 Sentiment & Structural Catalyst Correlation
- **News Sentiment Mapping:** Synthesize the provided scraped news items. Identify operational catalysts (e.g., product launches, export expansions) vs structural headwinds (earnings degradation, margin erosion).
- **Benchmark Contrast:** Leverage the relative alpha calculation ({_safe_val(nifty_outperformance_pts)} pts vs Nifty 50) to state if this stock acts as an independent alpha generator or is simply floating on broader index momentum.

---
## 💡 Comprehensive AI Outlook & Guardrails
- **Strategic Thesis:** Consolidate the findings into a multi-sentence forward-looking narrative.
- **Execution Trajectory:** Define clear, algorithmic target boundaries based on the structural high/low data points provided. Provide an alternative defensive strategy if crucial supports are violated.

---
*Disclaimer: This report is automatically compiled by a programmatic algorithmic pipeline from public market feeds and generative intelligence layer. It does not constitute formal financial, SEBI-registered, or legal investment advice.*'''

    system = (
        "You are a precise Indian equities analyst. Use only the raw values provided and never fabricate missing metrics. "
        "If a field is N/A or Null, explicitly say the assessment is constrained. Output only clean Markdown with the exact section structure requested."
    )
    text, err = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=500,
        system=system,
    )
    if text:
        return text
    return f"⚠️ Structured report unavailable: {err.split(chr(10))[0][:80]}" if err else "⚠️ Structured report temporarily unavailable."


