# MSK V2.2 Technical Chart Upgrade — audit and implementation

## Existing-code findings

| Module | Before this PR |
|---|---|
| `gen_smart_stock_chart.py` | Standalone interactive Telegram PNG: candles, EMA 9/21/50, SMA 20/50, BB, Fib/Elliott candidates, double patterns, high-volume flags and heuristic 11-check score |
| `report_charts.py` | PDF used a smaller chart: candles, EMA20/50/200, volume, RSI and MACD |
| `technical_indicators.py` | Shared scalar indicators; negative directional movement accidentally used `abs(low.diff())`, which can misread **higher lows** as bearish movement |
| `report_generator.py` | Embeds PDF chart from the same 1-year daily history used for written report, but without drawn trend channel, pattern evidence, volume anomaly labels or ADX panel |

**Do not embed or import `gen_smart_stock_chart.py`** into PDF: it downloads Yahoo data and scans NSE universe at import time. This can mix sources/timestamps and accidentally introduce different calculations. Its pattern detector also uses heuristic confidence values without statistical calibration; do not present these as historical accuracy probabilities.

## New PDF chart pages

1. **Technical Price Setup**: 105-session candle view with 200+ underlying candles required, EMA20/50/200, actual support/resistance, last 60-session linear-regression channel, conditional double top/bottom structure (with neckline and confirmed/unconfirmed labelling), and a marker only for strict confirmed breakout/breakdown conditions.
2. **Momentum and Participation**: volume vs **previous** 20 completed sessions, labelled 2x/3x spikes, Wilder RSI(14), MACD (12/26/9), Wilder ADX(14) and +DI/-DI, with consistent last-data-date labels and neutral/no-trade fallbacks.
3. No auto-generated target return, buy/sell recommendation or win-rate claim. Breakout **observation** requires: close above/below the *previous* 20-session range, EMA20/50 in the same direction, volume >=2.0x preceding 20 sessions, ADX >=20 and DI alignment.
4. If price data are under 200 candles, missing, contradictory or unavailable, the advanced pages fail closed and the **old PDF technical chart** remains as fallback. No network is called by chart generation.
5. ADX movement corrected in the shared library: `+DM=max(current high-previous high,0)` only when dominating `-DM`, `-DM=max(previous low-current low,0)` only when dominating `+DM`. This modifies actual ADX values throughout MSK where they were previously wrong; regression comparison protects the chart/PDF.

## Safety and deployment

- V2 report layout unchanged except **technical chart section**, so Fundamental pages are preserved.
- Uses synthetic OHLCV fixtures in GitHub CI, **never** real-money or Telegram publishing tests.
- **PR only**: no merge/deploy since MSK Render `autoDeploy` is enabled on main.
- Current chart is *technical research* not a backtested prediction. A "WAIT" observation is deliberately expected frequently. Real EOD datasets need independent verification before production deployment.
