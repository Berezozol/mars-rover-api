"""In-memory per-key request limiter with several sliding windows (docs/API.md, stage 9).

Each window keeps, per key, a short queue of buckets ``[first_hit_time, count]``. A bucket
collects the hits made within ``granularity`` seconds of its first hit and leaves the window
``window`` seconds after that first hit. So hits are forgotten at most ``granularity`` early
(1 s for a minute window, 10 s for an hour window) and a key costs at most
``window / granularity + 1`` buckets per window, whatever its request rate.

Keys whose buckets have all expired are swept periodically; the number of keys is also
capped (least recently used keys are dropped first). Not thread-safe: meant for one event
loop, where the methods run without awaiting.
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]

# Buckets per window at most (granularity = window / this, but never below 1 s).
_BUCKETS_PER_WINDOW = 360
_MIN_GRANULARITY = 1.0
DEFAULT_MAX_KEYS = 100_000


@dataclass
class _Window:
    limit: int
    length: float
    granularity: float


@dataclass(frozen=True)
class RateState:
    """Result of one hit: ``wait`` is None if allowed, else seconds until a hit fits.

    ``limit``/``remaining``/``reset`` describe the tighter window after this request.
    """

    wait: float | None
    limit: int
    remaining: int
    reset: int


@dataclass
class _Counter:
    """Buckets of one key in one window, oldest first; ``total`` is their sum."""

    buckets: deque[list[float]] = field(default_factory=deque)
    total: int = 0

    def expire(self, now: float, length: float) -> None:
        while self.buckets and self.buckets[0][0] + length <= now:
            self.total -= int(self.buckets.popleft()[1])

    def add(self, now: float, granularity: float) -> None:
        if self.buckets and now - self.buckets[-1][0] < granularity:
            self.buckets[-1][1] += 1
        else:
            self.buckets.append([now, 1])
        self.total += 1

    def reset_in(self, now: float, window: _Window) -> int:
        """Whole seconds (rounded up) until the oldest hit leaves the window; 0 if empty."""
        if not self.buckets:
            return 0
        return max(0, math.ceil(self.buckets[0][0] + window.length - now))

    def wait_time(self, now: float, window: _Window) -> float | None:
        """Seconds until one more hit fits, None if it fits now (buckets already expired)."""
        excess = self.total - window.limit + 1
        if excess <= 0:
            return None
        for started, count in self.buckets:
            excess -= int(count)
            if excess <= 0:
                return started + window.length - now
        return window.length  # unreachable: the buckets sum to total


class RateLimiter:
    """Allows ``limit`` hits per ``window`` seconds per key, for every window at once."""

    def __init__(
        self,
        windows: list[tuple[int, float]],
        clock: Clock = time.monotonic,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        max_concurrent: int | None = None,
    ) -> None:
        if not windows:
            raise ValueError("At least one window is required")
        self._windows = [_window(limit, length) for limit, length in windows]
        self._max_length = max(window.length for window in self._windows)
        self._max_keys = max_keys
        # Public: tests replace it with a fake clock.
        self.clock = clock
        self._keys: OrderedDict[str, list[_Counter]] = OrderedDict()
        self._next_sweep: float | None = None
        # Requests being served per key; None = no concurrency limit.
        self.max_concurrent = max_concurrent
        self._in_flight: dict[str, int] = {}

    def enter(self, key: str) -> bool:
        """Take a concurrency slot; False if the key already has ``max_concurrent`` requests
        in flight. Every True must be paired with ``leave``."""
        running = self._in_flight.get(key, 0)
        if self.max_concurrent is not None and running >= self.max_concurrent:
            return False
        self._in_flight[key] = running + 1
        return True

    def leave(self, key: str) -> None:
        running = self._in_flight.get(key, 0) - 1
        if running > 0:
            self._in_flight[key] = running
        else:
            self._in_flight.pop(key, None)

    def peek(self, key: str) -> RateState:
        """The window state without counting a hit (``wait`` is None)."""
        return self._state(key, self.clock(), None)

    def acquire(self, key: str) -> RateState:
        """Count a hit if allowed (a rejected one is not counted); return the window state."""
        now = self.clock()
        wait = self._wait_time(key, now)
        if wait is None:
            self._add(key, now)
        return self._state(key, now, wait)

    def hit(self, key: str) -> float | None:
        """Count a hit; None if allowed, else seconds to wait (the hit is not counted)."""
        return self.acquire(key).wait

    def check(self, key: str) -> float | None:
        """Like ``hit`` without counting: None if a hit would be allowed now."""
        return self._wait_time(key, self.clock())

    def record(self, key: str) -> None:
        """Count a hit without checking the limit."""
        self._add(key, self.clock())

    def reset(self) -> None:
        self._keys.clear()
        self._next_sweep = None

    def __len__(self) -> int:
        """Number of keys currently tracked."""
        return len(self._keys)

    def _wait_time(self, key: str, now: float) -> float | None:
        self._maybe_sweep(now)
        counters = self._keys.get(key)
        if counters is None:
            return None
        waits = [
            wait
            for counter, window in zip(counters, self._windows, strict=True)
            if (wait := _expired(counter, now, window).wait_time(now, window)) is not None
        ]
        return max(waits) if waits else None

    def _state(self, key: str, now: float, wait: float | None) -> RateState:
        """The tighter window after this request: fewest remaining, then the later reset.

        On a full tie the first (shortest) window wins.
        """
        # Defensive: the key is normally tracked by now (a rejected hit implies existing
        # counters); an untracked key just reports empty windows.
        counters = self._keys.get(key) or [_Counter() for _ in self._windows]
        states: list[RateState] = []
        for counter, window in zip(counters, self._windows, strict=True):
            _expired(counter, now, window)
            remaining = max(0, window.limit - counter.total)
            states.append(RateState(wait, window.limit, remaining, counter.reset_in(now, window)))
        best = states[0]  # __init__ guarantees at least one window
        for state in states[1:]:
            if state.remaining < best.remaining or (
                state.remaining == best.remaining and state.reset > best.reset
            ):
                best = state
        return best

    def _add(self, key: str, now: float) -> None:
        counters = self._keys.get(key)
        if counters is None:
            counters = [_Counter() for _ in self._windows]
            self._keys[key] = counters
            self._evict_overflow()
        else:
            self._keys.move_to_end(key)
        for counter, window in zip(counters, self._windows, strict=True):
            _expired(counter, now, window).add(now, window.granularity)

    def _evict_overflow(self) -> None:
        while len(self._keys) > self._max_keys:
            self._keys.popitem(last=False)

    def _maybe_sweep(self, now: float) -> None:
        """Every ``max window`` seconds drop the keys whose hits have all expired."""
        if self._next_sweep is None:
            self._next_sweep = now + self._max_length
            return
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._max_length
        for key in [key for key, counters in self._keys.items() if self._idle(counters, now)]:
            del self._keys[key]

    def _idle(self, counters: list[_Counter], now: float) -> bool:
        for counter, window in zip(counters, self._windows, strict=True):
            if _expired(counter, now, window).total:
                return False
        return True


def _window(limit: int, length: float) -> _Window:
    if limit < 1 or length <= 0:
        raise ValueError("A window needs a limit >= 1 and a positive length")
    return _Window(limit, float(length), max(_MIN_GRANULARITY, length / _BUCKETS_PER_WINDOW))


def _expired(counter: _Counter, now: float, window: _Window) -> _Counter:
    counter.expire(now, window.length)
    return counter
