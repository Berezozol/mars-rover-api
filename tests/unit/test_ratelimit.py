"""In-memory per-key rate limiter (docs/API.md, "Ограничение частоты"; stage 9).

Agreed interface (app/core/ratelimit.py):
- ``RateLimiter(windows: list[tuple[int, float]], clock=time.monotonic)``: every window is
  ``(limit, seconds)``; all windows apply at once.
- ``hit(key) -> float | None``: ``None`` = allowed and counted; otherwise the seconds until a
  slot frees up, and the rejected call is NOT counted.
- ``check(key)`` -- the same answer as ``hit`` without counting; ``record(key)`` counts.
- ``reset()`` forgets everything; ``clock`` is a public, replaceable attribute.

Time is driven by a fake clock; nothing sleeps.
"""

from collections.abc import Iterable

import pytest

from app.core.ratelimit import RateLimiter

pytestmark = pytest.mark.unit

START = 10_000.0
EPS = 0.001


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(windows: list[tuple[int, float]], clock: Clock | None = None) -> RateLimiter:
    return RateLimiter(windows, clock=clock or Clock())


def hit_n(limiter: RateLimiter, key: str, n: int) -> list[float | None]:
    return [limiter.hit(key) for _ in range(n)]


# --- one window ----------------------------------------------------------------------------


def test_hit_under_limit_returns_none() -> None:
    limiter = make([(3, 60)])

    assert hit_n(limiter, "a", 3) == [None, None, None]


def test_hit_over_limit_returns_seconds_until_free() -> None:
    clock = Clock()
    limiter = make([(3, 60)], clock)
    hit_n(limiter, "a", 3)

    retry = limiter.hit("a")

    assert retry is not None
    assert retry == pytest.approx(60, abs=EPS)


def test_hit_retry_counts_from_oldest_hit_in_window() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    limiter.hit("a")  # t=0
    clock.advance(10)
    limiter.hit("a")  # t=10
    clock.advance(20)  # t=30

    retry = limiter.hit("a")

    assert retry == pytest.approx(30, abs=EPS)


def test_hit_after_retry_seconds_is_allowed_again() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    hit_n(limiter, "a", 2)
    clock.advance(25)
    retry = limiter.hit("a")
    assert retry is not None

    clock.advance(retry - 0.01)
    assert limiter.hit("a") is not None, "still inside the window just before retry"

    clock.advance(0.01 + EPS)
    assert limiter.hit("a") is None


def test_hit_rejected_calls_are_not_counted() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    hit_n(limiter, "a", 2)
    # Hammering while blocked must not push the release time further away.
    for _ in range(50):
        clock.advance(1)
        assert limiter.hit("a") is not None

    clock.advance(60 - 50 + EPS)  # 60 s after the two accepted hits

    assert hit_n(limiter, "a", 2) == [None, None]
    assert limiter.hit("a") is not None


def test_hit_window_slides_one_slot_at_a_time() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    limiter.hit("a")  # t=0
    clock.advance(30)
    limiter.hit("a")  # t=30
    clock.advance(30 + EPS)  # t=60+: only the first hit expired

    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None


# --- two windows ---------------------------------------------------------------------------


def test_hit_minute_window_blocks_before_hour_window() -> None:
    clock = Clock()
    limiter = make([(3, 60), (5, 3600)], clock)
    assert hit_n(limiter, "a", 3) == [None, None, None]

    retry = limiter.hit("a")

    assert retry == pytest.approx(60, abs=EPS)


def test_hit_hour_window_blocks_after_minute_window_frees() -> None:
    clock = Clock()
    limiter = make([(3, 60), (5, 3600)], clock)
    hit_n(limiter, "a", 3)  # t=0
    clock.advance(61)
    assert hit_n(limiter, "a", 2) == [None, None]  # t=61, hour window now full (5)

    retry = limiter.hit("a")

    assert retry is not None
    assert retry == pytest.approx(3600 - 61, abs=EPS)


def test_hit_hour_window_frees_after_hour() -> None:
    clock = Clock()
    limiter = make([(3, 60), (5, 3600)], clock)
    hit_n(limiter, "a", 3)  # t=0
    clock.advance(61)
    hit_n(limiter, "a", 2)  # t=61
    clock.advance(3600 - 61 + EPS)  # the three hits of t=0 left the hour window

    assert hit_n(limiter, "a", 3) == [None, None, None]
    assert limiter.hit("a") is not None  # hour: 2 + 3 = 5 again


def test_hit_retry_when_both_windows_full_is_the_longer_wait() -> None:
    clock = Clock()
    limiter = make([(2, 60), (2, 3600)], clock)
    hit_n(limiter, "a", 2)

    retry = limiter.hit("a")

    assert retry == pytest.approx(3600, abs=EPS)


# --- keys ----------------------------------------------------------------------------------


def test_hit_different_keys_are_independent() -> None:
    limiter = make([(1, 60)])
    assert limiter.hit("10.0.0.1") is None
    assert limiter.hit("10.0.0.1") is not None

    assert limiter.hit("10.0.0.2") is None
    assert limiter.hit("10.0.0.1") is not None


# --- check / record / reset ----------------------------------------------------------------


def test_check_does_not_count() -> None:
    limiter = make([(2, 60)])

    for _ in range(10):
        assert limiter.check("a") is None

    assert hit_n(limiter, "a", 2) == [None, None]


def test_record_counts_and_check_reports_block() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    limiter.record("a")
    assert limiter.check("a") is None
    limiter.record("a")

    retry = limiter.check("a")

    assert retry == pytest.approx(60, abs=EPS)
    assert limiter.hit("a") is not None
    clock.advance(60 + EPS)
    assert limiter.check("a") is None


def test_check_matches_hit_answer_when_blocked() -> None:
    clock = Clock()
    limiter = make([(1, 60)], clock)
    limiter.hit("a")
    clock.advance(15)

    assert limiter.check("a") == pytest.approx(45, abs=EPS)
    assert limiter.hit("a") == pytest.approx(45, abs=EPS)


def test_reset_forgets_all_keys() -> None:
    limiter = make([(1, 60)])
    limiter.hit("a")
    limiter.hit("b")

    limiter.reset()

    assert limiter.hit("a") is None
    assert limiter.hit("b") is None


def test_clock_attribute_is_replaceable() -> None:
    limiter = RateLimiter([(1, 60)])
    clock = Clock()
    limiter.clock = clock
    limiter.hit("a")
    assert limiter.hit("a") is not None

    clock.advance(60 + EPS)

    assert limiter.hit("a") is None


# --- memory --------------------------------------------------------------------------------


def _stored_items(limiter: RateLimiter) -> int:
    """Total size of the limiter's containers: a proxy for the number of tracked keys.

    The layout is private; whatever dicts, lists, sets or deques it keeps are counted one
    level deep (a dict of per-key deques counts keys plus timestamps).
    """
    total = 0
    for value in vars(limiter).values():
        if isinstance(value, dict):
            total += len(value)
            total += sum(_size(v) for v in value.values())
        elif isinstance(value, list | set | tuple) and not _is_windows(value):
            total += len(value)
            total += sum(_size(v) for v in value)
        else:
            total += _size(value)
    return total


def _size(value: object) -> int:
    if isinstance(value, dict | list | set | tuple) or hasattr(value, "__len__"):
        try:
            return len(value)  # type: ignore[arg-type]
        except TypeError:
            return 0
    return 0


def _is_windows(value: Iterable[object]) -> bool:
    items = list(value)
    return bool(items) and all(isinstance(item, tuple) and len(item) == 2 for item in items)


def test_hit_expired_keys_are_purged() -> None:
    """Many one-off clients (IPs) must not make the memory grow forever."""
    clock = Clock()
    limiter = make([(60, 60), (2000, 3600)], clock)
    for n in range(5000):
        limiter.hit(f"old-{n}")
    loaded = _stored_items(limiter)
    assert loaded >= 5000  # sanity: the proxy sees the stored keys

    # Long after every window has passed, a stream of new clients over ~20 minutes.
    clock.advance(2 * 3600)
    for n in range(1200):
        clock.advance(1)
        limiter.hit(f"new-{n}")

    # 1200 live keys against 5000 expired ones: without purging the size would only grow.
    assert _stored_items(limiter) < loaded / 2, "expired keys are still kept"
    # The old keys are free again (if they were dropped, their history is gone).
    assert limiter.hit("old-0") is None
