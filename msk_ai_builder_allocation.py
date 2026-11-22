"""Whole-share allocation of a previously approved batch of NSE research.

The allocator receives approval evidence from its caller. It does not promote
weekly staging data, fetch market data, or create investment suitability scores.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from msk_ai_builder_profile import ResearchProfile

# Provisional model limits from the existing V2 profile design.
EQUITY_CAP = {
    "low": {6: 0, 18: 25, 36: 40, 60: 50},
    "medium": {6: 15, 18: 60, 36: 75, 60: 80},
    "high": {6: 25, 18: 65, 36: 85, 60: 90},
}
_SYMBOL = re.compile(r"[A-Z0-9&-]{2,20}")
_ISIN = re.compile(r"IN[A-Z0-9]{10}")


@dataclass(frozen=True)
class ApprovedCandidate:
    symbol: str
    isin: str
    industry: str
    close_date: date
    close_rupees: Decimal
    score: Decimal


@dataclass(frozen=True)
class Position:
    candidate: ApprovedCandidate
    shares: int

    @property
    def cost(self):
        return self.candidate.close_rupees * self.shares


@dataclass(frozen=True)
class ModelPortfolio:
    profile: ResearchProfile
    close_date: date
    positions: tuple[Position, ...]
    cash: Decimal


def allocate_portfolio(profile, candidates, *, expected_session, risk_history=None):
    """Select and size approved candidates from one expected market session."""
    if not isinstance(profile, ResearchProfile) or type(expected_session) is not date:
        raise ValueError("Invalid allocation profile or session")
    candidates = tuple(candidates)
    if len(candidates) > 20:
        raise ValueError("Approved batch exceeds 20 candidates")

    symbols, identities = set(), set()
    for item in candidates:
        if (not isinstance(item, ApprovedCandidate)
                or not isinstance(item.symbol, str) or not _SYMBOL.fullmatch(item.symbol)
                or not isinstance(item.isin, str) or not _ISIN.fullmatch(item.isin)
                or not isinstance(item.industry, str) or not item.industry.strip()
                or item.close_date != expected_session
                or not isinstance(item.close_rupees, Decimal)
                or not item.close_rupees.is_finite() or item.close_rupees <= 0
                or not isinstance(item.score, Decimal) or not item.score.is_finite()
                or not 0 <= item.score <= 100
                or item.symbol in symbols or item.isin in identities):
            raise ValueError("Invalid or stale approved candidate")
        symbols.add(item.symbol)
        identities.add(item.isin)

    returns = None
    if risk_history is not None:
        from msk_ai_builder_optimizer import validated_returns
        returns = validated_returns(risk_history, symbols, expected_session)

    capital = Decimal(profile.capital_rupees)
    equity_budget = capital * EQUITY_CAP[profile.risk][profile.months] / 100
    stock_cap = capital * (10 if profile.risk == "low" else 15) / 100
    if equity_budget == 0:
        return ModelPortfolio(profile, expected_session, (), capital)

    selected, industries = [], set()
    highest_price = Decimal(0)
    per_stock = Decimal(0)
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.symbol)):
        industry = candidate.industry.strip().casefold()
        next_limit = min(equity_budget / (len(selected) + 1), stock_cap)
        next_highest = max(highest_price, candidate.close_rupees)
        if industry in industries or next_highest > next_limit:
            continue
        highest_price, per_stock = next_highest, next_limit
        selected.append(candidate)
        industries.add(industry)
        if len(selected) == profile.max_stocks:
            break

    if returns is not None and selected:
        from msk_ai_builder_optimizer import minimum_variance_amounts
        try:
            amounts = minimum_variance_amounts(selected, per_stock * len(selected), stock_cap, returns)
        except Exception:
            raise ValueError("Verified optimization unavailable") from None
    else:
        amounts = {item.symbol: per_stock for item in selected}
    positions = tuple(Position(item, int(amounts[item.symbol] // item.close_rupees)) for item in selected)
    return ModelPortfolio(profile, expected_session, positions,
                          capital - sum((row.cost for row in positions), Decimal(0)))
