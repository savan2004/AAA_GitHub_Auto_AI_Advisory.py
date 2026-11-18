"""Pure, evidence-bounded diagnostics from already verified Doctor holding records.

No network, disk, database, orders, risk-profile assumptions or stock recommendations.
All observations are dated-price mathematics, not investment suitability advice.
"""
from decimal import Decimal


def _decimal(value):
    return Decimal(str(value))


def diagnose_portfolio(holdings):
    """Return deterministic portfolio evidence suitable for the 1–5 holding PDF."""
    if not holdings:
        raise ValueError("empty_positions")
    rows = sorted(holdings, key=lambda r: (-_decimal(r["value"]), r["symbol"]))
    total_value = sum((_decimal(row["value"]) for row in rows), Decimal("0"))
    total_cost = sum((_decimal(row["invested"]) for row in rows), Decimal("0"))
    if total_value <= 0 or total_cost <= 0:
        raise ValueError("invalid_totals")
    weights = [_decimal(row["value"]) / total_value for row in rows]
    concentration = sum((w * w for w in weights), Decimal("0"))
    gains = [(r["symbol"], _decimal(r["pnl"])) for r in rows if _decimal(r["pnl"]) > 0]
    losses = [(r["symbol"], _decimal(r["pnl"])) for r in rows if _decimal(r["pnl"]) < 0]
    gains.sort(key=lambda item: (-item[1], item[0]))
    losses.sort(key=lambda item: (item[1], item[0]))
    top = rows[0]
    by_symbol = {}
    high_count = 0
    for r in rows:
        pct = _decimal(r["value"]) * Decimal("100") / total_value
        close, ema20 = _decimal(r["close"]), _decimal(r["ema20"])
        ema50 = r.get("ema50")
        below_50 = ema50 is not None and close < _decimal(ema50)
        rsi = r.get("rsi14")
        flags = []
        if pct > 40:
            flags.append("Above 40% single-stock concentration")
        elif pct > 25:
            flags.append("Above 25% single-stock concentration")
        if below_50:
            flags.append("Last close below EMA50")
        elif close < ema20:
            flags.append("Last close below EMA20")
        if rsi is not None and _decimal(rsi) > 70:
            flags.append("RSI14 above 70; stretched momentum")
        if _decimal(r["pnl"]) < 0:
            flags.append("Unrealised loss versus entered buy cost")
        priority = ("HIGH FLAGS" if pct > 40 or (pct > 25 and below_50)
                    else "WATCH FLAGS" if flags else "NO RULE FLAGS")
        if priority == "HIGH FLAGS":
            high_count += 1
        by_symbol[r["symbol"]] = {
            "priority": priority, "flags": flags, "weight": float(pct),
            "ema20_distance_pct": float(Decimal("100") * (close / ema20 - 1)),
        }
    return {
        "total_cost": total_cost, "total_value": total_value,
        "top_two_pct": float(Decimal("100") * sum(weights[:2])),
        "top_three_pct": float(Decimal("100") * sum(weights[:3])),
        "effective_positions": float(Decimal("1") / concentration),
        "largest_symbol": top["symbol"],
        "largest_pct": float(weights[0] * 100),
        "largest_value": _decimal(top["value"]),
        "largest_ten_percent_stress": _decimal(top["value"]) * Decimal("0.10"),
        "largest_stress_portfolio_pct": float(weights[0] * 10),
        "top_gain": gains[0] if gains else None,
        "top_loss": losses[0] if losses else None,
        "gross_gains": sum((amount for _, amount in gains), Decimal("0")),
        "gross_losses": sum((-amount for _, amount in losses), Decimal("0")),
        "high_flag_count": high_count,
        "by_symbol": by_symbol,
    }


def industry_exposure(holdings, peer_evidence):
    """Weight industry labels only when the verified official roster supplied them."""
    if not isinstance(peer_evidence, dict) or peer_evidence.get("status") != "SCREENED":
        return []
    mapping = peer_evidence.get("held_industries") or {}
    total = sum((_decimal(row["value"]) for row in holdings), Decimal("0"))
    if total <= 0:
        return []
    buckets = {}
    for row in holdings:
        name = row["symbol"]
        group = mapping.get(name)
        if not isinstance(group, str) or not group.strip():
            group = "Unclassified / no official match"
        buckets[group] = buckets.get(group, Decimal("0")) + _decimal(row["value"])
    return sorted(
        ({"industry": name, "weight": float(100 * amount / total)}
         for name, amount in buckets.items()),
        key=lambda item: (-item["weight"], item["industry"]),
    )
