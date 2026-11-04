"""Safe recovery primitives for MSK services.

Only idempotent callable operations may be retried. No code rewriting, deployment,
portfolio writes, trading orders, Telegram sends or data fabrication.
"""
from __future__ import annotations
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")
class RecoveryExhausted(RuntimeError):
    pass

@dataclass
class CircuitBreaker:
    threshold: int = 3
    reset_seconds: float = 120.0
    failures: int = 0
    opened_at: float | None = None

    def allowed(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.opened_at is None or now - self.opened_at >= self.reset_seconds

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self, now: float | None = None) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic() if now is None else now

def retry_read_only(operation: Callable[[], T], *, attempts: int = 3,
                    base_delay: float = 0.25, max_delay: float = 2.0,
                    breaker: CircuitBreaker | None = None,
                    retryable: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Retry only explicitly supplied read-only operations.

    Caller must guarantee idempotency. Non-transient errors fail immediately.
    """
    if not 1 <= attempts <= 5 or base_delay < 0 or max_delay < base_delay:
        raise ValueError("invalid bounded retry settings")
    breaker = breaker or CircuitBreaker()
    if not breaker.allowed():
        raise RecoveryExhausted("circuit open; read-only provider unavailable")
    for attempt in range(attempts):
        try:
            result = operation()
        except retryable as exc:
            breaker.failure()
            if not breaker.allowed() or attempt == attempts - 1:
                raise RecoveryExhausted("read-only recovery exhausted") from exc
            sleep(min(max_delay, base_delay * (2 ** attempt)))
        else:
            breaker.success()
            return result
    raise AssertionError("unreachable")

def validate_market_snapshot(snapshot: dict, *, required: tuple[str, ...] = ("symbol", "asof", "price")) -> bool:
    """Fail closed: caller must separately verify source provenance and trading session."""
    import math
    if not isinstance(snapshot, dict) or any(not snapshot.get(k) for k in required):
        return False
    price = snapshot.get("price")
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0
