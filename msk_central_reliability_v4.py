"""Shadow-only bounded cache, request coalescing and provider failure isolation.

No background jobs, database, network or bot integration. Provider timeouts
must be enforced in each actual transport. Cache keys include the exact
evidence identity, as-of day and caller-owned revision namespace.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
import math
from threading import Lock, Event
from time import monotonic

from msk_central_evidence_v4 import EvidenceKey, Resolution, valid_receipt


@dataclass
class _Flight:
    done: Event
    result: Resolution | None = None
    invalidated: bool = False


class EvidenceGateway:
    def __init__(self, *, capacity=2048, max_inflight=8, ttl=300,
                 wait_timeout=5, clock=monotonic):
        for value, limit in ((capacity, 10000), (max_inflight, 32)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("invalid resource limit")
        for value, limit in ((ttl, 3600), (wait_timeout, 30)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= limit:
                raise ValueError("invalid time limit")
        self.capacity, self.max_inflight = capacity, max_inflight
        self.ttl, self.wait_timeout, self.clock = ttl, wait_timeout, clock
        self._lock = Lock()
        self._cache = OrderedDict()
        self._flights = {}

    def get(self, key, *, as_of, revision, loader):
        if (not isinstance(key, EvidenceKey) or type(as_of) is not date
                or not isinstance(revision, str) or not revision or len(revision) > 128):
            raise ValueError("invalid cache identity")
        if key.period_end > as_of:
            return Resolution("withheld_future_period")
        identity = (key, as_of, revision)
        with self._lock:
            cached = self._cache.pop(identity, None)
            if cached is not None and self.clock() < cached[0]:
                self._cache[identity] = cached
                return cached[1]
            flight = self._flights.get(identity)
            owner = flight is None
            if owner:
                if len(self._flights) >= self.max_inflight:
                    return Resolution("withheld_busy")
                flight = _Flight(Event())
                self._flights[identity] = flight
        if not owner:
            if not flight.done.wait(self.wait_timeout):
                return Resolution("withheld_wait_timeout")
            return flight.result or Resolution("withheld_provider_failure")
        result = Resolution("withheld_provider_failure")
        try:
            candidate = loader()
            if isinstance(candidate, Resolution):
                if candidate.status != "verified":
                    result = candidate
                elif (candidate.receipts and
                      all(valid_receipt(r, key, as_of) for r in candidate.receipts) and
                      len({r.value for r in candidate.receipts}) == 1):
                    # The loader must be the independently verified resolver;
                    # this cache rechecks shape/identity, not source bytes.
                    result = candidate
        except Exception:
            pass
        finally:
            with self._lock:
                try:
                    if flight.invalidated:
                        result = Resolution("withheld_revision_changed")
                    elif result.status == "verified":
                        self._cache[identity] = (self.clock() + self.ttl, result)
                        while len(self._cache) > self.capacity:
                            self._cache.popitem(last=False)
                finally:
                    flight.result = result
                    self._flights.pop(identity, None)
                    flight.done.set()
        return result

    def invalidate(self, key):
        """Call on a new filing or correction, including during a fetch."""
        with self._lock:
            for identity in tuple(self._cache):
                if identity[0] == key:
                    del self._cache[identity]
            for identity, flight in self._flights.items():
                if identity[0] == key:
                    flight.invalidated = True


class ProviderUnavailable(RuntimeError):
    pass


class CircuitBreaker:
    """One instance per provider; process-local, not a durable retry budget.

    Wrap a bounded transport call. Exceptions count as failures; callers must
    raise for transport/HTTP failures instead of returning a successful empty
    response. After cooldown only one probe may run.
    """
    def __init__(self, *, threshold=3, cooldown=60, clock=monotonic):
        if type(threshold) is not int or not 1 <= threshold <= 10:
            raise ValueError("invalid threshold")
        if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or not 0 < cooldown <= 3600:
            raise ValueError("invalid cooldown")
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self._lock = Lock()
        self._failures = 0
        self._until = None
        self._probing = False
        self._generation = 0

    def call(self, fn, *args, **kwargs):
        with self._lock:
            if self._until is not None:
                if self.clock() < self._until or self._probing:
                    raise ProviderUnavailable("provider circuit open")
                self._probing = True
            generation = self._generation
        try:
            result = fn(*args, **kwargs)
        except BaseException:
            with self._lock:
                if generation == self._generation:
                    self._failures += 1
                    if self._probing or self._failures >= self.threshold:
                        self._until = self.clock() + self.cooldown
                        self._generation += 1
                    self._probing = False
            raise
        with self._lock:
            if generation == self._generation:
                self._failures = 0
                self._until = None
                self._probing = False
        return result
