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


# --- acquire / RateState (stage 18D) -------------------------------------------------------


def test_acquire_single_window_decrements_remaining_by_one() -> None:
    limiter = make([(3, 60)])

    states = [limiter.acquire("a") for _ in range(3)]

    assert [(s.wait, s.limit, s.remaining) for s in states] == [
        (None, 3, 2),
        (None, 3, 1),
        (None, 3, 0),
    ]


def test_acquire_minute_window_tighter_reports_minute() -> None:
    limiter = make([(3, 60), (1000, 3600)])

    state = limiter.acquire("a")

    assert (state.limit, state.remaining) == (3, 2)
    assert state.reset == 60


def test_acquire_hour_window_tighter_reports_hour() -> None:
    limiter = make([(10, 60), (4, 3600)])

    state = limiter.acquire("a")

    assert (state.limit, state.remaining) == (4, 3)
    assert 3590 <= state.reset <= 3600


def test_acquire_tighter_window_switches_after_minute_frees() -> None:
    clock = Clock()
    limiter = make([(3, 60), (5, 3600)], clock)
    first = [limiter.acquire("a") for _ in range(3)]
    clock.advance(61)

    after = limiter.acquire("a")

    assert [(s.limit, s.remaining) for s in first] == [(3, 2), (3, 1), (3, 0)]
    assert (after.limit, after.remaining) == (5, 1)  # hour: 5 - 4; minute would be 2


def test_acquire_rejected_has_zero_remaining_and_is_not_counted() -> None:
    clock = Clock()
    limiter = make([(2, 60), (100, 3600)], clock)
    hit_n(limiter, "a", 2)

    rejected = [limiter.acquire("a") for _ in range(4)]

    assert all(s.wait is not None for s in rejected)
    assert all((s.limit, s.remaining) == (2, 0) for s in rejected)
    clock.advance(60 + EPS)
    assert limiter.acquire("a").remaining == 1  # rejected calls were not counted


def test_acquire_remaining_is_never_negative() -> None:
    limiter = make([(1, 60), (2, 3600)])

    remaining = [limiter.acquire("a").remaining for _ in range(10)]

    assert remaining == [0] * 10


def test_acquire_reset_matches_wait_rounded_up_when_rejected() -> None:
    clock = Clock()
    limiter = make([(2, 60)], clock)
    limiter.hit("a")
    clock.advance(20)
    limiter.hit("a")
    clock.advance(10.5)

    state = limiter.acquire("a")

    assert state.wait is not None
    assert state.reset == 30  # ceil(29.5)
    assert state.reset >= state.wait


def test_acquire_reset_is_integer_and_not_negative() -> None:
    clock = Clock()
    limiter = make([(5, 60), (50, 3600)], clock)
    for _ in range(20):
        clock.advance(7.3)
        state = limiter.acquire("a")
        assert isinstance(state.reset, int)
        assert state.reset >= 0


def test_acquire_keys_are_independent() -> None:
    limiter = make([(3, 60)])
    limiter.acquire("a")
    limiter.acquire("a")

    assert limiter.acquire("b").remaining == 2


def test_acquire_hour_bucket_merges_hits_within_ten_seconds() -> None:
    clock = Clock()
    limiter = make([(3, 3600)], clock)
    limiter.hit("a")  # t=0: opens a bucket
    clock.advance(9)
    limiter.hit("a")  # t=9: same bucket, leaves the window at t=3600
    clock.advance(1)
    limiter.hit("a")  # t=10: a new bucket, leaves at t=3610
    clock.advance(3600 - 10)  # t=3600

    state = limiter.acquire("a")

    # Both hits of the first bucket are gone; only the t=10 hit and this one remain.
    assert state.wait is None
    assert (state.limit, state.remaining) == (3, 1)
    assert state.reset == 10


def test_acquire_hour_bucket_boundary_keeps_next_bucket_until_its_time() -> None:
    clock = Clock()
    limiter = make([(2, 3600)], clock)
    limiter.hit("a")  # t=0
    clock.advance(10)
    limiter.hit("a")  # t=10, own bucket
    clock.advance(3600 - 10 - 1)  # t=3599: nothing has left yet

    blocked = limiter.acquire("a")
    clock.advance(1)  # t=3600: the first bucket leaves
    freed = limiter.acquire("a")

    assert blocked.wait is not None
    assert blocked.reset == 1
    assert freed.wait is None
    assert freed.remaining == 0


def test_acquire_tie_with_remaining_left_picks_window_freeing_later() -> None:
    limiter = make([(3, 60), (3, 3600)])

    state = limiter.acquire("a")

    assert (state.limit, state.remaining) == (3, 2)
    assert state.reset == 3600


def test_acquire_tie_picks_larger_reset_whatever_the_window_order() -> None:
    limiter = make([(3, 3600), (3, 60)])

    state = limiter.acquire("a")

    assert (state.limit, state.remaining) == (3, 2)
    assert state.reset == 3600


def test_acquire_tie_with_different_limits_reports_hour_window() -> None:
    clock = Clock()
    limiter = make([(3, 60), (4, 3600)], clock)
    limiter.acquire("a")  # t=0
    clock.advance(70)

    state = limiter.acquire("a")  # minute: 3-1=2 left, hour: 4-2=2 left

    assert (state.limit, state.remaining) == (4, 2)
    assert state.reset == 3530


def test_acquire_rejected_with_hour_window_full_shows_hour_and_wait_matches_reset() -> None:
    clock = Clock()
    limiter = make([(3, 60), (4, 3600)], clock)
    hit_n(limiter, "a", 3)  # t=0: minute window full
    clock.advance(70)
    assert limiter.acquire("a").wait is None  # hour window now full

    state = limiter.acquire("a")

    assert state.wait is not None
    assert (state.limit, state.remaining) == (4, 0)
    assert state.reset == 3530
    assert state.reset == pytest.approx(state.wait, abs=1)


def test_acquire_both_windows_full_reset_is_the_larger_wait() -> None:
    limiter = make([(2, 60), (2, 3600)])
    hit_n(limiter, "a", 2)

    state = limiter.acquire("a")

    assert state.wait is not None
    assert (state.limit, state.remaining) == (2, 0)
    assert state.reset == 3600
    assert state.reset == pytest.approx(state.wait, abs=1)
