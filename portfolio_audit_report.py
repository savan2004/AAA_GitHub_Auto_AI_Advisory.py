"""MSK Portfolio Audit Report - standalone, memory-first Indian-equity PDF.

Install: pip install pandas numpy matplotlib reportlab
Run offline: python portfolio_audit_report.py
Test: python portfolio_audit_report.py --test
Save *synthetic* demo only when explicitly requested:
    python portfolio_audit_report.py --output sample_audit.pdf

Integrate with the MSK Bot using `MSKRepoProvider()` or your own `Provider`:
    report = create_report(holdings, provider=MSKRepoProvider(), language='gu')
    bot.send_document(chat_id, io.BytesIO(report.pdf), ...)

Inputs are never written or logged by this module. All charts/PDFs are BytesIO.
TTL caches contain public *fundamental/industry facts*, never quantities, costs,
user IDs or snapshots. An MSKRepoProvider needs the surrounding existing bot
repository for network adapters; the module itself runs offline without it.

IMPORTANT ASSUMPTIONS / LIMITS (also disclosed in the PDF):
  1. A position represents a single aggregate cost and, if present, one buy
     date. Multi-lot FIFO, fees, brokerage, currency conversion and STT are not
     reconstructed. Buy dates are optional; XIRR/tax are withheld if absent.
  2. Exchange rows must be genuine completed daily OHLCV; official/evidence
     metadata is supplied by the provider, not deduced from a ticker alone.
     Holiday-aware session freshness requires optional holiday dates.
  3. A 40%+ unadjusted one-day price gap suspends return-based analytics; no
     synthetic corporate-action adjustment is ever applied.
  4. Historic performance assumes today's share quantities held throughout;
     it is not realised account performance. Fixed-current-weight covariance
     is a local risk approximation, not a transaction history.
  5. Technical periods: 5/21/63/126/252 exchange observations; EMA20/50/200,
     Wilder RSI14/ATR14; 52W means 252 observed sessions.
  6. Confidence: VERIFIED requires dated exchange/issuer-reconciled evidence;
     dated Screener.in is SECONDARY. Undated or missing fields are MISSING.
  7. No financial source is deemed issuer-authenticated automatically.
  8. Nifty500 official constituents may omit market-cap buckets. In that case
     cap exposures remain unavailable; classification is never invented.
  9. Historic return risk uses equal trading-session intervals, 252 sessions
     per year, historical 5th percentile VaR and mean of worst-5% losses.
     Jensen alpha is shown ONLY as an illustrative 0% risk-free-rate estimate.
 10. Price-only XIRR excludes dividends; optional dividend cash flows are
     separately disclosed, not silently added to cost or XIRR.
 11. Tax is only an indicative unrealised-gain scenario when *all* buy dates
     and an explicitly provided TaxPolicy exist. No annual exemption is
     assumed available across the user's other investments.
 12. Quarterly, shareholding, action, event and peer fields are optional
     sourced inputs. If absent, the PDF prints 'unavailable: reason'.
 13. No transaction instructions, speculative figures or composite ratings are generated.
 14. Only 1-5 holdings; report has exactly 9 + N A4 pages (10-14).
 15. Official market-cap eligibility, ex-date calendars and fiscal statement
     comparability remain provider responsibilities; never inferred by price.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import logging
import math
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape as html_escape
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

LOG = logging.getLogger('msk.portfolio_audit')
IST = timezone(timedelta(hours=5, minutes=30))
NAVY = colors.HexColor('#10253C')
BLUE = colors.HexColor('#176FA7')
RED = colors.HexColor('#B83D49')
AMBER = colors.HexColor('#A6630A')
GREEN = colors.HexColor('#21785C')
PAPER = colors.HexColor('#EFF4F8')
MUTED = colors.HexColor('#617082')
INK = colors.HexColor('#23354B')
LINE = colors.HexColor('#D8E1EA')
WHITE = colors.white
SEVERITY_COLORS = {'HIGH': RED, 'MEDIUM': AMBER, 'LOW': GREEN}
PAGE_W, PAGE_H = A4
CONTENT_W = PAGE_W - 76
MAX_POSITIONS = 5
PERCENT = Decimal('100')


def money(v: Any) -> str:
    """Stable rupee precision in display and all accounting arithmetic."""
    if v is None:
        return 'unavailable'
    try:
        return 'Rs {:,.2f}'.format(Decimal(str(v)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return 'unavailable'


def val(v: Any) -> Optional[float]:
    """Accept strictly finite numeric inputs, never NaN/inf/booleans."""
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def dec(v: Any) -> Decimal:
    """Require a finite amount; reject ambiguous user numeric input."""
    if isinstance(v, bool):
        raise ValueError('boolean amount')
    try:
        x = Decimal(str(v))
        if x.is_finite():
            return x
    except (TypeError, ValueError, InvalidOperation):
        pass
    raise ValueError('non-finite amount')


def pct(v: Optional[float], *, digits: int = 1, signed: bool = False) -> str:
    """Human-readable percentage with explicit missing state."""
    x = val(v)
    if x is None:
        return 'unavailable'
    return (f'{x:+.{digits}f}%' if signed else f'{x:.{digits}f}%')


def unavailable(reason: str) -> str:
    """Use one consistent visible failure marker in every report section."""
    return 'unavailable: ' + str(reason).strip()[:115]


def _str(v: Any, maximum: int = 85) -> str:
    """One-line plain text; prevent accidental injection into PDF markup."""
    return re.sub(r'\s+', ' ', str(v or '')).strip()[:maximum]


@dataclass(frozen=True)
class Holding:
    """A single aggregated position, without a customer identity."""
    symbol: str
    quantity: Decimal
    avg_buy: Decimal
    buy_date: Optional[date] = None

    @staticmethod
    def from_value(raw: Mapping[str, Any]) -> 'Holding':
        sym = str(raw.get('symbol', '')).upper().strip().removeprefix('NSE:')
        sym = sym.removesuffix('.NS')
        if not re.fullmatch(r'[A-Z0-9&-]{2,20}', sym):
            raise ValueError('Invalid NSE symbol')
        quantity = dec(raw.get('quantity', raw.get('qty')))
        buy = dec(raw.get('avg_buy', raw.get('average_buy_price', raw.get('buy'))))
        if not (Decimal('0') < quantity <= Decimal('1e9') and
                Decimal('0') < buy <= Decimal('1e9')):
            raise ValueError('Invalid quantity or buy price')
        acquired = raw.get('buy_date')
        if isinstance(acquired, datetime):
            acquired = acquired.date()
        elif isinstance(acquired, str) and acquired:
            acquired = date.fromisoformat(acquired)
        elif acquired is not None and not isinstance(acquired, date):
            raise ValueError('Invalid buy date')
        return Holding(sym, quantity, buy, acquired)


def validate_holdings(items: Sequence[Holding | Mapping[str, Any]]) -> list[Holding]:
    """Validate count, uniqueness and cost without keeping the input."""
    if not 1 <= len(items) <= MAX_POSITIONS:
        raise ValueError('Portfolio must contain 1-5 symbols')
    rows = [p if isinstance(p, Holding) else Holding.from_value(p) for p in items]
    if len({r.symbol for r in rows}) != len(rows):
        raise ValueError('Duplicate symbols require consolidation outside this module')
    return rows


@dataclass
class PriceSeries:
    """History and exact provenance supplied by a market-data adapter."""
    frame: pd.DataFrame
    source: str
    retrieved_utc: Optional[str] = None
    verified_exchange: bool = False
    adjusted_for_actions: bool = False
    adjustment_reconciled: bool = False


@dataclass
class Dataset:
    """Public research evidence. Never contains a user's holdings/cost basis."""
    stocks: dict[str, PriceSeries] = field(default_factory=dict)
    nifty50: Optional[PriceSeries] = None
    nifty500: Optional[PriceSeries] = None
    constituents: dict[str, dict[str, str]] = field(default_factory=dict)
    constituents_source: str = ''
    constituents_asof: str = ''
    fundamentals: dict[str, dict[str, Any]] = field(default_factory=dict)
    dividends: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    actions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    synthetic: bool = False


class Provider(Protocol):
    """Inject live adapters; all calls are contained by fetch_dataset()."""
    def stock_history(self, symbol: str) -> PriceSeries:
        raise NotImplementedError("Implement this source in a Provider")
    def index_history(self, symbol: str) -> PriceSeries:
        raise NotImplementedError("Implement this source in a Provider")
    def constituents(self) -> Mapping[str, Mapping[str, str]]:
        raise NotImplementedError("Implement this source in a Provider")
    def fundamentals(self, symbol: str) -> Mapping[str, Any]:
        raise NotImplementedError("Implement this source in a Provider")
    def dividends(self, symbol: str) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError("Implement this source in a Provider")
    def actions(self, symbol: str) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError("Implement this source in a Provider")
    def events(self, symbol: str) -> Mapping[str, Any]:
        raise NotImplementedError("Implement this source in a Provider")


class InMemoryTTLCache:
    """Public metadata only: no holdings, trade costs, user IDs, or disk writes."""
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = max(0, int(ttl_seconds))
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get_or_load(self, key: str, load: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = load()
        if self.ttl_seconds:
            with self._lock:
                self._data[key] = (now + self.ttl_seconds, value)
        return value


def fetch_dataset(holdings: list[Holding], provider: Provider,
                  cache: Optional[InMemoryTTLCache] = None) -> Dataset:
    """Isolate every provider error; cache only public labels/fundamentals."""
    result = Dataset(synthetic=bool(getattr(provider, 'is_synthetic', False)))

    def call(key: str, thunk: Callable[[], Any], default: Any,
             public_cache: bool = False) -> Any:
        try:
            value = (cache.get_or_load(key, thunk) if cache and public_cache
                     else thunk())
            if value is None:
                result.errors[key] = 'source returned no data'
                return default
            return value
        except Exception as exc:
            # No ticker, quantity, credential, or client-specific details logged.
            LOG.warning('Public data source unavailable: %s', type(exc).__name__)
            result.errors[key] = 'source access/validation failed'
            return default

    for h in holdings:
        series = call('history:' + h.symbol,
                      lambda sym=h.symbol: provider.stock_history(sym), None)
        if isinstance(series, PriceSeries):
            result.stocks[h.symbol] = series
    for sym, attr in (('^NSEI', 'nifty50'), ('^CRSLDX', 'nifty500')):
        series = call('index:' + sym,
                      lambda symbol=sym: provider.index_history(symbol), None)
        if isinstance(series, PriceSeries):
            setattr(result, attr, series)
    constituents = call('nifty500:constituents', provider.constituents, {}, True)
    if isinstance(constituents, Mapping):
        for raw_sym, raw in constituents.items():
            symbol = str(raw_sym).upper()
            if (re.fullmatch(r'[A-Z0-9&-]{2,20}', symbol) and
                    isinstance(raw, Mapping)):
                result.constituents[symbol] = {
                    'industry': _str(raw.get('industry'), 70),
                    'market_cap_bucket': _str(raw.get('market_cap_bucket'), 25),
                }
        if 450 <= len(result.constituents) <= 550:
            result.constituents_source = ('SYNTHETIC OFFLINE DEMO' if result.synthetic
                                          else 'Nifty 500 index constituent feed')
            # Retrieval is not the index's official effective date.
            result.constituents_asof = ''
        else:
            result.constituents.clear()
            result.errors['nifty500:constituents'] = 'constituent roster incomplete'
    for h in holdings:
        symbol = h.symbol
        result.fundamentals[symbol] = dict(call(
            'fund:' + symbol,
            lambda s=symbol: provider.fundamentals(s), {}, True) or {})
        result.dividends[symbol] = list(call(
            'div:' + symbol, lambda s=symbol: provider.dividends(s), []) or [])
        result.actions[symbol] = list(call(
            'actions:' + symbol,
            lambda s=symbol: provider.actions(s), []) or [])
        result.events[symbol] = dict(call(
            'events:' + symbol, lambda s=symbol: provider.events(s), {}) or {})
    return result


def _normal_date(value: Any) -> Optional[date]:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value:
            return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        pass
    return None


def latest_completed_session(now: datetime, holidays: Sequence[date] = ()) -> date:
    """NSE equity EOD buffer: same day not expected until 17:00 IST."""
    local = now.astimezone(IST)
    d = local.date()
    blocked = set(holidays)
    if local.hour < 17:
        d -= timedelta(days=1)
    while d.weekday() >= 5 or d in blocked:
        d -= timedelta(days=1)
    return d


def trading_lag(last: date, expected: date,
                holidays: Sequence[date] = ()) -> int:
    """Number of expected weekday exchange sessions after last observation."""
    if last >= expected:
        return 0
    blocked = set(holidays)
    start = last + timedelta(days=1)
    count = 0
    while start <= expected:
        if start.weekday() < 5 and start not in blocked:
            count += 1
        start += timedelta(days=1)
    return count


def clean_history(series: Optional[PriceSeries], expected: date,
                  holidays: Sequence[date] = ()) -> tuple[Optional[pd.DataFrame], dict[str, Any]]:
    """Drop bad rows, block unadjusted >40% gaps, disclose provenance/lag."""
    meta = {'confidence': 'MISSING', 'reason': 'OHLCV not supplied',
            'age_sessions': None, 'bad_rows': 0, 'gap': None,
            'source': '', 'asof': None, 'adjustments_confirmed': False}
    if not isinstance(series, PriceSeries) or not isinstance(series.frame, pd.DataFrame):
        return None, meta
    meta['source'] = _str(series.source)
    try:
        df = series.frame.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'Date' not in df.columns:
                meta['reason'] = 'missing trading dates'
                return None, meta
            df.index = pd.to_datetime(df.pop('Date'), errors='coerce')
        if df.index.tz is not None:
            df.index = df.index.tz_convert(IST).tz_localize(None)
        df.index = df.index.normalize()
        df = df[~df.index.isna()]
        if df.index.has_duplicates:
            meta['reason'] = 'duplicate trading sessions'
            return None, meta
        if not df.index.is_monotonic_increasing:
            meta['reason'] = 'out-of-order trading sessions'
            return None, meta
        required = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not set(required).issubset(df.columns):
            meta['reason'] = 'incomplete OHLCV columns'
            return None, meta
        df = df[required]
        for col in required:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        valid = np.isfinite(df.to_numpy(dtype=float)).all(axis=1)
        valid &= (df[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1).to_numpy()
        valid &= (df['Volume'] >= 0).to_numpy()
        valid &= (df['High'] >= df[['Open', 'Low', 'Close']].max(axis=1)).to_numpy()
        valid &= (df['Low'] <= df[['Open', 'High', 'Close']].min(axis=1)).to_numpy()
        valid &= (df.index.date <= expected)
        meta['bad_rows'] = int(len(df) - valid.sum())
        df = df.loc[valid].tail(510)
        if len(df) < 15:
            meta['reason'] = 'fewer than 15 valid daily sessions'
            return None, meta
        last = df.index[-1].date()
        meta['asof'] = last.isoformat()
        meta['age_sessions'] = trading_lag(last, expected, holidays)
        if meta['age_sessions'] > 3:
            meta['reason'] = f'stale by {meta["age_sessions"]} trading days (>3)'
            return df, meta
        change = df['Close'].pct_change().abs().dropna()
        gaps = change[change > 0.40]
        if not gaps.empty:
            dt = gaps.index[-1].date().isoformat()
            magnitude = float(gaps.iloc[-1] * 100)
            meta['gap'] = (dt, magnitude)
            if not (series.adjusted_for_actions and series.adjustment_reconciled):
                meta['reason'] = f'unadjusted possible split/bonus gap {magnitude:.1f}% on {dt}'
                return df, meta
        if not series.source or not series.retrieved_utc:
            meta['reason'] = 'source or retrieval time missing'
            return df, meta
        if series.verified_exchange and meta['age_sessions'] == 0:
            meta['confidence'] = 'VERIFIED'
        else:
            meta['confidence'] = 'SECONDARY'
        meta['reason'] = ('completed NSE session' if meta['age_sessions'] == 0
                          else f'price delayed by {meta["age_sessions"]} trading days')
        return df, meta
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        LOG.warning('History validation: %s', type(exc).__name__)
        meta['reason'] = 'unusable historical candles'
        return None, meta


def ema(series: pd.Series, period: int) -> Optional[float]:
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1]) if len(series) >= period else None


def rsi14(close: pd.Series) -> Optional[float]:
    """Wilder RSI14 with a recursive Welles-Wilder smoothing seed."""
    if len(close) < 15:
        return None
    diff = close.diff().dropna()
    avg_gain = float(diff.iloc[:14].clip(lower=0).mean())
    avg_loss = float((-diff.iloc[:14]).clip(lower=0).mean())
    for move in diff.iloc[14:]:
        avg_gain = (avg_gain * 13 + max(float(move), 0.0)) / 14
        avg_loss = (avg_loss * 13 + max(-float(move), 0.0)) / 14
    if avg_gain == avg_loss == 0:
        return 50.0
    return 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)


def atr14_pct(frame: pd.DataFrame) -> Optional[float]:
    """Simple 14-period true range mean / last price."""
    if len(frame) < 15:
        return None
    prev = frame['Close'].shift(1)
    tr = pd.concat([(frame['High']-frame['Low']),
                    (frame['High']-prev).abs(),
                    (frame['Low']-prev).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-14:].mean() / frame['Close'].iloc[-1] * 100)


def max_drawdown(values: Sequence[float]) -> float:
    """Peak-to-trough drawdown for a positive observation sequence."""
    a = np.asarray(values, dtype=float)
    if len(a) < 2 or not np.isfinite(a).all() or (a <= 0).any():
        raise ValueError('Invalid valuation path')
    return float(np.min(a / np.maximum.accumulate(a) - 1))


def annual_volatility(daily_returns: Sequence[float]) -> float:
    """Annualised standard deviation, decimal units (not percentage)."""
    a = np.asarray(daily_returns, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        raise ValueError('Insufficient returns')
    return float(np.std(a, ddof=1) * math.sqrt(252))


def beta_against(returns: Sequence[float], benchmark: Sequence[float]) -> float:
    """Sample covariance divided by benchmark variance; dates pre-aligned."""
    p, b = np.asarray(returns, float), np.asarray(benchmark, float)
    if p.size != b.size or p.size < 3 or not np.isfinite(p).all() or not np.isfinite(b).all():
        raise ValueError('Nonaligned benchmark observations')
    denominator = float(np.var(b, ddof=1))
    if denominator < 1e-15:
        raise ValueError('Zero benchmark variance')
    return float(np.cov(p, b, ddof=1)[0, 1] / denominator)


def var_and_es(returns: Sequence[float], confidence: float = .95) -> tuple[float, float]:
    """Historical loss VaR/expected shortfall as positive decimal fractions."""
    a = np.asarray(returns, float)
    a = a[np.isfinite(a)]
    if len(a) < 40 or not .50 < confidence < 1:
        raise ValueError('Not enough tail observations')
    tail_cut = float(np.quantile(a, 1-confidence))
    return max(0., -tail_cut), max(0., -float(a[a <= tail_cut].mean()))


def hhi(weights: Sequence[float]) -> float:
    """Concentration HHI on unit fractions, not on 0-100 percentages."""
    a = np.asarray(weights, float)
    if not len(a) or (a < 0).any() or not np.isclose(a.sum(), 1., atol=1e-8):
        raise ValueError('Weights must sum to one')
    return float(np.dot(a, a))


def stress_scenarios(value: float, holding_values: Mapping[str, float]) -> dict[str, dict[int, float]]:
    """Loss in rupees for each stock and entire portfolio at -10/-20/-30%."""
    result = {sym: {k: float(pos) * k / 100
                    for k in (10, 20, 30)}
              for sym, pos in holding_values.items()}
    result['ALL POSITIONS'] = {k: float(value) * k / 100 for k in (10, 20, 30)}
    return result


def _ret(frame: pd.DataFrame, periods: int) -> Optional[float]:
    if frame is None or len(frame) < periods+1:
        return None
    return float(frame['Close'].iloc[-1] / frame['Close'].iloc[-periods-1] - 1)



def paired_trailing(stock: Optional[pd.DataFrame],
                    index: Optional[pd.DataFrame],
                    sessions: int) -> tuple[Optional[float],Optional[float]]:
    """Return aligned stock and Nifty history on exactly matching dates."""
    if stock is None or index is None:
        return None,None
    aligned=pd.concat([stock['Close'].rename('stock'),
                       index['Close'].rename('nifty50')],axis=1,join='inner').dropna()
    if (len(aligned)<sessions+1 or aligned.index[-1] != stock.index[-1]
            or aligned.index[-1] != index.index[-1]):
        return None,None
    a=float(aligned['stock'].iloc[-1]/aligned['stock'].iloc[-sessions-1]-1)
    b=float(aligned['nifty50'].iloc[-1]/aligned['nifty50'].iloc[-sessions-1]-1)
    return a,b


def _swing_levels(close: pd.Series, left: int = 3) -> tuple[Optional[float], Optional[float]]:
    """Latest local swing lows/highs, strictly retrospective. Not targets."""
    if len(close) < 2*left+1:
        return None, None
    a = close.to_numpy(dtype=float)
    low = [a[i] for i in range(left, len(a)-left)
           if a[i] <= min(a[i-left:i]) and a[i] < min(a[i+1:i+left+1])]
    high = [a[i] for i in range(left, len(a)-left)
            if a[i] >= max(a[i-left:i]) and a[i] > max(a[i+1:i+left+1])]
    return (float(low[-1]) if low else None,
            float(high[-1]) if high else None)


def _quarterly_valid(records: Any, max_count: int = 8) -> list[dict[str, Any]]:
    """Only accept period-labelled numeric quarterly facts."""
    result = []
    if not isinstance(records, (list, tuple)):
        return result
    for row in records[-max_count:]:
        if (not isinstance(row, Mapping) or not _normal_date(row.get('period'))
                or not str(row.get('source') or '').strip()):
            continue
        rev, profit, margin = val(row.get('revenue')), val(row.get('profit')), val(row.get('margin'))
        if rev is None or profit is None:
            continue
        result.append({'period': str(row['period'])[:10],
                       'revenue': rev, 'profit': profit, 'margin': margin,
                       **{key:row.get(key) for key in ('source','source_url','scope','unit','basis','retrieved_utc')}})
    return result


def _evidence(raw: Any) -> dict[str, Any]:
    """Tag individual facts with date + source; secondary never auto-verified."""
    if not isinstance(raw, Mapping):
        return {'value': None, 'confidence': 'MISSING', 'reason': 'no source evidence'}
    n = val(raw.get('value'))
    d = _normal_date(raw.get('period') or raw.get('asof'))
    src = _str(raw.get('source'), 35)
    if n is None or not d or not src:
        return {'value': None, 'confidence': 'MISSING',
                'reason': 'number, fiscal period or source missing'}
    verified = bool(raw.get('issuer_reconciled', False)) and 'screener' not in src.lower()
    return {'value': n, 'confidence': 'VERIFIED' if verified else 'SECONDARY',
            'source': src, 'period': d.isoformat(),
            'retrieved': _str(raw.get('retrieved_utc'), 30) or 'not provided',
            'scope':raw.get('scope'), 'basis':raw.get('basis'),
            'unit':raw.get('unit'), 'source_url':raw.get('source_url')}


def financial_value(fund: Mapping[str, Any], field_name: str) -> Optional[float]:
    v = _evidence((fund or {}).get('metrics', {}).get(field_name, {}))
    return v.get('value')


def _number_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    return numerator/denominator if numerator is not None and denominator and denominator > 0 else None


def _xirr(rows: Sequence[Holding], marks: Mapping[str, float], terminal: date) -> Optional[float]:
    """Price-only XIRR, one cost outflow per dated holding and terminal marks."""
    if not rows or any(h.buy_date is None or h.buy_date >= terminal for h in rows):
        return None
    flows: list[tuple[date, float]] = [
        (h.buy_date, -float(h.quantity * h.avg_buy)) for h in rows if h.buy_date]
    flows.append((terminal, sum(float(h.quantity) * marks[h.symbol] for h in rows)))
    origin = min(d for d, _ in flows)
    def npv(rate: float) -> float:
        return sum(amt / ((1+rate) ** ((d-origin).days/365.25)) for d, amt in flows)
    lo, hi = -0.9999, 10000.
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(120):
        mid = (lo+hi)/2
        if npv(lo)*npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo+hi)/2


@dataclass(frozen=True)
class TaxPolicy:
    """Explicitly supplied illustrative listed-equity rates; never tax filing."""
    stcg_rate: float = 0.20
    ltcg_rate: float = 0.125
    ltcg_exemption_remaining: float = 0.0
    cess_rate: float = 0.04
    surcharge_rate: float = 0.0
    stt_paid: bool = True


def indicative_tax(holdings: Sequence[Holding], marks: Mapping[str, float],
                   asof: date, policy: Optional[TaxPolicy]) -> Optional[dict[str, float]]:
    """Hypothetical gains-tax scenario, if all dates and explicit policy exist."""
    if policy is None or not policy.stt_paid or any(h.buy_date is None for h in holdings):
        return None
    st, lt = 0., 0.
    for h in holdings:
        if not h.buy_date or h.buy_date >= asof:
            return None
        gain = float(h.quantity) * (marks[h.symbol]-float(h.avg_buy))
        if (asof - h.buy_date).days > 365:
            lt += gain
        else:
            st += gain
    base = max(0, st)*policy.stcg_rate + max(0, lt-policy.ltcg_exemption_remaining)*policy.ltcg_rate
    return {'stcg_gain': st, 'ltcg_gain': lt,
            'illustrative_tax': base * (1+policy.cess_rate+policy.surcharge_rate)}

@dataclass(frozen=True)
class AuditFlag:
    """Deterministic rule matched to its actual observed evidence number."""
    symbol: str
    severity: str
    rule: str
    evidence: str
    rank_value: float = 0.0


@dataclass
class AuditModel:
    """Disposable calculated report model; no persistence or order generation."""
    holdings: list[Holding]
    report_id: str
    created_at: datetime
    asof: date
    records: list[dict[str, Any]]
    total_invested: Decimal
    total_value: Decimal
    total_pnl: Decimal
    risk: dict[str, Any]
    flags: list[AuditFlag]
    confidence: Counter
    diversification: dict[str, Any]
    xirr: Optional[float]
    tax: Optional[dict[str, float]]
    snapshot_change: Optional[dict[str, Any]]
    coverage: dict[str, Any]
    notes: list[str]
    synthetic: bool = False


def _comparable_facts(a, b):
    return (a.get('value') is not None and b.get('value') is not None
            and a.get('period') == b.get('period')
            and a.get('scope') in ('consolidated', 'standalone')
            and a.get('scope') == b.get('scope')
            and a.get('basis') in ('FY', 'TTM', 'quarter')
            and a.get('basis') == b.get('basis')
            and bool(a.get('unit')) and a.get('unit') == b.get('unit'))


def _display_benchmark_window(frame, count=260):
    window = frame.tail(count).copy()
    return window.div(window.iloc[0]).mul(100)


def _financial_flags(sym: str, data: Mapping[str, Any]) -> list[AuditFlag]:
    """Issuer-agnostic deterministic financial checks; no opinion or forecasts."""
    output: list[AuditFlag] = []
    funds = (data or {}).get('metrics', {})
    cfo = _evidence(funds.get('operating_cash_flow'))
    profit = _evidence(funds.get('net_profit'))
    if not data.get('is_lender') and _comparable_facts(cfo, profit) and cfo['value'] < profit['value']:
        output.append(AuditFlag(sym, 'MEDIUM', 'CFO below reported profit',
                                f'CFO {money(cfo["value"])}, profit {money(profit["value"])}',
                                profit['value']-cfo['value']))
    debt = _evidence(funds.get('debt_equity'))
    if not data.get('is_lender') and debt['value'] is not None and debt['value'] > 1:
        output.append(AuditFlag(sym, 'MEDIUM', 'Debt/equity above 1.0',
                                f'{debt["value"]:.2f}x > 1.0x', debt['value']))
    pledge = _evidence(funds.get('promoter_pledge_pct'))
    if pledge['value'] is not None and pledge['value'] > 20:
        output.append(AuditFlag(sym, 'HIGH', 'Promoter pledge above 20%',
                                f'{pledge["value"]:.1f}% > 20%', pledge['value']))
    ownership = (data or {}).get('ownership', [])
    if isinstance(ownership, (list, tuple)) and len(ownership) >= 2:
        ordered = sorted((r for r in ownership if isinstance(r, Mapping) and
                          _normal_date(r.get('period'))), key=lambda r: str(r['period']))
        if len(ordered) >= 2:
            old = val(ordered[-2].get('promoter'))
            new = val(ordered[-1].get('promoter'))
            if old is not None and new is not None and new < old:
                output.append(AuditFlag(sym, 'MEDIUM', 'Promoter holding declined',
                                        f'{old:.2f}% to {new:.2f}% (-{old-new:.2f} pp)', old-new))
    return output


def _price_flags(record: Mapping[str, Any]) -> list[AuditFlag]:
    """Price-only rules with a specific number and as-of date on every hit."""
    sym = record['symbol']
    res: list[AuditFlag] = []
    w = record['weight']*100
    if w > 40:
        res.append(AuditFlag(sym, 'HIGH', 'Concentration exceeds 40%',
                             f'{w:.2f}% > 40% (as of {record["asof"]})', w))
    meta = record['meta']
    if meta.get('age_sessions') is not None and meta['age_sessions'] > 3:
        res.append(AuditFlag(sym, 'HIGH', 'Daily price is stale',
                             f'{meta["age_sessions"]} sessions > 3', meta['age_sessions']))
    if meta.get('gap') and not record.get('adjustments_confirmed'):
        dt, magnitude = meta['gap']
        res.append(AuditFlag(sym, 'HIGH', 'Unadjusted possible corporate-action gap',
                             f'{magnitude:.1f}% one-day move on {dt} > 40%', magnitude))
    if not record.get('valid_technicals'):
        return res
    close = record['close']
    for period in (50, 200):
        e = record.get('ema'+str(period))
        if e is not None and close < e:
            res.append(AuditFlag(sym, 'MEDIUM', f'Close below EMA{period}',
                                 f'{money(close)} < {money(e)} ({100*(close/e-1):.1f}%)',
                                 abs(100*(close/e-1))))
    ret = record.get('return20')
    if ret is not None and ret < -0.15:
        res.append(AuditFlag(sym, 'HIGH', '20-session return below -15%',
                             f'{ret*100:.1f}% < -15%', -ret*100))
    for period in (20, 252):
        floor = record.get('low'+str(period))
        if floor is not None and close <= floor*1.02:
            res.append(AuditFlag(sym, 'MEDIUM', f'Close within 2% of {period}-session low',
                                 f'{money(close)} vs low {money(floor)} '
                                 f'({100*(close/floor-1):.1f}% above)',
                                 2-100*(close/floor-1)))
    volume_ratio = record.get('volume_ratio')
    last_ret = record.get('last_day_return')
    if volume_ratio is not None and volume_ratio > 1.5 and last_ret is not None and last_ret < 0:
        res.append(AuditFlag(sym, 'MEDIUM', 'Elevated volume on down session',
                             f'Volume {volume_ratio:.2f}x > 1.50x, price {last_ret*100:+.1f}%',
                             volume_ratio))
    atr = record.get('atr_pct')
    if atr is not None and atr > 5:
        res.append(AuditFlag(sym, 'MEDIUM', 'ATR14 above 5% of close',
                             f'{atr:.2f}% > 5%', atr))
    rsi = record.get('rsi')
    if rsi is not None and (rsi > 70 or rsi < 30):
        res.append(AuditFlag(sym, 'MEDIUM', 'RSI14 outside 30-70 band',
                             f'RSI14 {rsi:.1f} ({"above 70" if rsi > 70 else "below 30"})',
                             abs(rsi-50)))
    return res


def _build_record(h: Holding, frame: Optional[pd.DataFrame], meta: dict[str, Any],
                  fund: dict[str, Any], actions: list[dict[str, Any]],
                  dividends: list[dict[str, Any]], events: dict[str, Any],
                  asof: date) -> dict[str, Any]:
    """Calculate a position from a dated close, with noninvented missing values."""
    if frame is None or frame.empty:
        raise ValueError('No price evidence for a holding')
    close_raw = float(frame['Close'].iloc[-1])
    # Align accounting to the same displayed paisa value across ALL pages.
    close = dec(close_raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    invested = (h.quantity*h.avg_buy).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    value_amount = (h.quantity*close).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    pnl = value_amount-invested
    technical_ok = (meta['confidence'] in ('VERIFIED', 'SECONDARY') and not meta['gap']
                    and meta.get('age_sessions') is not None and meta['age_sessions'] <= 3)
    out: dict[str, Any] = {
        'symbol': h.symbol, 'qty': h.quantity, 'buy': h.avg_buy,
        'buy_date': h.buy_date, 'close': float(close), 'asof': str(meta['asof']),
        'invested': invested, 'value': value_amount, 'pnl': pnl,
        'pnl_pct': 100*float(pnl/invested), 'weight': 0.,
        'meta': meta, 'frame': frame, 'fund': fund, 'events': events,
        'actions': actions, 'dividends': dividends,
        'adjustments_confirmed': meta.get('adjustments_confirmed',False), 'valid_technicals': technical_ok,
        'ema20': None, 'ema50': None, 'ema200': None, 'rsi': None,
        'atr_pct': None, 'return20': None, 'low20': None, 'low252': None,
        'high20': None, 'high252': None, 'range_pos': None,
        'volume_ratio': None, 'days_to_exit': None,
        'last_day_return': None, 'swing_low': None, 'swing_high': None,
        'trailing': {}, 'confidence': meta['confidence'],
    }
    for action in actions:
        when = _normal_date(action.get('date')) if isinstance(action, Mapping) else None
        kind = str(action.get('type', '')).lower() if isinstance(action, Mapping) else ''
        if (kind in ('split', 'bonus') and h.buy_date and when
                and when >= h.buy_date):
            out['action_cost_warning'] = 'post-acquisition split/bonus requires broker cost-basis reconciliation'
    if not technical_ok:
        return out
    c = frame['Close']
    out.update({
        'ema20': ema(c,20), 'ema50': ema(c,50), 'ema200': ema(c,200),
        'rsi': rsi14(c), 'atr_pct': atr14_pct(frame),
        'return20': _ret(frame, 20),
        'last_day_return': float(c.iloc[-1]/c.iloc[-2]-1) if len(c)>1 else None,
        'low20': float(c.iloc[-20:].min()), 'high20': float(c.iloc[-20:].max()),
        'low252': float(c.iloc[-252:].min()), 'high252': float(c.iloc[-252:].max()),
    })
    if out['low252'] < out['high252']:
        out['range_pos'] = (out['close']-out['low252'])/(out['high252']-out['low252'])
    if len(frame) >= 21:
        avg = float(frame['Volume'].iloc[-21:-1].mean())
        if avg > 0:
            out['volume_ratio'] = float(frame['Volume'].iloc[-1]/avg)
            out['days_to_exit'] = float(h.quantity)/avg
    out['swing_low'], out['swing_high'] = _swing_levels(c)
    return out


def _risk_metrics(records: Sequence[Mapping[str, Any]],
                  benchmarks: Mapping[str, Optional[pd.DataFrame]]) -> dict[str, Any]:
    """No forward fill; include EVERY current holding or suppress risk outputs."""
    result: dict[str, Any] = {'status': 'UNAVAILABLE', 'reason': 'missing aligned completed histories'}
    if not records or any(not r['valid_technicals'] or len(r['frame']) < 65 for r in records):
        return result
    dates = {r['asof'] for r in records}
    if len(dates) != 1:
        result['reason'] = 'holdings latest sessions differ; no common final close'
        return result
    try:
        prices = pd.concat({r['symbol']: r['frame']['Close']
                            for r in records}, axis=1, join='inner').sort_index().tail(510)
        if len(prices) < 65 or prices.index[-1].date().isoformat() not in dates:
            result['reason'] = 'fewer than 65 aligned observed sessions'
            return result
        if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
            result['reason'] = 'non-finite common prices'
            return result
        qs = pd.Series({r['symbol']: float(r['qty']) for r in records})
        values = prices.mul(qs, axis=1).sum(axis=1)
        port_ret = values.pct_change().dropna()
        stock_ret = prices.pct_change().dropna()
        if len(port_ret) < 64 or port_ret.abs().max() > 0.4:
            result['reason'] = 'insufficient returns or unresolved 40%+ gap'
            return result
        returns = port_ret.to_numpy(dtype=float)
        var95, es95 = var_and_es(returns)
        cov = stock_ret.cov().to_numpy(dtype=float)*252
        weights = np.asarray([r['weight'] for r in records], float)
        variance = float(weights@cov@weights)
        contrib = (weights * (cov@weights)/variance if variance > 1e-14
                   else np.full(len(weights), np.nan))
        risk_vol = annual_volatility(returns)
        rolling = pd.DataFrame({'vol30': port_ret.rolling(30).std()*np.sqrt(252)*100,
                                'vol60': port_ret.rolling(60).std()*np.sqrt(252)*100})
        monthly = values.resample('ME').last().pct_change(fill_method=None).dropna().tail(12)
        observed = {
            'status': 'VERIFIED', 'observations': len(returns),
            'first': prices.index[0].date().isoformat(),
            'last': prices.index[-1].date().isoformat(),
            'annual_vol_pct': risk_vol*100,
            'max_dd_pct': 100*max_drawdown(values),
            'current_dd_pct': 100*(float(values.iloc[-1]/values.cummax().iloc[-1])-1),
            'var95_pct': var95*100, 'es95_pct': es95*100,
            'var95_rs': float(var95*sum(float(r['value']) for r in records)),
            'es95_rs': float(es95*sum(float(r['value']) for r in records)),
            'portfolio_series': values, 'monthly': monthly,
            'rolling': rolling, 'cov': cov, 'risk_contrib': dict(zip(prices.columns, contrib)),
            'benchmarks': {}, 'aligned_for_plot': None,
        }
        largest = int(np.argmax(weights))
        scenarios = []
        for target in (.40, .25, .15):
            simulated = weights.copy()
            old_largest = weights[largest]
            simulated[largest] = target
            others = 1-old_largest
            if others > 1e-12:
                simulated[np.arange(len(weights)) != largest] *= (1-target)/others
            else:
                simulated[:] = 0.
                simulated[largest] = target
                # Remaining weight is 0-volatility cash in a single-stock model.
            scen_vol = float(np.sqrt(max(0., simulated@cov@simulated))*100)
            scenarios.append((target, scen_vol))
        observed['what_if'] = scenarios
        for name in ('Nifty 50','Nifty 500'):
            frame = benchmarks.get(name)
            if frame is None or len(frame) < 65:
                observed['benchmarks'][name] = {'reason': 'index OHLCV unavailable'}
                continue
            pair = pd.concat([values.rename('portfolio'), frame['Close'].rename('index')],
                             axis=1, join='inner').dropna().sort_index()
            if len(pair) < 65 or pair.index[-1] != values.index[-1]:
                observed['benchmarks'][name] = {'reason': 'index has insufficient aligned last-session observations'}
                continue
            ret = pair.pct_change().dropna()
            if not np.isfinite(ret.to_numpy()).all() or (ret.abs() > .40).any().any():
                observed['benchmarks'][name] = {'reason': 'index returns inconsistent or gap flagged'}
                continue
            b = ret['index'].to_numpy()
            p = ret['portfolio'].to_numpy()
            try:
                beta = beta_against(p,b)
                corr = float(np.corrcoef(p,b)[0,1])
            except ValueError:
                observed['benchmarks'][name] = {'reason': 'zero index variance'}
                continue
            rel = pair['portfolio']/pair['index']
            portfolio_change = float(pair['portfolio'].iloc[-1]/pair['portfolio'].iloc[0]-1)
            index_change = float(pair['index'].iloc[-1]/pair['index'].iloc[0]-1)
            observed['benchmarks'][name] = {
                'beta': beta, 'correlation': corr, 'sessions': len(ret),
                'alpha_annual_pct': 252*(p.mean()-beta*b.mean())*100,  # RF=0 explicit
                'tracking_diff_pct': 100*(portfolio_change-index_change),
                'relative_dd_pct': 100*max_drawdown(rel),
                'portfolio_change_pct': 100*portfolio_change,
                'index_change_pct': 100*index_change,
            }
        plot_frames = [values.rename('Portfolio')]
        for title in ('Nifty 50', 'Nifty 500'):
            frame = benchmarks.get(title)
            if observed['benchmarks'].get(title, {}).get('sessions') and frame is not None:
                plot_frames.append(frame['Close'].rename(title))
        if len(plot_frames) > 1:
            comparison = pd.concat(plot_frames, axis=1, join='inner').dropna().sort_index()
            if len(comparison) >= 65 and comparison.index[-1] == values.index[-1]:
                observed['aligned_for_plot'] = comparison/comparison.iloc[0]*100
        return observed
    except (ValueError, KeyError, TypeError, IndexError, FloatingPointError) as exc:
        LOG.warning('Portfolio risk unavailable: %s', type(exc).__name__)
        result['reason'] = 'risk computation failed validation'
        return result


def build_model(holdings: Sequence[Holding | Mapping[str, Any]], dataset: Dataset,
                *, now: Optional[datetime] = None, holidays: Sequence[date] = (),
                snapshot: Optional[Mapping[str, Any]] = None,
                tax_policy: Optional[TaxPolicy] = None) -> AuditModel:
    """Pure deterministic financial research from an already fetched Dataset."""
    hs = validate_holdings(list(holdings))
    stamp = now or datetime.now(IST)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    expected = latest_completed_session(stamp, holidays)
    notes: list[str] = []
    records, frames, marks = [], {}, {}
    count: Counter = Counter({'VERIFIED':0, 'SECONDARY':0, 'MISSING':0})
    for h in hs:
        frame, meta = clean_history(dataset.stocks.get(h.symbol), expected, holidays)
        count[meta['confidence']] += 1
        if frame is None:
            # Incomplete price evidence is not rendered as a fictitious value.
            # Raise rather than silently omit a position from portfolio totals.
            raise ValueError('Unable to value all holdings: ' + meta['reason'])
        marks[h.symbol] = float(dec(frame['Close'].iloc[-1]).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP))
        frames[h.symbol] = frame
        record = _build_record(h, frame, meta,
                               dataset.fundamentals.get(h.symbol, {}),
                               dataset.actions.get(h.symbol, []),
                               dataset.dividends.get(h.symbol, []),
                               dataset.events.get(h.symbol, {}), expected)
        records.append(record)
        if meta['confidence'] == 'MISSING':
            notes.append(f'{h.symbol}: {meta["reason"]}')
        for key in ('revenue','net_profit','operating_margin','roe','roce',
                    'debt_equity','operating_cash_flow','receivable_days',
                    'promoter_pledge_pct','pe','pb','pe_median5y','industry_pe_median'):
            count[_evidence(dataset.fundamentals.get(h.symbol, {}).get('metrics', {}).get(key))['confidence']] += 1
    invested = sum((r['invested'] for r in records), Decimal('0'))
    current = sum((r['value'] for r in records), Decimal('0'))
    if current <= 0 or invested <= 0:
        raise ValueError('Nonpositive portfolio valuation')
    for r in records:
        r['weight'] = float(r['value']/current)
    if not math.isclose(sum(r['weight'] for r in records), 1, abs_tol=1e-10):
        raise ArithmeticError('Portfolio weights did not reconcile')
    asofs = {r['asof'] for r in records}
    asof = min(date.fromisoformat(v) for v in asofs)
    if len(asofs) > 1:
        notes.append('Mismatched last session dates across holdings')
    flags = []
    for r in records:
        flags.extend(_price_flags(r))
        flags.extend(_financial_flags(r['symbol'], r['fund']))
        if r.get('action_cost_warning'):
            flags.append(AuditFlag(r['symbol'], 'MEDIUM', 'Corporate action and cost basis',
                                   'post-buy split/bonus recorded; adjusted average cost unverified', 100))
    flags.sort(key=lambda f: ({'HIGH':0,'MEDIUM':1,'LOW':2}[f.severity],
                               -f.rank_value, f.symbol, f.rule))
    categories: dict[str, dict[str, float]] = {'industry': {}, 'market_cap_bucket': {}}
    coverage_reason = {}
    if not dataset.constituents:
        coverage_reason['industry'] = 'official Nifty 500 roster not validated'
        coverage_reason['market_cap_bucket'] = 'Nifty 500 cap categories not provided'
    for category in categories:
        for record in records:
            mapping = dataset.constituents.get(record['symbol'], {})
            record[category] = _str(mapping.get(category),45) if mapping else ''
            if category == 'industry':
                record['industry'] = record[category]
            value = _str(mapping.get(category), 45) if mapping else ''
            label = value or 'Unclassified'
            categories[category][label] = categories[category].get(label, 0.) + record['weight']
            # No official effective date in this source: never assert VERIFIED.
            count['SECONDARY' if value else 'MISSING'] += 1
    sectors = {k:v for k,v in categories['industry'].items() if k != 'Unclassified'}
    diversity = {
        'hhi': hhi([r['weight'] for r in records]),
        'effective_count': 1/hhi([r['weight'] for r in records]),
        'top1': max(r['weight'] for r in records),
        'top3': sum(sorted([r['weight'] for r in records],reverse=True)[:3]),
        'sector_count': len(sectors) if sectors else None,
        'buckets': categories, 'reason': coverage_reason,
    }
    index_frames: dict[str, Optional[pd.DataFrame]] = {}
    for key, series in [('Nifty 50',dataset.nifty50), ('Nifty 500',dataset.nifty500)]:
        frame, info = clean_history(series, expected, holidays)
        usable = (frame is not None and info['confidence'] != 'MISSING' and
                  info['age_sessions'] is not None and info['age_sessions'] <=3 and
                  info['gap'] is None)
        index_frames[key] = frame if usable else None
        count[info['confidence']] += 1
        if not usable:
            notes.append(f'{key}: {info["reason"]}')
    for r in records:
        for dur, sessions in (('1W',5),('1M',21),('3M',63),('6M',126),('1Y',252)):
            paired_stock,paired_index = paired_trailing(
                r['frame'] if r['valid_technicals'] else None,
                index_frames['Nifty 50'],sessions)
            r['trailing'][dur] = {
                'stock': paired_stock if paired_stock is not None else (
                    _ret(r['frame'],sessions) if r['valid_technicals'] else None),
                'nifty50': paired_index,
                'difference': (paired_stock-paired_index
                               if paired_stock is not None and paired_index is not None
                               else None)}
    risk = _risk_metrics(records, index_frames)
    xirr = _xirr(hs,marks,asof) if all(h.buy_date for h in hs) else None
    tax = indicative_tax(hs,marks,asof,tax_policy)
    previous: Optional[dict[str, Any]] = None
    if isinstance(snapshot, Mapping):
        try:
            prev_positions = snapshot.get('values') or {}
            prior_val = dec(snapshot.get('total_value'))
            if prior_val > 0 and isinstance(prev_positions, Mapping):
                previous = {'prior_total': prior_val,
                            'delta': current-prior_val,
                            'weight_changes': []}
                for r in records:
                    old_val = val(prev_positions.get(r['symbol']))
                    if old_val is not None and old_val >= 0:
                        previous['weight_changes'].append((
                            r['symbol'], 100*(r['weight']-old_val/float(prior_val))))
        except (TypeError, ValueError, KeyError, InvalidOperation):
            notes.append('Optional prior snapshot malformed; no comparison')
    if not flags:
        flags = [AuditFlag('ALL', 'LOW', 'No configured threshold triggered',
                           '0 deterministic rule hits', 0)]
    return AuditModel(
        holdings=hs, report_id='AUD-'+uuid.uuid4().hex[:11].upper(),
        created_at=stamp, asof=asof, records=records,
        total_invested=invested, total_value=current,
        total_pnl=current-invested, risk=risk, flags=flags,
        confidence=count, diversification=diversity,
        xirr=xirr, tax=tax, snapshot_change=previous,
        synthetic=dataset.synthetic, coverage={'expected':expected.isoformat(),
                  'last_prices':{r['symbol']:r['asof'] for r in records},
                  'constituents':len(dataset.constituents),
                  'source_errors':len(dataset.errors)}, notes=notes)

# ---------------------------------------------------------------------------
# Graphics - all created in memory. A failed chart is replaced by text.
# ---------------------------------------------------------------------------

def _chart_image(draw: Callable[[Any], None], width: float = CONTENT_W,
                 height: float = 140., figsize: tuple[float,float] = (7.,2.3)) -> Image:
    """Render Matplotlib chart to BytesIO; never create a PNG on disk."""
    fig = plt.figure(figsize=figsize, dpi=120)
    try:
        draw(fig)
        fig.patch.set_facecolor('white')
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=155, facecolor='white',
                    bbox_inches='tight', pad_inches=.10)
        buf.seek(0)
        image = Image(buf, width=width, height=height, kind='proportional')
        image._keep_buffer = buf  # hold resource until PDF build finishes
        return image
    finally:
        plt.close(fig)


def _donut(buckets: Mapping[str,float], title: str, width: float = 240,
           height: float = 165) -> Image:
    names = [name for name,v in buckets.items() if v > .001]
    weights = [buckets[k]*100 for k in names]
    if not weights:
        raise ValueError('No exposure data')
    def plot(fig: Any) -> None:
        ax = fig.add_subplot(111)
        palette = ['#176FA7','#308F8B','#D19A35','#7D78B5','#BE677E','#A4ADB6']
        ax.pie(weights, startangle=90, counterclock=False,
               colors=palette[:len(weights)],
               wedgeprops={'width':.38,'edgecolor':'white'},
               autopct=lambda p: ('%.0f%%'%p if p>=7 else ''),
               pctdistance=.81, textprops={'fontsize':8})
        ax.set_title(title, fontsize=10, weight='bold', color='#10253C')
        ax.legend([f'{n[:20]} ({w:.1f}%)' for n,w in zip(names,weights)],
                  loc='center left', bbox_to_anchor=(1.02,.5),
                  frameon=False, fontsize=7)
    return _chart_image(plot, width=width, height=height, figsize=(5.4,2.6))


def _bar_chart(labels: Sequence[str], values: Sequence[float],
               title: str, *, horizontal: bool = True, height: float = 115) -> Image:
    def plot(fig: Any) -> None:
        ax = fig.add_subplot(111)
        x = np.arange(len(labels))
        if horizontal:
            ax.barh(x, values, color='#176FA7', alpha=.9)
            ax.set_yticks(x,labels,fontsize=8)
            ax.axvline(0,color='#617082',linewidth=.65)
            ax.tick_params(axis='x',labelsize=7)
            ax.invert_yaxis()
        else:
            ax.bar(x,values,color='#176FA7')
            ax.set_xticks(x,labels,fontsize=7)
        ax.set_title(title,fontsize=9,color='#10253C')
        for spine in ax.spines.values(): spine.set_visible(False)
        ax.grid(axis='x' if horizontal else 'y',alpha=.18)
        ax.set_axisbelow(True)
    return _chart_image(plot, height=height, figsize=(7,2))


def _lines_chart(series: pd.DataFrame, title: str, height: float = 160) -> Image:
    def plot(fig: Any) -> None:
        ax = fig.add_subplot(111)
        palette = ['#176FA7','#CB8B22','#8066AE','#308F8B']
        for k,col in enumerate(series.columns):
            ax.plot(series.index,series[col],label=str(col),
                    color=palette[k%len(palette)],linewidth=1.45)
        ax.set_title(title,fontsize=9,color='#10253C')
        ax.tick_params(labelsize=7)
        ax.grid(alpha=.18)
        ax.legend(ncol=max(1,min(3,len(series.columns))),fontsize=7,
                  loc='upper left',frameon=False)
        for spine in ax.spines.values():spine.set_visible(False)
        fig.autofmt_xdate(rotation=0)
    return _chart_image(plot, height=height, figsize=(7.2,2.4))


def _monthly_heatmap(series: pd.Series, height: float = 105) -> Image:
    """Explicit observed month labels, no missing month imputation."""
    def plot(fig: Any) -> None:
        ax=fig.add_subplot(111)
        arr=series.to_numpy(float)[np.newaxis,:]*100
        ax.imshow(arr,aspect='auto',cmap='RdYlGn',vmin=-12,vmax=12)
        ax.set_yticks([0],['% monthly'],fontsize=8)
        ax.set_xticks(range(len(series)),
                      [str(d.strftime('%b\n%y')) for d in series.index],fontsize=7)
        for j,v in enumerate(arr[0]):
            ax.text(j,0,f'{v:+.1f}',ha='center',va='center',fontsize=6.3,
                    color='black')
        ax.set_title('Observed monthly portfolio changes (static current units)',
                     fontsize=8.7,color='#10253C')
        for spine in ax.spines.values():spine.set_visible(False)
    return _chart_image(plot,height=height,figsize=(7.2,1.6))


def _quarterly_chart(quarters: Sequence[Mapping[str,Any]],
                    height: float = 100) -> Image:
    rows = _quarterly_valid(quarters)
    if len(rows) < 2:
        raise ValueError('Fewer than two dated quarters')
    def plot(fig: Any) -> None:
        ax=fig.add_subplot(111)
        x=np.arange(len(rows))
        ax.plot(x,[q['revenue'] for q in rows],'-o',color='#176FA7',
                lw=1.25,ms=2.7,label='Revenue')
        ax.plot(x,[q['profit'] for q in rows],'-o',color='#A6630A',
                lw=1.25,ms=2.7,label='Profit')
        ax.set_xticks(x,[q['period'][2:7] for q in rows],fontsize=7)
        ax.tick_params(axis='y',labelsize=7)
        ax.legend(loc='upper left',frameon=False,fontsize=7)
        ax.set_title('Dated quarterly financial series (secondary until reconciled)',
                     fontsize=8.5,color='#10253C')
        ax.grid(alpha=.17)
        for spine in ax.spines.values():spine.set_visible(False)
    return _chart_image(plot,height=height,figsize=(6.8,1.6))



def _quarterly_yoy_chart(quarters: Sequence[Mapping[str,Any]],
                         height: float = 63) -> Image:
    """True same-quarter YoY revenue/profit change, never adjacent-quarter growth."""
    rows=_quarterly_valid(quarters)
    if len(rows)<5:
        raise ValueError('Need five dated quarters for YoY')
    x=[]; ys=[]; yp=[]
    for j in range(4,len(rows)):
        older,newer=rows[j-4],rows[j]
        if older['revenue'] <= 0 or older['profit'] == 0:
            continue
        x.append(newer['period'][2:7])
        ys.append((newer['revenue']/older['revenue']-1)*100)
        yp.append((newer['profit']/abs(older['profit'])-1)*100)
    if not x:
        raise ValueError('No positive same-quarter comparator')
    def plot(fig: Any) -> None:
        ax=fig.add_subplot(111)
        pos=np.arange(len(x))
        ax.plot(pos,ys,'-o',color='#176FA7',ms=2.8,lw=1.2,label='Revenue YoY %')
        ax.plot(pos,yp,'-o',color='#B26C0B',ms=2.8,lw=1.2,label='Profit YoY %')
        ax.axhline(0,color='#617082',lw=.6)
        ax.set_xticks(pos,x,fontsize=6)
        ax.tick_params(axis='y',labelsize=6)
        ax.legend(loc='upper left',frameon=False,fontsize=6,ncol=2)
        ax.grid(alpha=.15)
        for spine in ax.spines.values():spine.set_visible(False)
    return _chart_image(plot,height=height,figsize=(6.8,1.35))


def _script_image(lines: Sequence[str], *, lang: str,
                  width: float = CONTENT_W, height: float = 55) -> Image:
    """Render Hindi/Gujarati with Pillow RAQM shaping (matplotlib dependency)."""
    from PIL import Image as PILImage, ImageDraw, ImageFont, features
    import os
    if not features.check('raqm'):
        raise ValueError('complex-script RAQM support missing')
    family = ('NotoSansDevanagari-Regular.ttf' if lang=='hi'
              else 'NotoSansGujarati-Regular.ttf')
    candidates = [f'/usr/share/fonts/truetype/noto/{family}',
                  f'/usr/share/fonts/truetype/noto/{family.replace("-Regular", "")}',
                  f'/usr/share/fonts/opentype/noto/{family}']
    path = next((p for p in candidates if os.path.isfile(p)),None)
    if not path:
        raise ValueError('Indian-script font unavailable')
    # Each row is drawn with RAQM, so reordering/conjuncts are shaped.
    font = ImageFont.truetype(path,26,layout_engine=ImageFont.Layout.RAQM)
    W,H = 1320, max(105, 54*len(lines)+22)
    image = PILImage.new('RGB',(W,H),'white')
    drawer = ImageDraw.Draw(image)
    latin_path='/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf'
    latin = (ImageFont.truetype(latin_path,26)
             if os.path.isfile(latin_path) else font)
    for i,line in enumerate(lines):
        x=22
        # Indic shaping and Latin ticker glyphs need different fonts.
        for fragment in re.findall(r'[A-Za-z0-9%.,()+-]+|[^A-Za-z0-9%.,()+-]+',line):
            active = latin if re.match(r'[A-Za-z0-9%.,()+-]',fragment) else font
            drawer.text((x,14+52*i),fragment,font=active,fill=(25,45,69),
                        direction='ltr',features=['kern'])
            x += drawer.textlength(fragment,font=active,direction='ltr')
    buf=io.BytesIO()
    image.save(buf,format='PNG')
    buf.seek(0)
    item=Image(buf,width=width,height=height,kind='proportional')
    item._keep_buffer=buf
    return item


# ---------------------------------------------------------------------------
# PDF typesetting. Page breaks are deliberate: 6 core + N companies + 3 appendices.
# ---------------------------------------------------------------------------

def _styles() -> dict[str,ParagraphStyle]:
    return {
        'title':ParagraphStyle('auditTitle',fontName='Helvetica-Bold',fontSize=15,
                              leading=19,textColor=NAVY,spaceAfter=10),
        'h2':ParagraphStyle('auditH2',fontName='Helvetica-Bold',fontSize=10.3,
                           leading=13.5,textColor=NAVY,spaceBefore=8,spaceAfter=4,
                           keepWithNext=True),
        'body':ParagraphStyle('auditBody',fontName='Helvetica',fontSize=8.0,
                             leading=11.3,textColor=INK,spaceAfter=4),
        'small':ParagraphStyle('auditSmall',fontName='Helvetica',fontSize=7.0,
                              leading=9.5,textColor=INK,spaceAfter=2),
        'tiny':ParagraphStyle('auditTiny',fontName='Helvetica',fontSize=6.35,
                             leading=8.2,textColor=INK,spaceAfter=1),
        'warning':ParagraphStyle('auditWarning',fontName='Helvetica-Bold',fontSize=7.4,
                                leading=10.2,textColor=RED,spaceAfter=4),
        'center':ParagraphStyle('auditCenter',fontName='Helvetica-Bold',fontSize=10,
                               leading=13,alignment=TA_CENTER,textColor=NAVY),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    """Escape every provider/client field before using ReportLab mini-markup."""
    return Paragraph(html_escape(str(text)).replace('\n','<br/>'),style)


def _tbl(data: list[list[Any]], widths: Sequence[float], *, header: bool = True,
         font_size: float = 7., row_padding: float = 4.,
         right_align: Sequence[int] = ()) -> Table:
    """Bounded single-page table with alternating row shading and wrap."""
    styles=_styles()
    cell_style=styles['tiny'] if font_size < 7 else styles['small']
    rows=[[item if isinstance(item,(Paragraph,Image,Table)) else _p(item,cell_style)
           for item in raw] for raw in data]
    table=Table(rows,colWidths=list(widths),hAlign='LEFT',repeatRows=1 if header else 0)
    commands=[('VALIGN',(0,0),(-1,-1),'TOP'),
              ('TOPPADDING',(0,0),(-1,-1),row_padding),
              ('BOTTOMPADDING',(0,0),(-1,-1),row_padding),
              ('LEFTPADDING',(0,0),(-1,-1),5),
              ('RIGHTPADDING',(0,0),(-1,-1),5),
              ('LINEBELOW',(0,-1),(-1,-1),.5,LINE),
              ('ROWBACKGROUNDS',(0,1 if header else 0),(-1,-1),
               [colors.white,colors.HexColor('#F4F7FA')])]
    if header:
        commands += [('BACKGROUND',(0,0),(-1,0),NAVY),
                     ('TEXTCOLOR',(0,0),(-1,0),WHITE)]
        # Paragraph is its own foreground; make dedicated white heading cells.
        table._cellvalues[0]=[
            _p(str(t),ParagraphStyle('tableHeader',parent=cell_style,
                                      textColor=WHITE,fontName='Helvetica-Bold'))
            for t in data[0]]
    for j in right_align:
        commands.append(('ALIGN',(j,1 if header else 0),(j,-1),'RIGHT'))
    table.setStyle(TableStyle(commands))
    return table


def _info_panel(title: str, rows: Sequence[str], color: colors.Color = BLUE) -> Table:
    sty=_styles()
    left=[_p(title,ParagraphStyle('panelTitle',parent=sty['small'],
                                  textColor=color,fontName='Helvetica-Bold'))]
    left.extend(_p(x,sty['small']) for x in rows)
    sub=Table([[x] for x in left],colWidths=[CONTENT_W-24],hAlign='LEFT')
    sub.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PAPER),
                              ('BOX',(0,0),(-1,-1),.5,LINE),
                              ('LEFTPADDING',(0,0),(-1,-1),11),
                              ('RIGHTPADDING',(0,0),(-1,-1),11),
                              ('TOPPADDING',(0,0),(-1,-1),4),
                              ('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    return sub


def _head(story: list[Any], heading: str, desc: Optional[str] = None) -> None:
    sty=_styles()
    story.append(_p(heading,sty['title']))
    if desc:
        story.append(_p(desc,sty['small']))
    story.append(HRFlowable(width='100%',thickness=.6,color=LINE))
    story.append(Spacer(1,5))


def _sub(story: list[Any], heading: str) -> None:
    story.append(_p(heading,_styles()['h2']))


def _para(story: list[Any], content: str, *, tiny: bool = False) -> None:
    story.append(_p(content,_styles()['tiny' if tiny else 'body']))


def _severity_mark(label: str) -> Table:
    """Small coloured severity label embedded in each holding header row."""
    col=SEVERITY_COLORS.get(label,GREEN)
    badge=Table([[Paragraph(html_escape(label),ParagraphStyle(
        'badge',fontName='Helvetica-Bold',fontSize=8,
        textColor=WHITE,leading=10))]],colWidths=[65],rowHeights=[20])
    badge.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),col),
                               ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                               ('ALIGN',(0,0),(-1,-1),'CENTER'),
                               ('LEFTPADDING',(0,0),(-1,-1),9)]))
    return badge


def _qr_or_none(url: Optional[str], size: int=48) -> Optional[Drawing]:
    """Draw optional HTTPS link; never include URLs with credentials/fragments."""
    if not isinstance(url,str) or len(url)>300:
        return None
    parsed=urlparse(url)
    if (parsed.scheme!='https' or not parsed.netloc or parsed.username or
            parsed.password or parsed.fragment):
        return None
    qr=QrCodeWidget(url)
    bounds=qr.getBounds()
    scale=size/max(bounds[2]-bounds[0],bounds[3]-bounds[1])
    d=Drawing(size,size,transform=[scale,0,0,scale,0,0])
    d.add(qr)
    return d


def _page_decor(canvas: Any, doc: Any, report_id: str) -> None:
    """Fixed navy header, consistent margins, short non-advice footer."""
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0,PAGE_H-63,PAGE_W,63,fill=1,stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont('Helvetica-Bold',12.6)
    canvas.drawString(38,PAGE_H-30,'MSK | PORTFOLIO AUDIT')
    canvas.setFont('Helvetica',7.5)
    canvas.drawRightString(PAGE_W-38,PAGE_H-29,
                           'SYNTHETIC DEMO - NO REAL PRICES'
                           if getattr(doc,'synthetic',False) else
                           'EDUCATIONAL EQUITY RESEARCH')
    canvas.setStrokeColor(LINE)
    canvas.line(38,41,PAGE_W-38,41)
    canvas.setFillColor(MUTED)
    canvas.setFont('Helvetica',7)
    canvas.drawString(38,29,f'{report_id} | Private in-memory report | No account orders')
    canvas.drawRightString(PAGE_W-38,29,f'Page {doc.page}')
    canvas.restoreState()


def _top_issue_lines(model: AuditModel, count: int=3) -> list[str]:
    issues=model.flags[:count]
    lines=[f'{j+1}. {f.severity} | {f.symbol} | {f.rule}: {f.evidence}'
           for j,f in enumerate(issues)]
    while len(lines)<count:
        lines.append(f'{len(lines)+1}. LOW | no further configured rule hits (0)')
    return lines


def _make_page1(story: list[Any], m: AuditModel) -> None:
    _head(story,'01  |  PORTFOLIO DASHBOARD',
          f'Report ID {m.report_id} | Generated {m.created_at.astimezone(IST):%d %b %Y %H:%M IST} '
          f'| Valuation close {m.asof}')
    if m.synthetic:
        _para(story,'SYNTHETIC DATA ONLY - OFFLINE VALIDATION, NOT MARKET FIGURES.')
    invested,value,pnl=m.total_invested,m.total_value,m.total_pnl
    panel=_tbl([['INVESTED','LAST-CLOSE VALUE','UNREALISED P/L'],
                [money(invested),money(value),f'{money(pnl)} ({pct(float(pnl/invested)*100,signed=True)})']],
               [CONTENT_W/3]*3,header=True,row_padding=10)
    story.append(panel)
    _para(story, 'XIRR (price only): '+(
        pct(m.xirr*100,digits=2,signed=True) if m.xirr is not None
        else unavailable('all acquisition dates required')))
    _sub(story,'TOP 3 ISSUES | RANKED BY DETERMINISTIC SEVERITY')
    story.append(_info_panel('Numerical rule evidence (not an investment instruction)',
                             _top_issue_lines(m),color=RED))
    _sub(story,'CURRENT ALLOCATION')
    try:
        weights={r['symbol']:r['weight'] for r in m.records}
        story.append(_donut(weights,'Portfolio allocation',width=CONTENT_W,height=173))
    except Exception:
        _para(story,unavailable('allocation chart could not render'))
    _sub(story,'DATA CONFIDENCE SUMMARY')
    conf=m.confidence
    _para(story, f'VERIFIED fields: {conf["VERIFIED"]}  |  '
          f'SECONDARY fields: {conf["SECONDARY"]}  |  MISSING fields: {conf["MISSING"]}. '
          'Confidence is counted per assessed field, not a portfolio quality grade.')
    _sub(story,'ITEMS TO REVIEW')
    for item in ('[ ] Position and industry concentration',
                 '[ ] Cost basis after splits / bonus changes',
                 '[ ] Data age, missing peer/issuer evidence and dividends'):
        _para(story,item,tiny=True)


def _position_word(close: float, e: Optional[float]) -> str:
    return ('N/A' if e is None else ('above' if close >= e else 'below'))


def _make_page2(story: list[Any], m: AuditModel) -> None:
    _head(story,'02  |  HOLDINGS AND RELATIVE RETURNS',
          'Every valuation is qty x the same two-decimal displayed close; trailing returns use observed sessions.')
    hdr=['SYMBOL','QTY','AVG COST','CLOSE','VALUE','P/L','WEIGHT','RSI']
    widths=[57,57,58,60,77,81,53,CONTENT_W-443]
    table=[hdr]
    for r in m.records:
        table.append([r['symbol'],f'{r["qty"]:,.0f}',money(r['buy']),
                      money(r['close']),money(r['value']),money(r['pnl']),
                      pct(r['weight']*100),
                      f'{r["rsi"]:.1f}' if r['rsi'] is not None else 'N/A'])
    story.append(_tbl(table,widths,row_padding=5))
    _sub(story,'HOLDING EVIDENCE | SEVERITY IN THE HEADER')
    for r in m.records:
        own=[f for f in m.flags if f.symbol==r['symbol']]
        sev=own[0].severity if own else 'LOW'
        t=Table([[_p(f'{r["symbol"]}  |  {pct(r["weight"]*100)} of total',_styles()['h2']),
                  _severity_mark(sev)]],colWidths=[CONTENT_W-83,83])
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                               ('BACKGROUND',(0,0),(-1,-1),PAPER),
                               ('LEFTPADDING',(0,0),(-1,-1),6),
                               ('RIGHTPADDING',(0,0),(-1,-1),6)]))
        c=r['close']
        trend=(f'EMA20 { _position_word(c,r["ema20"]) } | '
               f'EMA50 { _position_word(c,r["ema50"]) } | '
               f'EMA200 { _position_word(c,r["ema200"]) }')
        d=(f'20D {pct(r["return20"]*100,signed=True) if r["return20"] is not None else "N/A"}  | '
           f'ATR14 {pct(r["atr_pct"])}  |  52W range location '
           f'{pct(100*r["range_pos"]) if r["range_pos"] is not None else "N/A"}')
        relative=[]
        for k,v in r['trailing'].items():
            relative.append(f'{k}: {pct(v["stock"]*100,signed=True) if v["stock"] is not None else "N/A"} '
                            f'vs N50 {pct(v["nifty50"]*100,signed=True) if v["nifty50"] is not None else "N/A"}')
        block=[t,_p(trend,_styles()['small']),_p(d,_styles()['small']),
               _p(' | '.join(relative),_styles()['tiny']),Spacer(1,4)]
        story.append(KeepTogether(block))
    _para(story,'Relative difference = stock return minus Nifty 50 return in percentage points on matched dates; '
          'same-window index data may be unavailable.',tiny=True)


def _make_page3(story: list[Any], m: AuditModel) -> None:
    d=m.diversification
    _head(story,'03  |  DIVERSIFICATION AND EXPOSURE',
          'HHI and sector labels describe current holdings; they do not imply suitability.')
    summary=[['HHI','EFFECTIVE STOCK COUNT','TOP 1','TOP 3','VERIFIED INDUSTRIES'],
             [f'{d["hhi"]:.4f}',f'{d["effective_count"]:.2f}',
              pct(d['top1']*100),pct(d['top3']*100),
              str(d['sector_count']) if d['sector_count'] is not None else 'Unavailable']]
    story.append(_tbl(summary,[CONTENT_W/5]*5,row_padding=8))
    _para(story,'HHI = sum(weight squared); effective stock count = 1/HHI. '
          'An effective count near 1 signals concentrated exposure.')
    panels=[]
    for key,title in [('industry','Industry allocation'),('market_cap_bucket','Market-cap bucket')]:
        bucket=m.diversification['buckets'][key]
        known={k:v for k,v in bucket.items() if k!='Unclassified'}
        if known:
            try:
                panels.append(_donut(bucket,title,width=240,height=170))
            except Exception:
                panels.append(_p(unavailable('chart rendering failed'),_styles()['body']))
        else:
            reason=m.diversification['reason'].get(key,'no matched verified label')
            panels.append(_p(title+' - '+unavailable(reason),_styles()['body']))
    chart_table=Table([[panels[0],panels[1]]],colWidths=[CONTENT_W/2]*2)
    chart_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    story.append(chart_table)
    _sub(story,'OVERLAP AND SOURCE LIMITS')
    groups: dict[str,list[str]]={}
    for r in m.records:
        # Industry labels are not reliably stored on individual holdings here;
        # derive via exact source mapping in a later override if supplied.
        label=r.get('industry') or 'Unclassified'
        groups.setdefault(label,[]).append(r['symbol'])
    overlap=[(sector,names) for sector,names in groups.items()
             if sector!='Unclassified' and len(names)>1]
    if overlap:
        for sector,names in overlap:
            _para(story,f'Shared industry: {sector} | {len(names)} holdings | '
                  f'constituents: {", ".join(names)}')
    else:
        _para(story,unavailable('no verified shared-industry relationship identified'))
    _para(story,'An industry assignment from an official Nifty 500 membership CSV '
          'does not establish business-model equivalence. Market-cap buckets '
          'are shown only if explicitly supplied by the provider.')


def _make_page4(story: list[Any], m: AuditModel) -> None:
    _head(story,'04  |  PORTFOLIO RISK ANALYTICS',
          'Retrospective, fixed-current-quantity value path; no reconstruction of transaction timing.')
    r=m.risk
    if r['status']!='VERIFIED':
        story.append(_info_panel('Risk section not calculated',
                                 [unavailable(r.get('reason','missing data'))],color=AMBER))
        return
    metrics=[['ANNUALISED VOL','MAX DRAWDOWN','CURRENT DRAWDOWN','VaR 95% (1D)','ES 95% (1D)'],
             [pct(r['annual_vol_pct']),pct(r['max_dd_pct']),pct(r['current_dd_pct']),
              f'{pct(r["var95_pct"])} / {money(r["var95_rs"])}',
              f'{pct(r["es95_pct"])} / {money(r["es95_rs"])}']]
    story.append(_tbl(metrics,[CONTENT_W/5]*5,row_padding=7))
    _para(story,f'{r["observations"]} aligned daily-return observations, {r["first"]} to {r["last"]}. '
          'VaR is an observed 5th-percentile one-day loss; expected shortfall '
          'averages the worst tail, not a maximum possible loss.',tiny=True)
    _sub(story,'ROLLING 30 / 60-SESSION ANNUALISED VOLATILITY')
    roll=r['rolling'].dropna(how='all')*1.
    if len(roll)>=10:
        try:
            story.append(_lines_chart(roll.tail(220),'Rolling volatility, %',height=159))
        except Exception:
            _para(story,unavailable('rolling volatility chart'))
    else:
        _para(story,unavailable('too few returns for volatility curves'))
    _sub(story,'MARGINAL RISK CONTRIBUTIONS AT CURRENT WEIGHTS')
    contributions=r['risk_contrib']
    if all(val(v) is not None for v in contributions.values()):
        try:
            story.append(_bar_chart(list(contributions),
                                    [100*x for x in contributions.values()],
                                    'Variance contribution (may be negative)',height=142))
        except Exception:
            _para(story,unavailable('risk contribution chart'))
    else:
        _para(story,unavailable('portfolio covariance variance near zero'))
    _sub(story,'PER-SECURITY DRAWDOWN AND ANNUALISED VOLATILITY')
    per=[['SYMBOL','OBSERVATIONS','VOLATILITY','MAX DD','CURRENT DD']]
    for item in m.records:
        prices=item['frame']['Close']
        if not item['valid_technicals'] or len(prices)<60:
            per.append([item['symbol'],'N/A','N/A','N/A','N/A'])
            continue
        v=prices.to_numpy()
        rr=prices.pct_change().dropna().to_numpy()
        per.append([item['symbol'],str(len(rr)),pct(annual_volatility(rr)*100),
                    pct(100*max_drawdown(v)),
                    pct(100*(v[-1]/np.maximum.accumulate(v)[-1]-1))])
    story.append(_tbl(per,[CONTENT_W/5]*5,row_padding=4))


def _make_page5(story: list[Any], m: AuditModel) -> None:
    _head(story,'05  |  BENCHMARKS, STRESS AND ARITHMETIC SCENARIOS',
          'All paths use observed sessions; scenario rows do not assert future outcomes.')
    risk=m.risk
    if risk['status']=='VERIFIED':
        benchmark=risk.get('benchmarks',{})
        rows=[['BENCHMARK','PAIRED N','BETA / CORR','ALPHA*','TRACKING DIFF','RELATIVE DD']]
        for name in ('Nifty 50','Nifty 500'):
            b=benchmark.get(name,{})
            if b.get('sessions'):
                rows.append([name,str(b['sessions']),
                             f'{b["beta"]:.2f} / {b["correlation"]:.2f}',
                             pct(b['alpha_annual_pct']),pct(b['tracking_diff_pct']),
                             pct(b['relative_dd_pct'])])
            else:
                rows.append([name, 'N/A',unavailable(b.get('reason','missing data')),'N/A','N/A','N/A'])
        story.append(_tbl(rows,[77,66,93,70,89,CONTENT_W-395],row_padding=4))
        _para(story,'* Illustrative Jensen alpha = 252 x (daily portfolio mean - beta x daily index mean), '
              'with risk-free rate assumed 0%. Tracking difference is the observed cumulative '
              'percentage-point gap. Relative drawdown uses the portfolio/index relative path.',tiny=True)
        if risk.get('aligned_for_plot') is not None:
            try:
                story.append(_lines_chart(_display_benchmark_window(risk['aligned_for_plot']),
                                          'Portfolio vs Nifty 50 / Nifty 500 (start=100)',height=140))
            except Exception:
                _para(story,unavailable('benchmark chart'))
        else:
            _para(story,unavailable('no aligned index comparison path'))
        if len(risk['monthly']):
            try:
                story.append(_monthly_heatmap(risk['monthly'],height=85))
            except Exception:
                _para(story,unavailable('monthly heatmap'))
    else:
        story.append(_info_panel('Benchmark risk not calculated',
                                 [unavailable(risk.get('reason','price history missing'))]))
    _sub(story,'STATIC SHOCK ARITHMETIC: CASH LOSS AND PORTFOLIO PERCENT')
    pos_values={r['symbol']:float(r['value']) for r in m.records}
    shocks=stress_scenarios(float(m.total_value),pos_values)
    rows=[['POSITION','-10% move','-20% move','-30% move']]
    for sym, values in shocks.items():
        rows.append([sym]+[f'{money(values[k])} / {values[k]/float(m.total_value)*100:.2f}%'
                           for k in (10,20,30)])
    story.append(_tbl(rows,[91,(CONTENT_W-91)/3]*0+[91]+[(CONTENT_W-91)/3]*3,
                      row_padding=3.3,font_size=6.4))
    _sub(story,'ALTERNATIVE WEIGHT ARITHMETIC (LARGEST HOLDING)')
    if risk['status']=='VERIFIED':
        name=max(m.records,key=lambda x:x['weight'])['symbol']
        _para(story,'Illustration: '+name+' weights of 40%, 25%, 15%; residual '
              'weight proportionally allocated among other current holdings '
              '(or nonvolatile cash in a one-security portfolio).',tiny=True)
        story.append(_tbl([['EXAMPLE WEIGHT','MODEL ANNUAL VOLATILITY']]+[
            [pct(w*100),pct(vol)] for w,vol in risk['what_if']],
            [CONTENT_W/2]*2,row_padding=3))
    else:
        _para(story,unavailable('risk covariance needed for alternate weights'))
    if m.tax is not None:
        _para(story,f'Indicative tax on hypothetical realisation (explicit rates/remaining '
              f'exemption supplied): {money(m.tax["illustrative_tax"])}. '
              'Actual tax depends on the complete account and applicable law.',tiny=True)
    else:
        _para(story,unavailable('tax estimate requires all buy dates and explicit tax policy'),tiny=True)


def _make_page6(story: list[Any], m: AuditModel) -> None:
    _head(story,'06  |  DETERMINISTIC RULE FLAGS',
          'Only tests with observed evidence can trigger; each hit reports its number.')
    groups={r['symbol']:[] for r in m.records}
    for f in m.flags:
        groups.setdefault(f.symbol,[]).append(f)
    story.append(_info_panel('Configured numerical thresholds',[
        'Weight >40%; close <EMA50/200; 20D return <-15%; near 20D/52W low (within 2%).',
        'Down day volume >1.5x; ATR14 >5%; RSI14 outside 30-70.',
        'CFO <net profit; debt/equity >1; promoter pledge >20%; promoter holding decline.',
        'Unreconciled daily price gap >40%; price stale >3 trading sessions.'
    ],color=BLUE))
    _sub(story,'OBSERVED FLAGS BY HOLDING')
    for sym,flags in groups.items():
        own=[r for r in m.records if r['symbol']==sym]
        if not own and sym=='ALL':
            continue
        badge=flags[0].severity if flags else 'LOW'
        compact=[f'{f.severity}: {f.rule} [{f.evidence}]' for f in flags]
        if not compact:
            compact=['LOW: no configured threshold triggered [0 rule hits]']
        text='; '.join(compact)
        content=Table([[_p(sym,_styles()['h2']),_severity_mark(badge)]],
                      colWidths=[CONTENT_W-80,80])
        content.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PAPER),
                                     ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                                     ('LEFTPADDING',(0,0),(-1,-1),4)]))
        story.append(KeepTogether([content,_p(text,_styles()['tiny']),Spacer(1,5)]))
    _sub(story,'FLAGS NOT EVALUATED')
    _para(story,'Absent issuer/ownership/market-cap/corporate-action fields do not '
          'pass a risk check by default. They remain MISSING in the source matrix.',tiny=True)



def _company_sub(story: list[Any], title: str) -> None:
    """Compact but orphan-safe company page section heading."""
    style=ParagraphStyle('companySection',fontName='Helvetica-Bold',
                         fontSize=9.1,leading=11.5,textColor=NAVY,
                         spaceBefore=3.5,spaceAfter=1.5,keepWithNext=True)
    story.append(_p(title,style))


def _field_text(financials: Mapping[str, Any], key: str,
                name: Optional[str] = None) -> str:
    f=_evidence(financials.get('metrics',{}).get(key))
    if f['value'] is None:
        return (name or key)+' = '+unavailable(f['reason'])
    return f'{name or key} = {f["value"]:,.2f} ({f["confidence"]}, {f["period"]})'


def _company_trend_and_financials(story: list[Any], r: dict[str, Any]) -> None:
    """Exactly one page of dated technical/issuer facts, no extra page break."""
    source=r['fund']
    url=source.get('source_url') or ''
    if urlparse(url).scheme == 'https' and urlparse(url).hostname == 'www.screener.in':
        _para(story,'Financial evidence: secondary; scope '+html_escape(str(source.get('scope') or 'unavailable'))+
              ' | <link href="'+html_escape(url,quote=True)+'">Source page</link>',tiny=True)
    _company_sub(story,'PRICE HISTORY, MOVING AVERAGES AND 52W RANGE')
    if r['valid_technicals'] and len(r['frame'])>=60:
        close=r['frame']['Close']
        tail=close.iloc[-150:]
        paths=pd.DataFrame({
            'Close':tail,
            'EMA20':close.ewm(span=20,adjust=False).mean().loc[tail.index],
            'EMA50':close.ewm(span=50,adjust=False).mean().loc[tail.index],
        })
        try:
            story.append(_lines_chart(paths,f'{r["symbol"]} | observed dated close and EMA',height=110))
        except Exception:
            _para(story,unavailable('price/EMA chart'))
        position=(f'{pct(r["range_pos"]*100)} of 52-week close range '
                  f'({money(r["low252"])} - {money(r["high252"])})'
                  if r['range_pos'] is not None else unavailable('52W price range'))
        _para(story, '52W position bar: [' + ('#'*int(max(0,min(10,round((r['range_pos'] or 0)*10)))))
              + ('-'*(10-int(max(0,min(10,round((r['range_pos'] or 0)*10))))))
              + '] '+position,tiny=True)
        lo,hi=r['swing_low'],r['swing_high']
        for label,level in [('Local swing low',lo),('Local swing high',hi)]:
            _para(story,f'{label}: {money(level)} | distance from close '
                  f'{pct((level/r["close"]-1)*100,signed=True) if level else "unavailable"}',tiny=True)
        max_20=r['high20']
        to_cost=(r['buy']/dec(r['close'])-1)*100
        recovery=max(0.,100*(max_20/r['close']-1)) if max_20 else None
        _para(story, f'Cost basis {money(r["buy"])} | arithmetic gain to cost '
              f'{max(Decimal(0),to_cost):.1f}% | gain to observed 20D close high '
              f'{pct(recovery)}. These are arithmetic gaps, not predictions.',tiny=True)
    else:
        _para(story,unavailable('insufficient verified adjusted OHLCV history'))
    _company_sub(story,'QUARTERLY TREND AND EARNINGS QUALITY')
    quarters=_quarterly_valid(source.get('quarterly',[]))
    if len(quarters)>=2:
        try:
            story.append(_quarterly_chart(quarters,height=63))
            if len(quarters)>=5:
                story.append(_quarterly_yoy_chart(quarters,height=40))
        except Exception:
            _para(story,unavailable('quarterly mini-chart'))
        if len(quarters)>=5:
            yoy=100*(quarters[-1]['revenue']/quarters[-5]['revenue']-1) if quarters[-5]['revenue']>0 else None
            _para(story,f'Latest revenue YoY vs four quarters earlier: {pct(yoy)} '
                  f'({quarters[-5]["period"]} to {quarters[-1]["period"]}).',tiny=True)
    else:
        _para(story,unavailable('fewer than two comparable dated issuer quarters'),tiny=True)
    fund_cash=financial_value(source,'operating_cash_flow')
    fund_profit=financial_value(source,'net_profit')
    receivable=financial_value(source,'receivable_days')
    ratio=_number_ratio(fund_cash,fund_profit)
    comparable = (not source.get('is_lender') and _comparable_facts(
        _evidence(source.get('metrics',{}).get('operating_cash_flow')),
        _evidence(source.get('metrics',{}).get('net_profit'))))
    if not comparable:
        ratio = None
    divergence=(fund_cash-fund_profit) if comparable else None
    _para(story,f'Operating cash flow / net profit: '
          f'{ratio:.2f}x' if ratio is not None else unavailable('matching dated CFO/profit'),tiny=True)
    _para(story,f'Receivable days: {f"{receivable:.1f}" if receivable is not None else "unavailable"} '
          f'| CFO-profit difference: {money(divergence)}',tiny=True)
    _company_sub(story,'OWNERSHIP, VALUATION AND DIVIDEND CONTEXT')
    ownership=source.get('ownership',[])
    accepted=[o for o in (ownership if isinstance(ownership,list) else [])
              if isinstance(o,Mapping) and _normal_date(o.get('period'))
              and str(o.get('source') or '').strip()]
    accepted=sorted(accepted,key=lambda x:str(x['period']))[-4:]
    if accepted:
        for row in accepted:
            _para(story,f'{str(row["period"])[:10]} | promoter {pct(val(row.get("promoter")))} '
                  f'| pledge {pct(val(row.get("pledge")))} '
                  f'| FII {pct(val(row.get("fii")))} '
                  f'| DII {pct(val(row.get("dii")))}',tiny=True)
        if len(accepted)>=2:
            old,new=accepted[0],accepted[-1]
            changes=[]
            for key in ('promoter','fii','dii'):
                v0,v1=val(old.get(key)),val(new.get(key))
                changes.append(f'{key.upper()} {v1-v0:+.2f} pp'
                               if v0 is not None and v1 is not None
                               else f'{key.upper()} change unavailable')
            _para(story,'Observed ownership-window changes: '+', '.join(changes),tiny=True)
    else:
        _para(story,unavailable('dated four-quarter ownership and promoter-pledge data'),tiny=True)
    f1=financial_value(source,'pe')
    f2=financial_value(source,'pe_median5y')
    f3=financial_value(source,'industry_pe_median')
    _para(story,f'P/E {f"{f1:.2f}x" if f1 is not None else "N/A"}; '
          f'own 5Y median {f"{f2:.2f}x" if f2 is not None else "N/A"}; '
          f'industry median {f"{f3:.2f}x" if f3 is not None else "N/A"}. '
          'All ratios require dated comparable sources.',tiny=True)
    recent=[]
    for event in r['dividends']:
        if not isinstance(event,Mapping):continue
        d=_normal_date(event.get('date'))
        amt=val(event.get('amount_per_share'))
        if (d and amt is not None and amt>=0 and
                str(event.get('source') or '').strip() and
                0<=(date.fromisoformat(r['asof'])-d).days<=365):
            recent.append(amt)
    if recent:
        trailing=sum(recent)
        _para(story,f'Past-12-month dividends {money(trailing)} per share; '
              f'historical yield {pct(100*trailing/r["close"])}; '
              f'historical annual amount at current quantity {money(trailing*float(r["qty"]))}.',tiny=True)
    else:
        _para(story,unavailable('dated verified dividend cash-flow history'),tiny=True)
    _para(story,unavailable('future annual dividend amount not an observed fact'),tiny=True)
    _company_sub(story,'CORPORATE ACTIONS AND UPCOMING DATES')
    actions=[a for a in r['actions'] if isinstance(a,Mapping) and
             _normal_date(a.get('date')) and str(a.get('source') or '').strip()]
    if actions:
        for action in actions[-2:]:
            _para(story,f'{_str(action.get("date"),12)} {_str(action.get("type"),20)} '
                  f'{_str(action.get("detail"),43)} | '
                  'adjusted broker cost: unconfirmed',tiny=True)
    else:
        _para(story,unavailable('no dated split/bonus/action log'),tiny=True)
    for key,label in (('next_results','Next result date'),('ex_dividend','Ex-dividend date')):
        event=r['events'].get(key)
        if (isinstance(event,Mapping) and _normal_date(event.get('date'))
                and str(event.get('source') or '').strip()):
            _para(story,f'{label}: {_str(event["date"],12)} '
                  f'| {_str(event.get("source"),31)} | source verification pending',tiny=True)
        else:
            _para(story,label+': '+unavailable('dated source not supplied'),tiny=True)
    _company_sub(story,'FUNDAMENTAL FIELD PROVENANCE | PER-FIELD CONFIDENCE')
    metrics=source.get('metrics',{})
    keys=('revenue','net_profit','operating_margin','roe','roce','debt_equity',
          'operating_cash_flow','receivable_days','pe','pe_median5y','industry_pe_median')
    records=[['FIELD','VALUE','FISCAL PERIOD','SOURCE','RETRIEVED UTC','TAG']]
    for key in keys:
        item=_evidence(metrics.get(key))
        records.append([key.replace('_',' '),
                        f'{item["value"]:,.2f}' if item.get('value') is not None else 'N/A',
                        item.get('period','N/A'),item.get('source','N/A'),
                        item.get('retrieved','N/A'),item['confidence']])
    story.append(_tbl(records,[103,53,73,115,103,CONTENT_W-447],
                      font_size=6.2,row_padding=.65))
    _para(story,'Retrieval times: '+_str(source.get('retrieved_utc') or 'unknown')+
          '. Missing or undated fields remain MISSING; secondary sources are not issuer audited.',tiny=True)


def _make_company_page(story: list[Any], m: AuditModel, r: dict[str,Any],
                       number: int, company_report_urls: Mapping[str,str]) -> None:
    _head(story,f'{number:02d}  |  COMPANY EVIDENCE: {r["symbol"]}',
          f'Close {money(r["close"])} | {r["asof"]} | weight {pct(r["weight"]*100)} '
          f'| OHLCV confidence {r["meta"]["confidence"]}')
    if not r['valid_technicals']:
        story.append(_info_panel('Market history limitations',[
            unavailable(r['meta']['reason'])],color=AMBER))
    _company_trend_and_financials(story,r)
    url=company_report_urls.get(r['symbol'])
    qr=_qr_or_none(url,size=42)
    if qr is not None:
        story.append(KeepTogether([_p('External company report link (optional QR)',_styles()['tiny']),qr]))


def _make_appendix_a(story: list[Any], m: AuditModel) -> None:
    _head(story,'APPENDIX A  |  DATA LINEAGE AND LAST AUDIT',
          'Source facts, observed coverage and optional previous snapshot - never stored here.')
    _sub(story,'LAST AUDIT DIFFERENCES (OPTIONAL INPUT DICTIONARY)')
    prior=m.snapshot_change
    if prior:
        _para(story,f'Previous total: {money(prior["prior_total"])} | '
              f'Current total: {money(m.total_value)} | '
              f'Observed difference: {money(prior["delta"])}. '
              'Changes reflect both dates/prices and any quantity input differences.')
        for sym,shift in prior['weight_changes']:
            _para(story,f'{sym}: portfolio allocation moved {shift:+.2f} percentage points',tiny=True)
    else:
        _para(story,unavailable('previous dated audit snapshot was not supplied'))
    _sub(story,'FIELD-LEVEL DATA CONFIDENCE')
    for state in ('VERIFIED','SECONDARY','MISSING'):
        _para(story,f'{state}: {m.confidence[state]} audited report input fields')
    _sub(story,'SESSION / SOURCE DATA QUALITY')
    info=[['SYMBOL','AS-OF','PRICE TAG','SESSION LAG','INVALID ROWS','UNADJUSTED GAP']]
    for r in m.records:
        meta=r['meta']
        info.append([r['symbol'],r['asof'],meta['confidence'],
                     str(meta.get('age_sessions')) if meta.get('age_sessions') is not None else 'N/A',
                     str(meta.get('bad_rows',0)),
                     f'{meta["gap"][1]:.1f}%' if meta.get('gap') else 'No flagged gap'])
    story.append(_tbl(info,[70,72,81,74,80,CONTENT_W-377],row_padding=6))
    _para(story,f'Expected completed exchange session: {m.coverage["expected"]}. '
          f'Official Nifty 500 roster rows validated: {m.coverage["constituents"]}. '
          f'Public provider calls unavailable: {m.coverage["source_errors"]}.')
    if m.notes:
        _sub(story,'OBSERVED DATA LIMITS')
        for note in m.notes[:12]:
            _para(story,'- '+note,tiny=True)
    else:
        _para(story,'No additional provider failures were recorded in this report run.')
    _sub(story,'WHAT DATA WAS NOT ASSERTED')
    for item in ('No value was reconstructed for an unpriced position.',
                 'No industry or market-cap bucket was inferred from company size or name.',
                 'No fiscal metric was marked VERIFIED without a fiscal date and issuer reconciliation.',
                 'A missing benchmark suppresses index-relative statistics, not the holdings valuation.',
                 'The optional previous snapshot was read only for this report generation.'):
        _para(story,'- '+item,tiny=True)


def _make_appendix_b(story: list[Any], m: AuditModel, language: str) -> None:
    _head(story,'APPENDIX B  |  PLAIN-LANGUAGE GLOSSARY',
          'A description of metrics and observed evidence, with English / Hindi / Gujarati summary.')
    terms=[
        ('RSI','A 0-100 measure of recent upward vs downward price movement; it is not a recommendation.'),
        ('EMA','Exponential moving average; recent closing prices receive more weight.'),
        ('ATR','Average true range; describes past daily price movement including gaps.'),
        ('Drawdown','The percent fall from an observed previous high in the same dated series.'),
        ('Beta','The historically observed sensitivity of portfolio daily returns vs an index.'),
        ('VaR 95%','Historical one-day loss threshold at the 5th percentile; larger losses can occur.'),
        ('HHI','Sum of squared current position weights; higher HHI means greater concentration.'),
        ('Expected shortfall','Average loss from observations in the worst 5% daily return tail.'),
        ('Alpha','Illustrative annualised excess return after a beta adjustment, with zero risk-free-rate assumption.'),
        ('XIRR','Annualised money-weighted rate if all acquisition dates are provided; here price-only.'),
    ]
    story.append(_tbl([['TERM','MEANING']]+[[term,description] for term,description in terms],
                      [123,CONTENT_W-123],row_padding=4))
    _sub(story,'SHORT SUMMARY | SELECTED LANGUAGE FIRST')
    leader=max(m.records,key=lambda r:r['weight'])
    weight=pct(leader['weight']*100)
    flags=sum(f.severity=='HIGH' for f in m.flags)
    en=[f'The largest holding is {leader["symbol"]}, representing {weight} of last-close value.',
        f'{flags} high-severity numerical rule(s) appeared; missing source facts remain unverified.']
    hi=[f'सबसे बड़ी होल्डिंग {leader["symbol"]} है, जिसका हिस्सा {weight} है।',
        f'{flags} उच्च-स्तर के तथ्य-आधारित संकेत मिले। अनुपलब्ध आंकड़े अप्रमाणित हैं।']
    gu=[f'સૌથી મોટું હોલ્ડિંગ {leader["symbol"]} છે, જેનો હિસ્સો {weight} છે.',
        f'{flags} ઊંચી ગંભીરતાના આંકડાકીય સંકેત છે. ગેરહાજર માહિતી અપ્રમાણિત છે.']
    order=([('hi','HINDI',hi),('en','ENGLISH',en),('gu','GUJARATI',gu)]
           if language=='hi' else
           [('gu','GUJARATI',gu),('en','ENGLISH',en),('hi','HINDI',hi)]
           if language=='gu' else
           [('en','ENGLISH',en),('hi','HINDI',hi),('gu','GUJARATI',gu)])
    for key,label,lines in order:
        _para(story,label,tiny=True)
        if key=='en':
            for item in lines:_para(story,item,tiny=True)
        else:
            try:
                story.append(_script_image(lines,lang=key,height=58))
            except Exception:
                _para(story,unavailable(label+' shaping font; transliteration omitted to avoid garbling'),tiny=True)
    _para(story,'All summary wording is descriptive. It does not express a security preference.',tiny=True)


def _make_appendix_c(story: list[Any], m: AuditModel) -> None:
    _head(story,'APPENDIX C  |  METHODS, ASSUMPTIONS AND LIMITS',
          'Formula notes, provenance requirements and educational-research boundaries.')
    docs=[
        ('Investment/valuation','Sum(qty x entered average purchase cost); '
         'current value = sum(qty x displayed two-decimal verified close); difference = unrealised P/L.'),
        ('Date validation','Business-session freshness with optional NSE holiday calendar; '
         'stale if more than 3 completed trading sessions. Exclude non-finite or invalid OHLCV.'),
        ('Corporate actions','Flag absolute daily close changes above 40%; block risk '
         'statistics unless a verified adjusted history is supplied. Broker cost basis is not inferred.'),
        ('Technical history','RSI uses Wilder smoothing; EMA spans 20, 50, 200; '
         'ATR14 averages true range. 52W uses up to 252 observed closing sessions.'),
        ('Historical analytics','Static current-share-count price path, 252-day annualisation; '
         'drawdown = value / historical running high - 1. No interpolation of missing days.'),
        ('VaR and tail loss','Historical one-day VaR95 = negative 5th percentile of '
         'sample daily portfolio returns; tail mean uses returns below that threshold.'),
        ('Benchmark and alpha','Inner-join observed dates. Beta = sample covariance / '
         'benchmark sample variance. Alpha uses annualised mean return residual '
         'with illustrative risk-free rate 0%, not a performance promise.'),
        ('Risk attribution','Current-value weights x sample annualised return covariance. '
         'Risk contributions can be negative; sums approximate 100% when variance is nonzero.'),
        ('Scenarios','One-security -10/-20/-30% marks move, other prices frozen. '
         'Weight scenarios redistribute the remainder proportionally, not a suggested action.'),
        ('Money-weighted calculation','XIRR (if all buy dates) uses aggregate dated cash '
         'outflows and current terminal marks. Dividends/fees/partial trades excluded.'),
        ('Tax disclosure','Optional unrealised-gain illustration requires all dates and an '
         'explicit policy; no automatic application of exemption, loss set-off or tax residency.'),
        ('Company financials','Individual source, fiscal period and retrieval time '
         'required; Screener.in secondary facts do not count as issuer reconciled.'),
        ('Limitations','No live tradeable quotes, no personal objectives, account-wide '
         'tax status, governance verification, guaranteed returns or trade instructions.'),
    ]
    story.append(_tbl([['METHOD / SCOPE','EXPLANATION']]+[[a,b] for a,b in docs],
                      [134,CONTENT_W-134],row_padding=4.3))
    _sub(story,'SOURCE AND DATA RELIABILITY')
    _para(story,'Expected public inputs: NSE-completed daily OHLCV, Nifty 50/Nifty 500 '
          'index series, official Nifty 500 roster, dated secondary Screener.in '
          'fundamentals, and separately dated dividend/corporate-action records.')
    _para(story,'Historical evidence is not a description of today\'s executable quote. '
          'An index is a comparator, not a representation of transaction costs.')
    _sub(story,'REGULATORY AND PRIVACY NOTE')
    _para(story,'Educational research only. The author/operator does not assert SEBI '
          'registration. Data providers and Telegram have independent retention '
          'policies. This Python module retains no customer portfolio or PDF on disk '
          'unless its caller explicitly requests output.')
    _para(story,'Indicative Indian listed-equity rates may require verification: '
          'Income Tax Department official information at '
          'https://www.incometaxindia.gov.in/en/sale-of-shares. '
          'This audit never assumes personal tax exemptions automatically.',tiny=True)


def create_pdf(model: AuditModel, *, language: str = 'en',
               company_report_urls: Optional[Mapping[str,str]] = None) -> bytes:
    """Produce one A4 10-14-page PDF wholly in memory (no blank sections)."""
    if language not in ('en','hi','gu'):
        raise ValueError('language must be en, hi or gu')
    urls=company_report_urls or {}
    output=io.BytesIO()
    doc=SimpleDocTemplate(output,pagesize=A4,
                          leftMargin=38,rightMargin=38,topMargin=80,bottomMargin=51,
                          title='MSK Portfolio Audit',author='MSK Bot | educational')
    doc.synthetic=model.synthetic
    story: list[Any]=[]
    _make_page1(story,model)
    story.append(PageBreak())
    _make_page2(story,model)
    story.append(PageBreak())
    _make_page3(story,model)
    story.append(PageBreak())
    _make_page4(story,model)
    story.append(PageBreak())
    _make_page5(story,model)
    story.append(PageBreak())
    _make_page6(story,model)
    for index,record in enumerate(model.records,7):
        story.append(PageBreak())
        _make_company_page(story,model,record,index,urls)
    story.append(PageBreak())
    _make_appendix_a(story,model)
    story.append(PageBreak())
    _make_appendix_b(story,model,language)
    story.append(PageBreak())
    _make_appendix_c(story,model)
    on_page=lambda canvas,doc: _page_decor(canvas,doc,model.report_id)
    doc.build(story,onFirstPage=on_page,onLaterPages=on_page)
    data=output.getvalue()
    if not (data.startswith(b'%PDF-') and 10000 <= len(data) <= 8*1024*1024):
        raise ValueError('PDF failed data/size verification')
    return data


@dataclass(frozen=True)
class ReportResult:
    """Immediately disposable PDF bytes and inspectable calculated model."""
    pdf: bytes
    model: AuditModel


def create_report(holdings: Sequence[Holding | Mapping[str, Any]],
                  *, provider: Optional[Provider] = None,
                  dataset: Optional[Dataset] = None,
                  cache: Optional[InMemoryTTLCache] = None,
                  snapshot: Optional[Mapping[str, Any]] = None,
                  language: str = 'en',
                  now: Optional[datetime] = None,
                  holidays: Sequence[date] = (),
                  tax_policy: Optional[TaxPolicy] = None,
                  company_report_urls: Optional[Mapping[str,str]] = None) -> ReportResult:
    """One privacy-safe complete audit, with explicit provider/dataset injection."""
    rows=validate_holdings(list(holdings))
    if dataset is None:
        if provider is None:
            raise ValueError('Provide a market data Provider or verified Dataset')
        dataset=fetch_dataset(rows,provider,cache)
    model=build_model(rows,dataset,now=now,holidays=holidays,
                      snapshot=snapshot,tax_policy=tax_policy)
    pdf=create_pdf(model,language=language,company_report_urls=company_report_urls)
    return ReportResult(pdf,model)

# ---------------------------------------------------------------------------
# Existing MSK Bot integration adapter; import-time optional, no rewrite.
# ---------------------------------------------------------------------------

class MSKRepoProvider:
    """Connect to the *existing* MSK modules if run inside that repository.

    Public market-data/fundamental information is always secondary here unless
    the existing source explicitly proves a dated official NSE/issuer record.
    Historical OHLCV uses 2y requests; both benchmark symbols are external
    quotes, not claims of audited exchange index closes. The adapter has no
    reference to the legacy persistent PortfolioManager or user accounts.
    """
    is_synthetic = False

    def stock_history(self, symbol: str) -> PriceSeries:
        from market_data import bulk_history
        frames = bulk_history([symbol], '2y', chunk_size=1, retries=0,
                              max_fallbacks=0, download_timeout=15)
        if not isinstance(frames,dict):
            raise ValueError('Unexpected market_data return type')
        frame=frames.get(symbol)
        if frame is None:
            frame=frames.get(symbol+'.NS')
        if frame is None:
            raise ValueError('No 2-year NSE chart')
        return PriceSeries(frame=frame,source='MSK 2Y market_data (secondary)',
                           retrieved_utc=datetime.now(timezone.utc).isoformat(),
                           verified_exchange=False)

    def index_history(self, symbol: str) -> PriceSeries:
        from market_data import index_history
        frame=index_history(symbol,'2y',retries=0,timeout=13)
        if frame is None:
            raise ValueError('Index history absent')
        return PriceSeries(frame=frame,source='MSK index_history (secondary)',
                           retrieved_utc=datetime.now(timezone.utc).isoformat())

    def constituents(self) -> Mapping[str, Mapping[str,str]]:
        from portfolio_doctor_peers import official_nifty500_industries
        values=official_nifty500_industries()
        if not 450 <= len(values) <= 550:
            raise ValueError('Official roster incomplete')
        return {s:{'industry':sector,'market_cap_bucket':''}
                for s,sector in values.items()}

    def fundamentals(self, symbol: str) -> Mapping[str,Any]:
        from portfolio_doctor_advanced import collect_fundamentals_no_holdings_cache
        raw=collect_fundamentals_no_holdings_cache(symbol,'', request_timeout=4)
        if not isinstance(raw,dict) or raw.get('status') not in ('PARTIAL','COMPLETE'):
            return {}
        source=raw.get('source') or 'Screener.in (secondary)'
        period=raw.get('annual_period')
        metrics={}
        for key,detail in (raw.get('metrics') or {}).items():
            if isinstance(detail,dict):
                period_label=detail.get('period')
                metrics[key]={
                    'value':detail.get('value'), 'period':period_label,
                    'source':source, 'retrieved_utc':raw.get('retrieved_utc'),
                    'issuer_reconciled':False,
                    'scope':detail.get('scope'), 'unit':detail.get('unit'),
                    'basis':detail.get('basis'), 'source_url':detail.get('source_url'),
                }
        return {'metrics':metrics, 'is_lender':raw.get('is_lender', False),
                'scope':raw.get('scope'), 'source_url':raw.get('source_url'),
                'quarterly':[dict(x, source=source) for x in (raw.get('quarterly') or [])
                             if isinstance(x,dict) and x.get('source')],
                'ownership':[dict(x, source=source) for x in (raw.get('ownership') or [])
                             if isinstance(x,dict) and x.get('source')],
                'retrieved_utc':raw.get('retrieved_utc')}

    def dividends(self, symbol: str) -> Sequence[Mapping[str,Any]]:
        # This secondary feed can be disabled by a private worker's provider
        # boundary. Failure never causes an assumed zero or a false dividend.
        import yfinance as yf
        raw=yf.Ticker(symbol+'.NS').dividends
        if raw is None:
            return []
        return [{'date':d.date().isoformat(),
                 'amount_per_share':float(v),
                 'source':'Yahoo Finance (secondary)'}
                for d,v in raw.tail(20).items() if val(v) is not None]

    def actions(self, symbol: str) -> Sequence[Mapping[str,Any]]:
        import yfinance as yf
        raw=yf.Ticker(symbol+'.NS').actions
        if raw is None or not isinstance(raw,pd.DataFrame) or 'Stock Splits' not in raw:
            return []
        return [{'date':d.date().isoformat(), 'type':'split',
                 'detail':f'provider ratio {float(row["Stock Splits"]):g}x',
                 'source':'Yahoo Finance (secondary)'}
                for d,row in raw.tail(100).iterrows()
                if val(row.get('Stock Splits')) is not None and float(row['Stock Splits'])>0]

    def events(self, symbol: str) -> Mapping[str,Any]:
        # No stable authenticated event calendar in the existing MSK Doctor.
        # Caller can subclass the provider with a dated exchange filing feed.
        return {}


class SyntheticProvider:
    """Deterministic *clearly synthetic* offline NSE-like OHLCV fixture."""
    is_synthetic = True
    def __init__(self, asof: date = date(2026,11,18)):
        self.asof=asof
        self._symbols={'FCL':(47.25,3.8),'SCI':(244.80,2.1),
                       'DLF':(689.35,1.4), '^NSEI':(25800.,.85),
                       '^CRSLDX':(22500.,.93)}
        self._hist: dict[str,pd.DataFrame]={}

    def _history(self,symbol: str) -> pd.DataFrame:
        if symbol in self._hist:
            return self._hist[symbol].copy()
        value,sd=self._symbols[symbol]
        dates=pd.bdate_range(end=self.asof,periods=508)
        seed=sum(ord(ch) for ch in symbol)+791
        rng=np.random.default_rng(seed)
        growth=.00045 if symbol in ('DLF','^NSEI','^CRSLDX') else .00022
        increments=rng.normal(growth,sd/100,len(dates))
        raw=np.exp(np.cumsum(increments))
        close=raw/raw[-1]*value
        swing=rng.uniform(.001,.012,len(dates))
        op=close*(1+rng.normal(0,.002,len(dates)))
        hi=np.maximum(op,close)*(1+swing)
        lo=np.minimum(op,close)*(1-swing)
        vol=rng.integers(80_000,3_000_000,len(dates))
        frame=pd.DataFrame({'Open':op,'High':hi,'Low':lo,
                            'Close':close,'Volume':vol},index=dates)
        self._hist[symbol]=frame
        return frame.copy()

    def stock_history(self,symbol: str) -> PriceSeries:
        return PriceSeries(self._history(symbol),'SYNTHETIC | OFFLINE TEST',
                           '2026-11-19T00:00:00+00:00',False)

    def index_history(self,symbol: str) -> PriceSeries:
        return PriceSeries(self._history(symbol),'SYNTHETIC | INDEX TEST',
                           '2026-11-19T00:00:00+00:00',False)

    def constituents(self) -> Mapping[str,Mapping[str,str]]:
        # 500 synthetic members, never represented as an official live roster.
        values={f'FAKE{i:03d}':{'industry':'Other (synthetic)',
                                 'market_cap_bucket':'Unknown'} for i in range(497)}
        values.update({'FCL':{'industry':'Capital Goods (synthetic)','market_cap_bucket':'Small'},
                       'SCI':{'industry':'Shipping (synthetic)','market_cap_bucket':'Mid'},
                       'DLF':{'industry':'Real Estate (synthetic)','market_cap_bucket':'Large'}})
        return values

    def fundamentals(self,symbol: str) -> Mapping[str,Any]:
        period='2026-03-31'
        def field(v:float) -> dict[str,Any]:
            return {'value':v,'period':period,'source':'SYNTHETIC secondary test',
                    'retrieved_utc':'2026-11-18T18:40:00Z',
                    'issuer_reconciled':False}
        factors={'FCL':(.7,1.2,22.),'SCI':(5.6,4.1,14.),'DLF':(8.3,4.8,12.)}
        a,b,c=factors[symbol]
        metrics={'revenue':field(a*1000),'net_profit':field(b*90),
                 'operating_margin':field(c),'roe':field(13.5),
                 'roce':field(16.2),'debt_equity':field(.42),
                 'operating_cash_flow':field(b*70),'receivable_days':field(88),
                 'pe':field(19.), 'pe_median5y':field(22.),
                 'industry_pe_median':field(24.), 'promoter_pledge_pct':field(0)}
        quarters=[]
        for i in range(8):
            year=2024+i//4
            month=[6,9,12,3][i%4]
            if month==3:year+=1
            quarters.append({'period':date(year,month,30 if month in (6,9) else
                                            31).isoformat(),
                             'revenue':a*125*(1+.06*i),
                             'profit':b*12*(1+.055*i),'margin':c+.12*i,
                             'source':'SYNTHETIC secondary test'})
        ownership=[{'period':f'{yr}-{month:02d}-30',
                    'promoter':53.4,'pledge':0.,'fii':11.+n,'dii':8.+.2*n,
                    'source':'SYNTHETIC secondary test'}
                   for n,(yr,month) in enumerate(((2025,9),(2025,12),(2026,3),(2026,6)))]
        return {'metrics':metrics,'quarterly':quarters,'ownership':ownership,
                'retrieved_utc':'2026-11-18T18:40:00Z'}

    def dividends(self,symbol: str) -> Sequence[Mapping[str,Any]]:
        return [{'date':'2026-08-01','amount_per_share':1.5,
                 'source':'SYNTHETIC dividend test'}]

    def actions(self,symbol: str) -> Sequence[Mapping[str,Any]]:
        return []

    def events(self,symbol: str) -> Mapping[str,Any]:
        return {}


# ---------------------------------------------------------------------------
# Pure, pytest-style core assertions run using --test (no pytest dependency).
# ---------------------------------------------------------------------------

def test_pnl_and_money_reconcile() -> None:
    prices=[Decimal('44.63'),Decimal('256.41'),Decimal('764.30')]
    h=[Holding('FCL',Decimal('40000'),Decimal('15')),
       Holding('SCI',Decimal('150'),Decimal('140.5')),
       Holding('DLF',Decimal('50'),Decimal('458'))]
    invested=sum((x.quantity*x.avg_buy for x in h),Decimal('0'))
    current=sum((x.quantity*p for x,p in zip(h,prices)),Decimal('0'))
    assert invested == Decimal('643975.0'),invested
    assert current == Decimal('1861876.50'),current
    assert current-invested == Decimal('1217901.50')


def test_weights_sum_exactly_100() -> None:
    items=np.array([1785200.,38461.5,38215.])
    weights=items/items.sum()
    assert abs(weights.sum()*100-100)<1e-12
    assert abs(hhi(weights)-sum(weights*weights))<1e-12


def test_hhi_and_effective_count() -> None:
    assert abs(hhi([.5,.5])-.5)<1e-12
    assert abs(1/hhi([.5,.5])-2)<1e-12
    assert abs(hhi([1.])-1)<1e-12


def test_drawdown() -> None:
    assert abs(max_drawdown([100,120,90,99])+.25)<1e-12
    assert abs(max_drawdown([100,101,102])-0)<1e-12


def test_volatility() -> None:
    sample=[.01,-.02,.005,.02,-.015,.003]
    assert abs(annual_volatility(sample)-np.std(sample,ddof=1)*np.sqrt(252))<1e-12


def test_beta() -> None:
    benchmark=np.array([.01,-.02,.03,-.008,.015])
    portfolio=benchmark*1.5+.002
    assert abs(beta_against(portfolio,benchmark)-1.5)<1e-12


def test_var_and_es() -> None:
    arr=np.linspace(-.1,.1,101)
    v,e=var_and_es(arr)
    assert abs(v-.09)<1e-12
    assert e >= v


def test_stress_scenarios() -> None:
    data=stress_scenarios(100000.,{'SAMPLEA':60000.,'SAMPLEB':40000.})
    assert data['SAMPLEA'][10]==6000.
    assert data['ALL POSITIONS'][30]==30000.
    assert sum(data[name][20] for name in ('SAMPLEA','SAMPLEB'))==data['ALL POSITIONS'][20]



def test_xirr_and_tax_require_dated_inputs() -> None:
    undated=[Holding('SAMPLEA',Decimal('10'),Decimal('100'))]
    dated=[Holding('SAMPLEA',Decimal('10'),Decimal('100'),date(2025,11,18))]
    assert _xirr(undated,{'SAMPLEA':121.},date(2026,11,18)) is None
    value=_xirr(dated,{'SAMPLEA':121.},date(2026,11,18))
    assert value is not None and .20 < value < .22
    assert indicative_tax(undated,{'SAMPLEA':121.},date(2026,11,18),TaxPolicy()) is None
    assert indicative_tax(dated,{'SAMPLEA':121.},date(2026,11,18),None) is None
    assert indicative_tax(dated,{'SAMPLEA':121.},date(2026,11,18),TaxPolicy()) is not None


def test_stale_gap_and_undated_fundamentals_fail_closed() -> None:
    provider=SyntheticProvider()
    original=provider.stock_history('FCL')
    frame,meta=clean_history(original,date(2026,11,18))
    assert frame is not None and meta['confidence']=='SECONDARY'
    _,stale=clean_history(original,date(2026,11,27))
    assert stale['confidence']=='MISSING' and 'stale' in stale['reason']
    altered=original.frame.copy()
    ix=altered.index[-1]
    altered.loc[ix,'Open']*=.3
    altered.loc[ix,'High']*=.3
    altered.loc[ix,'Low']*=.3
    altered.loc[ix,'Close']*=.3
    _,gap=clean_history(PriceSeries(altered,'SYNTHETIC','2026-11-19'),date(2026,11,18))
    assert gap['confidence']=='MISSING' and gap['gap'][1]>40
    assert _evidence({'value':15,'source':'Screener.in (secondary)'})['confidence']=='MISSING'
    assert _evidence({'value':15,'source':'Issuer', 'period':'2026-03-31',
                      'issuer_reconciled':True})['confidence']=='VERIFIED'


def test_paired_trailing_uses_identical_sessions() -> None:
    provider=SyntheticProvider()
    stock=provider.stock_history('FCL').frame
    index=provider.index_history('^NSEI').frame
    a,b=paired_trailing(stock,index,21)
    assert a is not None and b is not None
    stale=index.iloc[:-1]
    assert paired_trailing(stock,stale,21)==(None,None)


def test_document_is_10_to_14_pages_synthetic() -> None:
    dataset=fetch_dataset(validate_holdings([
        {'symbol':'FCL','qty':40000,'buy':15},
        {'symbol':'SCI','qty':150,'buy':140.5},
        {'symbol':'DLF','qty':50,'buy':458}]), SyntheticProvider())
    model=build_model([{'symbol':'FCL','qty':40000,'buy':15},
                       {'symbol':'SCI','qty':150,'buy':140.5},
                       {'symbol':'DLF','qty':50,'buy':458}],dataset,
                       now=datetime(2026,11,19,10,0,tzinfo=IST))
    assert round(sum(r['weight'] for r in model.records)*100,8)==100
    assert model.total_invested==Decimal('643975.00')
    assert all(r['asof']=='2026-11-18' for r in model.records)
    assert all(r['meta']['confidence'] == 'SECONDARY' for r in model.records)
    payload=create_pdf(model)
    assert payload.startswith(b'%PDF-')
    try:
        from pypdf import PdfReader
    except ImportError:
        return
    pages=PdfReader(io.BytesIO(payload)).pages
    assert len(pages)==12, 'Expected 6 core + 3 holdings + 3 appendix pages, got '+str(len(pages))
    texts=[p.extract_text() or '' for p in pages]
    assert all(len(t)>100 for t in texts), 'Empty or partial PDF page'
    assert sum('COMPANY EVIDENCE:' in t for t in texts)==3
    assert all(model.report_id in t for t in texts)


def run_tests() -> None:
    """Run all offline assert-style tests without accessing live exchanges."""
    tests=[test_pnl_and_money_reconcile,test_weights_sum_exactly_100,
           test_hhi_and_effective_count,test_drawdown,test_volatility,
           test_beta,test_var_and_es,test_stress_scenarios,
           test_xirr_and_tax_require_dated_inputs,
           test_stale_gap_and_undated_fundamentals_fail_closed,
           test_paired_trailing_uses_identical_sessions,
           test_document_is_10_to_14_pages_synthetic]
    for t in tests:
        t()
        print('PASS',t.__name__)
    print(f'PASS {len(tests)} complete offline tests')


def main() -> None:
    """Offline mock, or explicit demo save; no network and no default disk I/O."""
    parser=argparse.ArgumentParser(description='MSK offline Portfolio Audit module')
    parser.add_argument('--output',help='Explicit output path for SYNTHETIC offline demo PDF')
    parser.add_argument('--test',action='store_true',help='Run offline validation tests')
    parser.add_argument('--language',choices=['en','hi','gu'],default='en')
    args=parser.parse_args()
    if args.test:
        run_tests()
        return
    mock=[{'symbol':'FCL','quantity':40000,'avg_buy':15},
          {'symbol':'SCI','quantity':150,'avg_buy':140.5},
          {'symbol':'DLF','quantity':50,'avg_buy':458}]
    result=create_report(mock,provider=SyntheticProvider(),language=args.language,
                         now=datetime(2026,11,19,10,0,tzinfo=IST))
    print('SYNTHETIC OFFLINE REPORT - NOT REAL MARKET OR CUSTOMER DATA')
    print('Report ID:',result.model.report_id)
    print('Invested:',money(result.model.total_invested),
          '| Value:',money(result.model.total_value),
          '| P/L:',money(result.model.total_pnl))
    print('PDF created in BytesIO:',len(result.pdf),'bytes')
    if args.output:
        with open(args.output,'wb') as handle:  # intentional opt-in write
            handle.write(result.pdf)
        print('Explicit synthetic demo saved:',args.output)


if __name__ == '__main__':
    main()

