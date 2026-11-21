"""Offline executable demonstration. No real tickers, live prices, or recommendations.

The model is a testable rule-based prototype, not trained or backtested AI.
Production use is intentionally unavailable.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import math
from msk_ai_builder_profile import ResearchProfile

@dataclass(frozen=True)
class DemoCompany:
    number: int
    sector: str
    bank: bool
    annual: tuple
    quarters: tuple
    roe: float
    quality: float  # ROCE for non-lenders; capital adequacy for banks
    balance_risk: float  # debt/equity or gross NPA %
    relative_value: float  # valuation multiple / same-sector peer median
    relative_strength: float  # hypothetical excess total return in %
    drawdown: float  # magnitude in %
    overview: str

    @property
    def label(self):
        return f"Fictional Company {self.number:02d}"

@dataclass(frozen=True)
class DemoPosition:
    company: DemoCompany
    score: int
    rupees: int

@dataclass(frozen=True)
class DemoBasket:
    profile: ResearchProfile
    positions: tuple
    reserve: int

def _points(value, tiers):
    return sum(points for threshold, points in tiers if value >= threshold)

def score(company):
    """Transparent provisional rubric; thresholds are not calibrated."""
    if not isinstance(company, DemoCompany) or type(company.number) is not int or not 1 <= company.number <= 100:
        raise ValueError("Only synthetic demo companies supported")
    if len(company.annual) != 5 or len(company.quarters) != 8:
        raise ValueError("Incomplete history")
    vals = (company.roe, company.quality, company.balance_risk,
            company.relative_value, company.relative_strength, company.drawdown)
    if any(type(v) not in (float,int) or not math.isfinite(v) for v in vals):
        raise ValueError("Invalid demo metrics")
    if company.relative_value <= 0 or company.drawdown < 0 or company.balance_risk < 0:
        raise ValueError("Invalid demo metrics")
    for revenue, profit in company.annual + company.quarters:
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (revenue,profit)) or revenue <= 0:
            raise ValueError("Invalid financial history")
    if min(p for _,p in company.annual) <= 0 or company.quarters[-1][1] <= 0:
        return 0
    if company.drawdown > 45 or (company.bank and company.balance_risk > 4) or (not company.bank and company.balance_risk > 1.5):
        return 0
    growth = ((company.annual[-1][0]/company.annual[0][0])**.25-1)*100
    earnings = ((company.annual[-1][1]/company.annual[0][1])**.25-1)*100
    fundamentals = _points(company.roe,((10,8),(15,7))) + _points(company.quality,((12,5),(17,5)))
    growth_score = _points(growth,((5,5),(10,5))) + _points(earnings,((5,5),(10,5)))
    valuation = _points(2-company.relative_value,((.8,10),(1,10)))
    technical = _points(company.relative_strength,((0,5),(5,5),(10,5)))
    risk = _points(-company.drawdown,((-35,5),(-25,5)))
    sector_comparison = _points(-company.balance_risk,(((-3 if company.bank else -1),5),((-2 if company.bank else -.5),5)))
    return fundamentals + growth_score + valuation + technical + risk + sector_comparison

# Hypothetical constraints, subject to subsequent validation.
EQUITY_CAP = {
    "low": {6:0,18:25,36:40,60:50},
    "medium": {6:15,18:60,36:75,60:80},
    "high": {6:25,18:65,36:85,60:90},
}

def build_demo_basket(profile, companies):
    if not isinstance(profile, ResearchProfile):
        raise ValueError("Invalid profile")
    companies = tuple(companies)
    if len({c.number for c in companies}) != len(companies):
        raise ValueError("Duplicate company")
    ranked = sorted(((score(c),c) for c in companies), key=lambda item:(-item[0],item[1].number))
    capital = profile.capital_rupees
    budget = capital * EQUITY_CAP[profile.risk][profile.months] // 100
    per_stock = capital * (10 if profile.risk == "low" else 15) // 100
    per_sector = capital * 25 // 100
    chosen = []
    sectors = {}
    # One candidate per sector per pass, no forced minimum number.
    for points,c in ranked:
        if points < 75 or c.sector in sectors or len(chosen) == profile.max_stocks:
            continue
        if profile.risk == "low" and c.drawdown > 25:
            continue
        sectors[c.sector] = 0
        chosen.append((points,c))
    positions = []
    remaining = budget
    for points,c in chosen:
        amount = min(budget//len(chosen),per_stock,per_sector-sectors[c.sector],remaining)
        if amount <= 0:
            continue
        positions.append(DemoPosition(c,points,amount))
        remaining -= amount
        sectors[c.sector] += amount
    return DemoBasket(profile,tuple(positions),capital-sum(p.rupees for p in positions))

def synthetic_companies():
    sectors = ("Banking","Technology","Consumer","Healthcare","Industrials",
               "Utilities","Insurance","Telecom","Logistics","Materials",
               "Chemicals","Energy","Retail","Automotive","Media",
               "Services","Packaging","Textiles","Engineering","Food")
    return tuple(DemoCompany(i,sector,i==1,
        tuple((1000+i*30+j*160,100+i*3+j*22) for j in range(5)),
        tuple((250+i*8+j*24,25+i+j*3) for j in range(8)),
        18,19,1.2 if i==1 else .25, .9,12,22,
        f"Imaginary {sector.lower()} business, created solely for testing report layout.")
        for i,sector in enumerate(sectors,1))
