"""Cost of big v1 responses in the rate limit (docs/proposals/legacy-response-size.md).

Unit layer: AC-1 (`RateLimiter.charge`), AC-7 (a failing `send`, pure ASGI), AC-8 (settings),
`extra_hits` and the boundary cases of `ResponseSizeMiddleware` (body exactly U, U + 1, empty,
several body messages). The HTTP-level criteria live in tests/api/test_response_size_limit.py.

Time is a fake clock; nothing sleeps and nothing touches the network or the database.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from app.api.caching import RESPONSE_SIZE_SCOPE_KEY
from app.api.ratelimit import (
    RateLimitMiddleware,
    ResponseSizeMiddleware,
    client_key,
    extra_hits,
    response_cost,
)
from app.core.config import Settings
from app.core.ratelimit import RateLimiter

pytestmark = pytest.mark.unit

START = 50_000.0
UNIT = 1024
KEY = "198.51.100.7"
LIMIT = 100
# Raw unit R of the earlier tests: so large that the raw side never matters there (AC-9).
BIG_RAW_UNIT = 1_048_576
RAW_UNIT = 4 * UNIT  # R of the tests of docs/proposals/legacy-response-cpu.md


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- extra_hits ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body_bytes", "expected"),
    [
        pytest.param(0, 0, id="empty"),
        pytest.param(1, 0, id="one-byte"),
        pytest.param(UNIT - 1, 0, id="u-minus-1"),
        pytest.param(UNIT, 0, id="exactly-u"),
        pytest.param(UNIT + 1, 1, id="u-plus-1"),
        pytest.param(2 * UNIT, 1, id="exactly-2u"),
        pytest.param(2 * UNIT + 1, 2, id="2u-plus-1"),
        pytest.param(10 * UNIT, 9, id="10u"),
        pytest.param(10 * UNIT + 1, 10, id="10u-plus-1"),
    ],
)
def test_extra_hits_counts_started_units_minus_one(body_bytes: int, expected: int) -> None:
    assert extra_hits(body_bytes, UNIT) == expected


def test_extra_hits_worst_sol_without_gzip_costs_31_requests() -> None:
    """Spec section 3/4: 8 008 724 bytes at the default 256 KiB unit -> 31 requests (30 extra)."""
    assert extra_hits(8_008_724, 262_144) + 1 == 31


def test_extra_hits_gzipped_sol_costs_one_request() -> None:
    assert extra_hits(138_000, 262_144) == 0


# --- AC-1: RateLimiter.charge ----------------------------------------------------------------


def make_limiter(windows: list[tuple[int, float]] | None = None) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(windows or [(5, 60), (20, 3600)], clock=clock), clock


def test_ac1_charge_adds_hits_to_every_window() -> None:
    limiter, _ = make_limiter([(100, 60), (5, 3600)])

    limiter.charge(KEY, 3)

    state = limiter.peek(KEY)
    # The hour window (limit 5) is the tighter one: 5 - 3 = 2 left.
    assert (state.limit, state.remaining) == (5, 2)
    # The minute window counts them too: see test_ac1_charge_counts_in_the_minute_window_too.


def test_ac1_charge_counts_in_the_minute_window_too() -> None:
    limiter, clock = make_limiter([(5, 60), (100, 3600)])

    limiter.charge(KEY, 5)

    assert limiter.acquire(KEY).wait is not None  # minute window is full
    clock.advance(61)
    assert limiter.acquire(KEY).wait is None  # ... and free again after a minute


def test_ac1_overflow_gives_wait_until_the_charge_leaves_the_window() -> None:
    limiter, clock = make_limiter([(5, 60), (100, 3600)])

    limiter.charge(KEY, 7)  # more than the limit: allowed, nothing is checked
    state = limiter.acquire(KEY)

    assert state.wait == pytest.approx(60.0, abs=1.0)
    assert state.remaining == 0
    clock.advance(59)
    assert limiter.acquire(KEY).wait is not None
    clock.advance(2)
    assert limiter.acquire(KEY).wait is None


def test_ac1_wait_follows_the_oldest_charge_that_must_leave() -> None:
    limiter, clock = make_limiter([(5, 60), (100, 3600)])

    limiter.charge(KEY, 3)  # t0
    clock.advance(30)
    limiter.charge(KEY, 3)  # t0 + 30: total 6 > 5

    wait = limiter.acquire(KEY).wait

    # One more hit fits only after the t0 bucket leaves: 30 s from now.
    assert wait == pytest.approx(30.0, abs=1.5)


def test_ac1_charge_is_not_rejected_when_over_the_limit() -> None:
    limiter, _ = make_limiter([(2, 60)])

    limiter.charge(KEY, 2)
    limiter.charge(KEY, 5)  # the limiter never refuses a charge

    state = limiter.peek(KEY)
    assert state.remaining == 0


def test_ac1_charge_for_a_new_key_does_not_fail() -> None:
    limiter, _ = make_limiter()

    limiter.charge("never-seen", 2)

    assert len(limiter) == 1
    assert limiter.peek("never-seen").remaining == 3


@pytest.mark.parametrize("hits", [0, -1, -100], ids=["zero", "minus-one", "minus-hundred"])
def test_ac1_charge_of_zero_or_less_does_nothing(hits: int) -> None:
    limiter, _ = make_limiter()

    limiter.charge("fresh", hits)  # a new key is not even created
    assert len(limiter) == 0

    limiter.acquire(KEY)
    before = limiter.peek(KEY)
    limiter.charge(KEY, hits)
    assert limiter.peek(KEY) == before


def test_ac1_charge_is_per_key() -> None:
    limiter, _ = make_limiter([(5, 60)])

    limiter.charge("a", 5)

    assert limiter.acquire("a").wait is not None
    assert limiter.acquire("b").wait is None


# --- AC-8: settings --------------------------------------------------------------------------


def test_ac8_default_unit_is_256_kib() -> None:
    assert Settings(_env_file=None).rate_limit_v1_bytes_per_request == 262_144  # type: ignore[call-arg]


@pytest.mark.parametrize("value", [0, 1, 1023, -5], ids=["zero", "one", "1023", "negative"])
def test_ac8_unit_below_1024_is_a_settings_error(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_v1_bytes_per_request=value)  # type: ignore[call-arg]


def test_ac8_unit_of_exactly_1024_is_accepted() -> None:
    settings = Settings(_env_file=None, rate_limit_v1_bytes_per_request=1024)  # type: ignore[call-arg]
    assert settings.rate_limit_v1_bytes_per_request == 1024


def test_ac8_unit_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_V1_BYTES_PER_REQUEST", "4096")
    assert Settings(_env_file=None).rate_limit_v1_bytes_per_request == 4096  # type: ignore[call-arg]


def test_ac8_environment_value_below_1024_fails_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_V1_BYTES_PER_REQUEST", "1023")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


# --- ResponseSizeMiddleware over a fake app --------------------------------------------------


def make_scope(
    path: str,
    limiter: RateLimiter | None,
    unit: int = UNIT,
    client: str = KEY,
    scope_type: str = "http",
    raw_unit: int = BIG_RAW_UNIT,
) -> Scope:
    state = SimpleNamespace(
        v1_bytes_per_request=unit,
        v1_raw_bytes_per_request=raw_unit,
        settings=SimpleNamespace(
            rate_limit_v1_bytes_per_request=unit,
            rate_limit_v1_raw_bytes_per_request=raw_unit,
        ),
    )
    if limiter is not None:
        state.rate_limiter = limiter
    return {
        "type": scope_type,
        "method": "GET",
        "path": path,
        "headers": [],
        "client": (client, 40000),
        "app": SimpleNamespace(state=state),
    }


def fake_app(chunks: list[bytes], *, fail_with: BaseException | None = None) -> Any:
    """An ASGI app that answers 200 with ``chunks`` as body messages (``more_body`` set on all
    but the last); with ``chunks == []`` it sends no body message at all."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for index, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(chunks) - 1,
                }
            )
        if fail_with is not None:
            raise fail_with

    return app


async def no_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class Sink:
    """The client side: collects messages; may fail on the n-th body message."""

    def __init__(self, fail_on_body: int | None = None, error: BaseException | None = None) -> None:
        self.messages: list[Message] = []
        self.bodies = 0
        self._fail_on = fail_on_body
        self._error = error

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.body":
            self.bodies += 1
            if self._fail_on is not None and self.bodies == self._fail_on:
                assert self._error is not None
                raise self._error
        self.messages.append(message)

    @property
    def body_bytes(self) -> int:
        return sum(
            len(m.get("body", b"")) for m in self.messages if m["type"] == "http.response.body"
        )


async def run(app: Any, scope: Scope, sink: Sink | None = None) -> Sink:
    sink = sink or Sink()
    await ResponseSizeMiddleware(app)(scope, no_receive, sink)
    return sink


def spent(limiter: RateLimiter) -> int:
    """Hits counted for KEY in the tighter window (LIMIT - remaining)."""
    return LIMIT - limiter.peek(KEY).remaining


def new_limiter() -> RateLimiter:
    return RateLimiter([(LIMIT, 60), (LIMIT * 10, 3600)], clock=Clock(), max_concurrent=1)


BODY_CASES = [
    pytest.param([], 0, id="no-body-message"),
    pytest.param([b""], 0, id="empty-body"),
    pytest.param([b"x"], 0, id="one-byte"),
    pytest.param([b"x" * UNIT], 0, id="exactly-u"),
    pytest.param([b"x" * (UNIT + 1)], 1, id="u-plus-1"),
    pytest.param([b"x" * (2 * UNIT)], 1, id="exactly-2u"),
    pytest.param([b"x" * (2 * UNIT + 1)], 2, id="2u-plus-1"),
    pytest.param([b"x" * (UNIT // 2)] * 3, 1, id="more-body-3-halves-is-1.5u"),
    pytest.param([b"x" * UNIT, b""], 0, id="u-then-empty-final-chunk"),
    pytest.param([b"x" * UNIT, b"y"], 1, id="u-then-one-byte-chunk"),
    pytest.param([b"x" * UNIT] * 10, 9, id="more-body-10-units"),
    pytest.param([b"x", b"", b"x" * (3 * UNIT)], 3, id="mixed-chunks-3u-plus-1"),
]


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
@pytest.mark.parametrize(("chunks", "extra"), BODY_CASES)
async def test_response_size_charges_started_units_of_the_body(
    chunks: list[bytes], extra: int, prefix: str
) -> None:
    limiter = new_limiter()
    sink = await run(fake_app(chunks), make_scope(f"{prefix}/rovers/curiosity/photos", limiter))

    assert spent(limiter) == extra
    # The middleware is transparent: every message reaches the client untouched.
    assert sink.body_bytes == sum(len(c) for c in chunks)
    assert [m["type"] for m in sink.messages][0] == "http.response.start"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/rovers",
        "/api/v2/photos",
        "/health",
        "/",
        "/api/v10/rovers",
        "/api/v1x/rovers",
    ],
)
async def test_response_size_is_transparent_for_non_v1_paths(path: str) -> None:
    limiter = new_limiter()
    sink = await run(fake_app([b"x" * (10 * UNIT)]), make_scope(path, limiter))

    assert spent(limiter) == 0
    assert sink.body_bytes == 10 * UNIT


async def test_response_size_without_a_limiter_passes_through() -> None:
    sink = await run(fake_app([b"x" * (10 * UNIT)]), make_scope("/api/v1/rovers", None))

    assert sink.body_bytes == 10 * UNIT


async def test_response_size_ignores_non_http_scopes() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    limiter = new_limiter()
    await ResponseSizeMiddleware(app)(
        make_scope("/api/v1/rovers", limiter, scope_type="websocket"), no_receive, Sink()
    )

    assert seen == ["websocket"]
    assert spent(limiter) == 0


async def test_response_size_reads_the_unit_on_every_request() -> None:
    limiter = new_limiter()
    chunks = [b"x" * (4 * UNIT)]

    await run(fake_app(chunks), make_scope("/api/v1/rovers", limiter, unit=UNIT))
    assert spent(limiter) == 3
    await run(fake_app(chunks), make_scope("/api/v1/rovers", limiter, unit=2 * UNIT))
    assert spent(limiter) == 3 + 1


async def test_response_size_charges_the_client_key_of_an_ipv6_network() -> None:
    limiter = new_limiter()
    scope = make_scope("/api/v1/rovers", limiter, client="2001:db8:1:2::abcd")
    await run(fake_app([b"x" * (3 * UNIT)]), scope)

    other_address_same_64 = "2001:db8:1:2:ffff::1"
    shared = limiter.peek(client_key(make_scope("/", limiter, client=other_address_same_64)))

    # 3 U -> 2 extra hits, visible through another address of the same /64 ...
    assert shared.remaining == LIMIT - 2
    # ... and not through an address of another /64.
    assert (
        limiter.peek(client_key(make_scope("/", limiter, client="2001:db8:1:3::1"))).remaining
        == LIMIT
    )


# --- AC-7: the client drops the connection mid-body -------------------------------------------


def full_stack(app: Any) -> Any:
    """ResponseSize (outside) -> RateLimit -> app, as in app.main."""
    return ResponseSizeMiddleware(RateLimitMiddleware(app))


async def test_ac7_send_error_charges_the_failed_message_too_and_propagates_the_same_error() -> (
    None
):
    limiter = new_limiter()
    boom = ConnectionResetError("client went away")
    # 3 U go out; the second chunk (5 U) fails, but its bytes are counted before the send.
    app = fake_app([b"x" * (3 * UNIT), b"y" * (5 * UNIT)])
    sink = Sink(fail_on_body=2, error=boom)

    with pytest.raises(ConnectionResetError) as caught:
        await full_stack(app)(
            make_scope("/api/v1/rovers/curiosity/photos", limiter), no_receive, sink
        )

    assert caught.value is boom
    # 1 hit on entry + (ceil(8U / U) - 1) = 7 extra for 3 U + 5 U.
    assert spent(limiter) == 1 + 7


async def test_ac7_send_error_on_the_first_body_still_charges_that_body() -> None:
    limiter = new_limiter()
    boom = OSError("broken pipe")
    sink = Sink(fail_on_body=1, error=boom)

    with pytest.raises(OSError) as caught:
        await full_stack(fake_app([b"x" * (9 * UNIT)]))(
            make_scope("/api/v1/rovers", limiter), no_receive, sink
        )

    assert caught.value is boom
    # 1 hit on entry + (ceil(9U / U) - 1) = 8 extra: the failed message is charged.
    assert spent(limiter) == 1 + 8


async def test_ac7_send_error_releases_the_concurrency_slot() -> None:
    limiter = new_limiter()  # max_concurrent=1
    sink = Sink(fail_on_body=2, error=ConnectionResetError("gone"))

    with pytest.raises(ConnectionResetError):
        await full_stack(fake_app([b"x" * UNIT, b"y" * UNIT]))(
            make_scope("/api/v1/rovers", limiter), no_receive, sink
        )

    assert limiter.enter(KEY) is True  # the in-flight counter was released
    limiter.leave(KEY)


async def test_ac7_error_raised_by_the_app_after_a_partial_body_keeps_the_charge() -> None:
    limiter = new_limiter()
    boom = RuntimeError("route failed halfway")

    with pytest.raises(RuntimeError) as caught:
        await full_stack(fake_app([b"x" * (4 * UNIT)], fail_with=boom))(
            make_scope("/api/v1/rovers", limiter), no_receive, Sink()
        )

    assert caught.value is boom  # not replaced by an error of the middleware
    assert spent(limiter) == 1 + 3
    assert limiter.enter(KEY) is True
    limiter.leave(KEY)


async def test_ac7_charge_after_error_is_idempotent_per_request() -> None:
    """A failed request is charged exactly once (no double charge from `finally`)."""
    limiter = new_limiter()
    sink = Sink(fail_on_body=2, error=ConnectionResetError("gone"))

    with pytest.raises(ConnectionResetError):
        await full_stack(fake_app([b"x" * (2 * UNIT), b"y"]))(
            make_scope("/api/v1/rovers", limiter), no_receive, sink
        )

    # 2 U + 1 byte (the failed chunk) = 3 started units -> 1 entry + 2 extra, charged once.
    assert spent(limiter) == 1 + 2


# === docs/proposals/legacy-response-cpu.md: price by the raw (uncompressed) size =============


@pytest.mark.parametrize(
    ("wire", "raw", "unit", "raw_unit", "expected"),
    [
        pytest.param(0, None, 1024, 4096, 1, id="empty-no-raw"),
        pytest.param(0, 0, 1024, 4096, 1, id="empty-raw-zero"),
        pytest.param(1, 1, 1024, 4096, 1, id="one-byte"),
        pytest.param(1024, None, 1024, 4096, 1, id="wire-exactly-u"),
        pytest.param(1025, None, 1024, 4096, 2, id="wire-u-plus-1"),
        pytest.param(5000, None, 1024, 4096, 5, id="raw-unknown-wire-only"),
        pytest.param(100, 4096, 1024, 4096, 1, id="raw-exactly-r"),
        pytest.param(100, 4097, 1024, 4096, 2, id="raw-r-plus-1"),
        pytest.param(100, 40960, 1024, 4096, 10, id="raw-10r-small-wire"),
        pytest.param(100, 40961, 1024, 4096, 11, id="raw-10r-plus-1"),
        pytest.param(10 * 1024, 10 * 1024, 1024, 4096, 10, id="wire-dominates"),
        pytest.param(3 * 1024, 40960, 1024, 4096, 10, id="raw-dominates"),
        pytest.param(5000, 5000, 1024, 1024, 5, id="r-equals-u"),
        pytest.param(138_000, 8_008_724, 262_144, 1_048_576, 8, id="worst-sol-gzip-is-8"),
        pytest.param(8_008_724, 8_008_724, 262_144, 1_048_576, 31, id="worst-sol-plain-is-31"),
        pytest.param(23_000, 23_000, 262_144, 1_048_576, 1, id="ordinary-sol-is-1"),
    ],
)
def test_cpu_ac1_response_cost_is_the_max_of_wire_and_raw_units(
    wire: int, raw: int | None, unit: int, raw_unit: int, expected: int
) -> None:
    assert response_cost(wire, raw, unit, raw_unit) == expected


def test_cpu_ac1_scope_key_name_is_the_shared_one() -> None:
    assert RESPONSE_SIZE_SCOPE_KEY == "mars.response_size"


def test_cpu_ac8_default_raw_unit_is_1_mib() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.rate_limit_v1_raw_bytes_per_request == 1_048_576


@pytest.mark.parametrize("value", [0, 1, 1023, -5], ids=["zero", "one", "1023", "negative"])
def test_cpu_ac8_raw_unit_below_1024_is_a_settings_error(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_v1_raw_bytes_per_request=value)  # type: ignore[call-arg]


def test_cpu_ac8_raw_unit_below_the_wire_unit_is_a_settings_error() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            rate_limit_v1_bytes_per_request=8192,
            rate_limit_v1_raw_bytes_per_request=4096,
        )


@pytest.mark.parametrize(
    ("unit", "raw_unit"),
    [(1024, 1024), (1024, 4096), (262_144, 262_144)],
    ids=["both-1024", "r-above-u", "r-equals-default-u"],
)
def test_cpu_ac8_raw_unit_at_or_above_the_wire_unit_is_accepted(unit: int, raw_unit: int) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        rate_limit_v1_bytes_per_request=unit,
        rate_limit_v1_raw_bytes_per_request=raw_unit,
    )
    assert settings.rate_limit_v1_raw_bytes_per_request == raw_unit


def test_cpu_ac8_raw_unit_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_V1_RAW_BYTES_PER_REQUEST", "2097152")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.rate_limit_v1_raw_bytes_per_request == 2_097_152


def test_cpu_ac8_environment_raw_unit_below_1024_fails_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_V1_RAW_BYTES_PER_REQUEST", "1023")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def raw_reporting_app(
    chunks: list[bytes],
    raw: int | None,
    *,
    status: int = 200,
    fail_with: BaseException | None = None,
) -> Any:
    """Like the caching layer: writes raw into the scope dict (200 only) before the body."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        info = scope.get(RESPONSE_SIZE_SCOPE_KEY)
        if info is not None and raw is not None and status == 200:
            info["raw"] = raw
        await send({"type": "http.response.start", "status": status, "headers": []})
        for index, chunk in enumerate(chunks):
            await send(
                {"type": "http.response.body", "body": chunk, "more_body": index < len(chunks) - 1}
            )
        if fail_with is not None:
            raise fail_with

    return app


async def test_cpu_ac2_middleware_puts_a_mutable_dict_into_the_v1_scope() -> None:
    seen: list[Any] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope.get(RESPONSE_SIZE_SCOPE_KEY))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    await run(app, make_scope("/api/v1/rovers", new_limiter(), raw_unit=RAW_UNIT))

    assert isinstance(seen[0], dict)
    assert seen[0].get("raw") is None


@pytest.mark.parametrize("path", ["/api/v2/rovers", "/health", "/"])
async def test_cpu_ac6_non_v1_scope_gets_no_size_dict_and_no_charge(path: str) -> None:
    seen: list[bool] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(RESPONSE_SIZE_SCOPE_KEY in scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * (9 * UNIT), "more_body": False})

    limiter = new_limiter()
    await run(app, make_scope(path, limiter, raw_unit=RAW_UNIT))

    assert seen == [False]
    assert spent(limiter) == 0


RAW_CASES = [
    pytest.param(10, RAW_UNIT, 0, id="raw-exactly-r"),
    pytest.param(10, RAW_UNIT + 1, 1, id="raw-r-plus-1"),
    pytest.param(10, 10 * RAW_UNIT, 9, id="raw-10r"),
    pytest.param(10, 10 * RAW_UNIT + 1, 10, id="raw-10r-plus-1"),
    pytest.param(10, 0, 0, id="raw-zero"),
    pytest.param(10, None, 0, id="raw-unknown-wire-only"),
    pytest.param(3 * UNIT, 10 * RAW_UNIT, 9, id="raw-dominates-wire"),
    pytest.param(9 * UNIT, 2 * RAW_UNIT, 8, id="wire-dominates-raw"),
]


@pytest.mark.parametrize(("wire", "raw", "extra"), RAW_CASES)
async def test_cpu_ac2_response_is_charged_by_the_larger_of_wire_and_raw(
    wire: int, raw: int | None, extra: int
) -> None:
    limiter = new_limiter()
    scope = make_scope("/api/v1/rovers/curiosity/photos", limiter, raw_unit=RAW_UNIT)
    sink = await run(raw_reporting_app([b"z" * wire], raw), scope)

    assert spent(limiter) == extra
    assert sink.body_bytes == wire  # transparent


async def test_cpu_ac2_wire_is_summed_over_several_body_messages_raw_is_not_added() -> None:
    limiter = new_limiter()
    await run(
        raw_reporting_app([b"z" * 50] * 4, 3 * RAW_UNIT + 1),
        make_scope("/api/v1/rovers", limiter, raw_unit=RAW_UNIT),
    )

    assert spent(limiter) == 3  # ceil(raw / R) = 4 -> 3 extra; wire 200 B -> 0


@pytest.mark.parametrize("status", [404, 400, 503, 302])
async def test_cpu_ac7_no_raw_reported_means_wire_only_cost(status: int) -> None:
    """Without a raw size in the dict the cost follows the wire alone (the real non-200 path,
    where the caching layer reports nothing, is covered by the api AC-7 tests)."""
    limiter = new_limiter()
    await run(
        raw_reporting_app([b"z" * 10], 10 * RAW_UNIT, status=status),
        make_scope("/api/v1/rovers", limiter, raw_unit=RAW_UNIT),
    )

    assert spent(limiter) == 0


async def test_cpu_ac2_raw_charge_survives_a_failed_send() -> None:
    limiter = new_limiter()
    sink = Sink(fail_on_body=1, error=ConnectionResetError("gone"))

    with pytest.raises(ConnectionResetError):
        await full_stack(raw_reporting_app([b"z" * 10], 10 * RAW_UNIT))(
            make_scope("/api/v1/rovers", limiter, raw_unit=RAW_UNIT), no_receive, sink
        )

    assert spent(limiter) == 1 + 9  # entry + (10 - 1), charged once


async def test_cpu_ac2_raw_unit_is_read_on_every_request() -> None:
    limiter = new_limiter()
    app = raw_reporting_app([b"z"], 4 * RAW_UNIT)

    await run(app, make_scope("/api/v1/rovers", limiter, raw_unit=RAW_UNIT))
    assert spent(limiter) == 3
    await run(app, make_scope("/api/v1/rovers", limiter, raw_unit=2 * RAW_UNIT))
    assert spent(limiter) == 3 + 1


async def test_cpu_ac2_raw_unit_falls_back_to_the_settings_without_the_test_hook() -> None:
    limiter = new_limiter()
    scope = make_scope("/api/v1/rovers", limiter, raw_unit=RAW_UNIT)
    del scope["app"].state.v1_raw_bytes_per_request

    await run(raw_reporting_app([b"z"], 4 * RAW_UNIT), scope)

    assert spent(limiter) == 3


async def test_cpu_ac2_raw_unit_below_the_wire_unit_is_clamped_up_to_the_wire_unit() -> None:
    limiter = new_limiter()
    # raw_unit 512 < UNIT 1024 is read as 1024: 10 * UNIT raw -> 10 requests (9 extra), not 20.
    await run(
        raw_reporting_app([b"z"], 10 * UNIT),
        make_scope("/api/v1/rovers", limiter, raw_unit=512),
    )

    assert spent(limiter) == 9
