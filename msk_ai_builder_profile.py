"""Pure input contract for MSK AI Builder V2; no stock advice or I/O."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ResearchProfile:
    capital_rupees: int
    risk: str
    months: int

    def __post_init__(self):
        if type(self.capital_rupees) is not int or not 1 <= self.capital_rupees <= 10_000_000:
            raise ValueError("Capital must be 1 to 10,000,000 rupees")
        if self.risk not in ("low", "medium", "high"):
            raise ValueError("Invalid risk preference")
        if type(self.months) is not int or self.months not in (6, 18, 36, 60):
            raise ValueError("Invalid investment horizon")

    @property
    def max_stocks(self):
        if self.capital_rupees <= 1_000_000:
            return 5
        if self.capital_rupees <= 5_000_000:
            return 10
        return 20

    @property
    def short_horizon(self):
        return self.months <= 18
