# Insert after long_term_view() and before # ── News fetch ──────────────────────────────────────────────────────────

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
    """Generate the exact markdown investment-outlook structure requested by the research workflow."""
    if not ai_available():
        return "⚠️ No AI key set. Add GROQ_API_KEY/GEMINI_API_KEY/OPENAI_KEY to enable structured report generation."

    def _safe_val(value, fallback: str = "N/A"):
        if value is None or value == "" or value == "None":
            return fallback
        if isinstance(value, str):
            v = value.strip()
            return v if v else fallback
        return str(value)

    prompt = (
        f"You are an expert Automated Equity Research System specialized in the Indian Stock Markets (NSE & BSE). "
        f"Your role is to ingest raw technical indicators, fundamental valuation ratios, and news headlines for any arbitrary Indian ticker, "
        f"and compile a highly structured, professional, and actionable Investment Outlook Report.\n\n"
        f"### RAW SYSTEM VARIABLE INGESTION:\n"
        f"- [Ticker Name]: {company_name} (NSE/BSE: {symbol})\n"
        f"- [Price Context]: Last Traded Price: Rs. {_safe_val(ltp)}, Day Range: {_safe_val(day_high)} - {_safe_val(day_low)}, 52-Week Range: {_safe_val(y_high)} - {_safe_val(y_low)}\n"
        f"- [Technical Array]: Trend Signal: {_safe_val(trend_signal)}, RSI(14): {_safe_val(rsi_14)}, MACD: {_safe_val(macd_value)}, Exponential Moving Averages (EMA 20: {_safe_val(ema20)}, EMA 50: {_safe_val(ema50)}, EMA 200: {_safe_val(ema200)}), Bollinger Bands: {_safe_val(bb_lower)} - {_safe_val(bb_upper)}\n"
        f"- [Fundamental Array]: Market Cap: {_safe_val(market_cap)} Cr, TTM P/E: {_safe_val(pe_ttm)}, Forward P/E: {_safe_val(pe_forward)}, Price-to-Book (P/B): {_safe_val(price_to_book)}, ROE: {_safe_val(roe_percent)}%, Dividend Yield: {_safe_val(dividend_yield)}%, Debt-to-Equity: {_safe_val(debt_equity)}\n"
        f"- [Relative Benchmark]: Outperformance vs Nifty 50 over 1-year: {_safe_val(nifty_outperformance_pts)} points\n"
        f"- [Raw Scraped News Headlines]:\n{news_bullets or 'No material news headlines available.'}\n\n"
        f"### CORE ANALYTICAL LOGIC BRANCHES:\n"
        "You must strictly account for anomalies in the incoming data stream:\n"
        "1. If any Fundamental Metric (like Forward P/E or Debt/Equity) is passed as \"N/A\" or \"Null\", do not hallucinate metrics. Explicitly state in that sub-section that a true assessment is constrained due to incomplete raw financial listings.\n"
        "2. Cross-reference Technicals vs News: If Technicals read \"BULLISH/OVERBOUGHT\" but recent scraped news outlines structural drops (e.g., \"Net Profit down 50%\"), explicitly highlight this 'Momentum vs Fundamental Divergence' as a high-risk warning.\n\n"
        "### OUTPUT EXPECTED FORMAT (MARKDOWN ONLY):\n\n"
        "---\n"
        "## 📈 Technical Structure & Momentum Spectrum\n"
        "- **Trend Diagnostics:** Interpret the price tracking relative to the EMA 20, 50, and 200. State if the stock is structurally healthy or in a breakdown.\n"
        "- **Oscillator Readings:** Evaluate the RSI({{RSI_14}}). Clearly state if the stock is overextended (Overbought > 70), neutral, or oversold (< 30), and what this means for near-term entry windows.\n"
        "- **Volatility Parameters:** Analyze the width of the Bollinger Bands and whether a price squeeze or expansion is taking place.\n\n"
        "---\n"
        "## 🔎 Fundamental Quality & Valuation Framework\n"
        "- **Pricing Multiple Assessment:** Critique the TTM P/E of {{PE_TTM}} and Price-to-Book of {{PRICE_TO_BOOK}}. Determine if the equity commands an unjustified premium or represents value relative to typical micro/mid/large-cap distributions.\n"
        "- **Balance Sheet Health:** Cross-check the Return on Equity (ROE) against the corporate Debt-to-Equity profile to gauge financial leverage performance.\n\n"
        "---\n"
        "## 📰 Sentiment & Structural Catalyst Correlation\n"
        "- **News Sentiment Mapping:** Synthesize the provided scraped news items. Identify operational catalysts (e.g., product launches, export expansions) vs structural headwinds (earnings degradation, margin erosion).\n"
        "- **Benchmark Contrast:** Leverage the relative alpha calculation ({{NIFTY_OUTPERFORMANCE_PTS}} pts vs Nifty 50) to state if this stock acts as an independent alpha generator or is simply floating on broader index momentum.\n\n"
        "---\n"
        "## 💡 Comprehensive AI Outlook & Guardrails\n"
        "- **Strategic Thesis:** Consolidate the findings into a multi-sentence forward-looking narrative.\n"
        "- **Execution Trajectory:** Define clear, algorithmic target boundaries based on the structural high/low data points provided. Provide an alternative defensive strategy if crucial supports are violated.\n\n"
        "---\n"
        "*Disclaimer: This report is automatically compiled by a programmatic algorithmic pipeline from public market feeds and generative intelligence layer. It does not constitute formal financial, SEBI-registered, or legal investment advice.*"
    )

    system = (
        "You are a precise Indian equities analyst. Use only the numbers supplied. "
        "If a field is N/A or Null, say the assessment is constrained. Output only the exact Markdown structure requested."
    )

    text, err = _call_ai([
        {"role": "user", "content": prompt}
    ], max_tokens=500, system=system)
    if text:
        return text
    return f"⚠️ Structured report unavailable: {err.split(chr(10))[0][:80]}" if err else "⚠️ Structured report temporarily unavailable."


