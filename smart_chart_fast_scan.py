"""Fast bounded sample for the auto-chart button; only real completed daily bars.

Never claims to rank the full Nifty 500 universe or fabricates a BUY signal.
"""
import math
import random


class ChartScanUnavailable(RuntimeError):
    pass


def choose_auto_chart(universe, *, download, scorer, session_key, sample_limit=60):
    """Download daily OHLCV ONCE for a rotating, reproducible NSE sample.

    Return (highest real scorer result, sampled, valid, whole_universe).
    No unverified ticker, stale/empty quote or broad ranking is invented.
    """
    distinct = {}
    for symbol, name in universe:
        if isinstance(symbol, str) and symbol.endswith(".NS") and symbol not in distinct:
            distinct[symbol] = name
    whole = len(distinct)
    if whole == 0:
        raise ChartScanUnavailable("No current NSE chart universe")
    if not isinstance(sample_limit, int) or not 1 <= sample_limit <= 120:
        raise ValueError("Sample size must be between 1 and 120")
    selected = random.Random(str(session_key)).sample(
        list(distinct.items()), min(whole, sample_limit))
    symbols = [s for s, _ in selected]
    try:
        prices = download(
            tickers=symbols, period="6mo", interval="1d", progress=False,
            auto_adjust=True, group_by="ticker", threads=True, timeout=12
        )
    except Exception as exc:
        raise ChartScanUnavailable("Current daily chart data source unavailable") from exc
    if prices is None or getattr(prices, "empty", True):
        raise ChartScanUnavailable("No verified daily chart bars from provider")

    candidates = []
    for symbol, name in selected:
        try:
            if getattr(prices.columns, "nlevels", 1) >= 2:
                if symbol in prices.columns.get_level_values(0):
                    frame = prices[symbol]
                else:
                    frame = prices.xs(symbol, axis=1, level=1)
            elif len(symbols) == 1:
                frame = prices
            else:
                continue
            # Reject stale last sessions even when six months of history exist.
            last_session = frame.dropna(subset=["Close"]).index[-1]
            if str(last_session.date()) != str(session_key):
                continue
            candidate = scorer(symbol, name, df=frame.copy())
            if candidate and (candidate.get("sym") == symbol
                              and math.isfinite(float(candidate.get("close", math.nan)))
                              and math.isfinite(float(candidate.get("score", math.nan)))):
                candidates.append(candidate)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue

    if not candidates:
        raise ChartScanUnavailable("No valid real price bars in bounded chart sample")

    # A real fresh EMA cross is preferred, but do NOT label the sample as a
    # whole-universe winner; full 11-check chart still decides BUY/WAIT/SELL.
    fresh = [c for c in candidates if c.get("cross_dir") != 0]
    winner = max(fresh or candidates, key=lambda c: float(c["score"]))
    return winner, len(selected), len(candidates), whole
