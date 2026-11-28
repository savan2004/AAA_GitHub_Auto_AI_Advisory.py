"""Offline foundation for the MSK V4 central data collection gateway.

Not wired to main.py, schedulers, Telegram, databases or network providers.
Core membership must be obtained from the independently validated official
Nifty 500 roster before calling build_universe.
"""
from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping

CORE_LIMIT = 500
EXTRA_LIMIT = 200
TOTAL_LIMIT = CORE_LIMIT + EXTRA_LIMIT
BATCH_LIMIT = 50
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&._-]{0,29}")


def normalize_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid NSE symbol")
    symbol = value.strip().upper()
    if symbol.endswith(".NS"):
        symbol = symbol[:-3]
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid NSE symbol")
    return symbol


@dataclass(frozen=True)
class Universe:
    core: tuple[str, ...]
    extras: tuple[str, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.core + self.extras

    def __post_init__(self):
        if len(self.core) != CORE_LIMIT or len(set(self.core)) != CORE_LIMIT:
            raise ValueError("exactly 500 unique official core symbols required")
        if len(self.extras) > EXTRA_LIMIT:
            raise ValueError("maximum 200 additional symbols")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("duplicate symbols")
        if any(normalize_symbol(s) != s for s in self.symbols):
            raise ValueError("noncanonical symbol")


def build_universe(official_core: Iterable[str], extras: Iterable[str] = ()) -> Universe:
    core = tuple(normalize_symbol(s) for s in official_core)
    core_set = set(core)
    # Deduplicate requested extras, retaining request order. Never silently
    # replace or truncate the official roster or discard over-cap requests.
    additional = tuple(dict.fromkeys(
        symbol for raw in extras
        if (symbol := normalize_symbol(raw)) not in core_set
    ))
    return Universe(core, additional)


class Dataset(str, Enum):
    DAILY_OHLCV = "daily_ohlcv"
    HISTORICAL_OHLCV = "historical_ohlcv"
    INTRADAY = "intraday"
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    RESULTS = "results"
    FINANCIAL_STATEMENTS = "financial_statements"
    SHAREHOLDING = "shareholding"
    CORPORATE_ACTION = "corporate_action"
    SECTOR = "sector"
    NEWS = "news"


@dataclass(frozen=True)
class Batch:
    dataset: Dataset
    symbols: tuple[str, ...]


def plan_missing(universe: Universe,
                 missing_by_dataset: Mapping[Dataset, Iterable[str]],
                 *, batch_size: int = BATCH_LIMIT) -> tuple[Batch, ...]:
    """Build bounded, deduplicated work from caller-verified missing data.

    A caller must decide freshness/period/session and validate stored receipts.
    Technical calculations should be local; this plan does not fetch anything.
    Repeated symbols within a dataset become one request; different datasets
    are deliberately separate. Extras are not automatically refreshed.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= BATCH_LIMIT:
        raise ValueError("batch size must be 1..50")
    allowed = set(universe.symbols)
    batches = []
    for dataset, raw_symbols in missing_by_dataset.items():
        if not isinstance(dataset, Dataset):
            raise ValueError("unknown dataset")
        symbols = tuple(dict.fromkeys(normalize_symbol(s) for s in raw_symbols))
        if not set(symbols).issubset(allowed):
            raise ValueError("symbol outside admitted universe")
        for start in range(0, len(symbols), batch_size):
            batches.append(Batch(dataset, symbols[start:start + batch_size]))
    return tuple(batches)
