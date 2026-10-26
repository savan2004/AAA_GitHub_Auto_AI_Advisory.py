"""Evidence-gated, illustrative MSK model portfolios (no real trades).

Each profile selects ten dynamic candidates only when dated, normalized evidence
passes the existing research_upgrade rules. NEVER label a partial list a model.
Model capital is hypothetical; no returns, NAV or performance are simulated.
"""
from collections import Counter
from datetime import datetime, timezone
from html import escape
import math
import os
import re
import time

from research_upgrade import rank, snapshot

MODEL_CAPITAL_RUPEES = 100_000_000  # ₹10 crore, NOT ₹100 crore
MODEL_SIZE = 10
MAX_STOCKS_PER_SECTOR = 2
PROFILES = {
    "conservative": {
        "label": "🏦 CONSERVATIVE",
        "cash_pct": 20,
        "focus": "Quality, leverage control and lower volatility",
    },
    "moderate": {
        "label": "⚖️ MODERATE",
        "cash_pct": 10,
        "focus": "Quality-growth balance and controlled valuation risk",
    },
    "aggressive": {
        "label": "🚀 AGGRESSIVE",
        "cash_pct": 5,
        "focus": "High growth with strict data, sector and liquidity gates",
    },
}


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _validate_evidence(row, rupees_each, now):
    """Do not silently substitute undated/invalid quotations or missing metrics."""
    if not isinstance(row, dict):
        return "invalid candidate"
    for field in ("symbol", "sector", "asof"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            return f"missing {field}"
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", row["symbol"]):
        return "invalid NSE-style ticker"
    if any(word in row["sector"].lower() for word in ("financial", "bank", "insurance")):
        return "financial-sector validation unavailable"
    for field in ("roe", "growth", "debt", "cap", "pe", "vol", "price", "turnover", "fetched_at"):
        if _finite(row.get(field)) is None:
            return f"missing or invalid {field}"
    if row["price"] <= 0 or row["turnover"] <= 0:
        return "invalid price or liquidity"
    fetched = _finite(row["fetched_at"])
    if not 0 <= now - fetched <= 86400:
        return "fundamental snapshot older than 24 hours"
    try:
        candle_date = datetime.strptime(row["asof"], "%d-%b-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return "unparseable trading date"
    age_days = (datetime.fromtimestamp(now, timezone.utc) - candle_date).total_seconds() / 86400
    if not 0 <= age_days <= 7:
        return "historical price is stale or future-dated"
    # 10% participation assumption is only a conservative liquidity screen;
    # no execution/fill price or slippage is promised.
    if row["turnover"] < (rupees_each / 1e7) * 10:
        return "less than 10x hypothetical daily trading value"
    return None


def select_model(rows, profile, now=None, capital_rupees=MODEL_CAPITAL_RUPEES):
    """Pure selector: exactly ten validated stocks or NO portfolio publication."""
    if profile not in PROFILES:
        raise ValueError("Unsupported model")
    if isinstance(capital_rupees, bool) or not isinstance(capital_rupees, int) or capital_rupees <= 0:
        raise ValueError("Capital must be positive whole rupees")
    now = time.time() if now is None else float(now)
    cfg = PROFILES[profile]
    invested_rupees = capital_rupees * (100 - cfg["cash_pct"]) // 100
    one_allocation = invested_rupees // MODEL_SIZE
    rejected = Counter()
    screened = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            rejected["invalid candidate"] += 1
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol in seen:
            rejected["duplicate/invalid symbol"] += 1
            continue
        seen.add(symbol)
        reason = _validate_evidence(row, one_allocation, now)
        if reason:
            rejected[reason] += 1
        else:
            screened.append(row)
    # Existing research_upgrade.rank applies each profile's hard minimum
    # ROE/growth/size, maximum debt/PE/volatility and deterministic score.
    ranked = rank(screened, profile, size=len(screened))
    holdings, sector_count = [], Counter()
    for row in ranked:
        sector = row["sector"]
        if sector_count[sector] >= MAX_STOCKS_PER_SECTOR:
            rejected["sector concentration limit"] += 1
            continue
        sector_count[sector] += 1
        holdings.append(dict(row, allocation_rupees=one_allocation))
        if len(holdings) == MODEL_SIZE:
            break
    complete = len(holdings) == MODEL_SIZE
    return {
        "profile": profile,
        "status": "complete" if complete else "insufficient_verified_candidates",
        "qualified": len(holdings),
        "required": MODEL_SIZE,
        # Never publish a fractional allocation as a complete portfolio.
        "holdings": holdings if complete else [],
        "cash_rupees": capital_rupees - one_allocation * MODEL_SIZE if complete else None,
        "capital_rupees": capital_rupees,
        "rejected": dict(rejected),
        "checked": len(rows),
        "evidence_accepted": len(screened),
    }


def format_model(result, snap):
    """Render Telegram-safe chunks; display no profit/performance claims."""
    cfg = PROFILES[result["profile"]]
    title = cfg["label"]
    checked = snap.get("checked", 0)
    universe = snap.get("universe", 0)
    head = (f"<b>{title} | MSK Research Model</b>\n"
            f"Virtual corpus: ₹{result['capital_rupees']/1e7:,.2f} Cr (not invested)\n"
            f"Universe {universe} | history checked {checked} | eligible {result['evidence_accepted']}\n"
            "Fundamentals must be dated; prices are latest verified historical closes, NOT live LTP.\n")
    if result["status"] != "complete":
        reasons = "; ".join(f"{escape(k)}: {v}" for k, v in sorted(result["rejected"].items()))
        return [head + f"\nOnly {result['qualified']}/{MODEL_SIZE} passed the selection and diversification gates.\n"
                "No ten-stock model or allocation published. Insufficient verified coverage.\n"
                f"Reasons: {reasons[:800] or 'Too few qualified candidates / cache warming'}\n"
                "Retry when verified market and statement data are available."], []
    holdings = result["holdings"]
    messages = [head + f"Cash reserve: ₹{result['cash_rupees']/1e7:,.2f} Cr ({cfg['cash_pct']}%).\n"
                f"Ten holdings, at most {MAX_STOCKS_PER_SECTOR} from one sector.\n"
                "Scores are unbacktested heuristics. Amounts are illustrative risk budgets, not share orders."]
    for start in (0, 5):
        blocks = []
        for number, h in enumerate(holdings[start:start + 5], start + 1):
            blocks.append(
                f"<b>{number}. {escape(h['symbol'])}</b>  | {escape(h['sector'])}\n"
                f"Illustrative allocation ₹{h['allocation_rupees']/1e7:.2f} Cr\n"
                f"Score {h['score']:.1f}/100 | Trend {escape(str(h['timing']))}\n"
                f"ROE {h['roe']:.1f}% | Growth {h['growth']:.1f}% | D/E {h['debt']:.2f}\n"
                f"P/E {h['pe']:.1f} | Annualized volatility {h['vol']:.1f}%\n"
                f"Close ₹{h['price']:,.2f} ({escape(h['asof'])}); 20d turnover ₹{h['turnover']:.1f} Cr/day"
            )
        messages.append("\n\n".join(blocks))
    messages.append("⚠️ Screened research only. No real holdings, executed trades, NAV, "
                    "P&L, returns or forecast. Fundamental filing periods may predate "
                    "the retrieval timestamp. Banks/NBFCs excluded until validated separately.")
    return messages, [h["symbol"] for h in holdings]


def get_research_snapshot():
    """Bounded, cache-first feed using the already present research_upgrade engine.

    Initial presses may return insufficient coverage. We do NOT fake ten picks.
    Up to eight missing quotes and two history series may be refreshed on a click.
    """
    from data_engine import cached_get, get_hist, get_info
    from nifty500_collector import SECTOR_STOCKS

    sectors = [
        sorted(set(stocks))
        for sector, stocks in sorted(SECTOR_STOCKS.items())
        if not any(word in sector.lower() for word in ("banking", "finance", "insurance"))
    ]
    universe = sorted(set(symbol for group in sectors for symbol in group))
    priority = []
    for index in range(max((len(group) for group in sectors), default=0)):
        for group in sectors:
            if index < len(group):
                priority.append(group[index])

    def limited_history(symbols, period, chunk_size=60):
        result, pending = {}, []
        for symbol in symbols:
            history = cached_get(f"hist_{symbol}.NS_{period}", 600)
            if history is not None and not history.empty:
                result[symbol] = history
            else:
                pending.append(symbol)
        try:
            budget = min(2, max(0, int(os.getenv("MODEL_HISTORY_REFRESH_BUDGET", "2"))))
        except ValueError:
            budget = 2
        for symbol in pending[:budget]:
            try:
                frame = get_hist(symbol, period)
                if frame is not None and not frame.empty:
                    result[symbol] = frame
            except Exception:
                pass
        return result

    return snapshot(universe, cached_get, get_info, limited_history, priority=priority)


def run_model(profile):
    snap = get_research_snapshot()
    result = select_model(snap["rows"], profile)
    return format_model(result, snap)
