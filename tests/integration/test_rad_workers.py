"""Parallel RAD loading: `RadLoader.run(..., workers=N)` and `rad sync --workers N`.

Contract (from the task description, not from the implementation):

- `workers` is 1..8 on the CLI; `0`, `9`, `-1`, `abc` -> exit code 2 and no request at all.
- Default is 1 (the old behaviour: one sol at a time).
- With N > 1 at most N sols are in work at the same time; every sol is its own transaction; the
  final database state equals the N = 1 state; `sols_failed` is sorted.
- A failing sol (N > 1) lands in `sols_failed`, the others are loaded, exit code 1.
- `_ServerDown` (exit 3) with N > 1: the run stops, no new sols are taken, finished sols stay,
  unfinished ones are rolled back, the next run finishes the job.
- The advisory lock is one per run: a second parallel run gets exit code 2 (`locked`).

Real overlap is measured by a respx handler that holds file requests until N of them are in the
handler at the same moment (`ConcurrencyGate`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text

from app.db.session import create_engine
from app.services import rad_sync
from app.services.rad_sync import RadLoader, build_rad_client
from tests._rad import (
    CHECKSUMS,
    F_4842,
    F_4843,
    F_SOL10,
    LOCK_KEY,
    PREFIX,
    REV,
    TIMING,
    Factory,
    RadHost,
    fixture,
    good_file,
    rad_daily,
    rad_files,
    rad_hourly,
    rel_path,
    reset_hourly,
    run_rad,
    set_revision,
    synthetic_name,
    synthetic_path,
    wipe_rad,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

SOLS = list(range(100, 108))  # eight small synthetic sols
P = {sol: synthetic_path(synthetic_name(sol)) for sol in SOLS}
P4842 = rel_path(F_4842)
P4843 = rel_path(F_4843)
P10 = rel_path(F_SOL10)


# --- helpers -------------------------------------------------------------------------------------


def synthetic_files(sols: list[int] = SOLS) -> dict[str, bytes]:
    return {P[sol]: good_file(sol, dose_b=6.0 + sol / 1000) for sol in sols}


def make_host(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    *,
    synthetic: list[int] = SOLS,
    fixtures: tuple[str, ...] = (),
    mount: bool = True,
) -> RadHost:
    """A host with synthetic sols and, optionally, trimmed real fixture files."""
    host = RadHost(events, timing=fixture("TIMING_ERRORS.TXT"))
    host.files.update(synthetic_files(synthetic))
    for name in fixtures:
        host.files[rel_path(name)] = fixture(name)
    return host.mount(router) if mount else host


@dataclass
class ConcurrencyGate:
    """respx handler around a `RadHost`: file requests wait until `target` of them are inside.

    The gate opens once (then stays open), so the tail of a run is not delayed. `max_active` is the
    largest number of file requests inside the handler at one moment. The wait is bounded
    (`wait_s`, real time): a loader that never reaches `target` fails an assertion, not hangs.
    """

    host: RadHost
    target: int
    hold_yields: int = 20
    wait_s: float = 2.0
    active: int = 0
    max_active: int = 0
    reached: asyncio.Event = field(default_factory=asyncio.Event)

    def mount(self, router: respx.MockRouter) -> ConcurrencyGate:
        router.route(host=_host_name()).mock(side_effect=self)
        return self

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        if rel in (CHECKSUMS, TIMING):
            return self.host.handle(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.active >= self.target:
                self.reached.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.reached.wait(), self.wait_s)
            response = self.host.handle(request)
            for _ in range(self.hold_yields):
                await asyncio.sleep(0)
            return response
        finally:
            self.active -= 1


def _host_name() -> str:
    from tests._rad import HOST

    return HOST


@pytest.fixture
def router(upstream_router: respx.MockRouter) -> respx.MockRouter:
    return upstream_router


@pytest.fixture
async def loader(settings: Any) -> AsyncIterator[RadLoader]:
    engine = create_engine(settings.database_url)
    async with build_rad_client(settings) as client:
        yield RadLoader(engine, client, settings)
    await engine.dispose()


def _stable(row: dict[str, Any]) -> dict[str, Any]:
    """A row without surrogate ids and timestamps (they differ between two runs)."""
    return {k: v for k, v in row.items() if k != "id" and not k.endswith("_at")}


async def snapshot(factory: Factory) -> dict[str, list[dict[str, Any]]]:
    return {
        "daily": [_stable(r) for r in await rad_daily(factory)],
        "hourly": [_stable(r) for r in await rad_hourly(factory)],
        "files": [_stable(r) for r in await rad_files(factory)],
    }


async def age_hours(factory: Factory) -> None:
    """Make every sol look like it was computed by revision 1: no hours, old revision."""
    await set_revision(factory, 1, None)
    async with factory() as session:
        await session.execute(text("DELETE FROM rad_hourly"))
        await session.commit()


# --- real concurrency ----------------------------------------------------------------------------


async def test_run_with_three_workers_overlaps_file_requests_at_most_three_ways(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    gate = ConcurrencyGate(host, target=3)
    router.routes.clear()
    gate.mount(router)

    result = await loader.run(workers=3)

    assert result.exit_code == 0
    assert 2 <= gate.max_active <= 3
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS
    assert len(host.calls("files")) == len(SOLS)


async def test_run_with_one_worker_never_overlaps_file_requests(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    gate = ConcurrencyGate(host, target=1)
    router.routes.clear()
    gate.mount(router)

    result = await loader.run(workers=1)

    assert result.exit_code == 0
    assert gate.max_active == 1
    assert host.max_inflight == 1
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS


async def test_run_without_workers_argument_defaults_to_one_at_a_time(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    # target 2 would open the gate if two requests were ever inside; a short wait keeps it fast
    gate = ConcurrencyGate(host, target=2, wait_s=0.1)
    router.routes.clear()
    gate.mount(router)

    result = await loader.run()

    assert result.exit_code == 0
    assert gate.max_active == 1
    assert host.max_inflight == 1


async def test_run_with_workers_above_the_sol_count_uses_only_as_many_as_needed(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events, synthetic=[100, 101])
    gate = ConcurrencyGate(host, target=2)
    router.routes.clear()
    gate.mount(router)

    result = await loader.run(workers=8)

    assert result.exit_code == 0
    assert gate.max_active == 2
    assert [r["sol"] for r in await rad_daily(clean_db)] == [100, 101]


# --- the same result as with one worker ----------------------------------------------------------


@pytest.mark.parametrize("workers", [2, 4, 8], ids=["w2", "w4", "w8"])
async def test_sync_result_with_many_workers_equals_the_sequential_result(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    loader: RadLoader,
    clean_db: Factory,
    workers: int,
) -> None:
    make_host(router, events, fixtures=(F_SOL10, F_4842, F_4843))
    first = await loader.run(workers=1)
    assert first.exit_code == 0
    sequential = await snapshot(clean_db)
    assert len(sequential["daily"]) == len(SOLS) + 3
    assert sequential["hourly"], "the fixtures must produce hours"
    await wipe_rad(clean_db)

    second = await loader.run(workers=workers)

    assert second.exit_code == 0
    assert (second.sols_planned, second.sols_loaded) == (first.sols_planned, first.sols_loaded)
    assert (second.files_loaded, second.files_failed) == (first.files_loaded, first.files_failed)
    assert await snapshot(clean_db) == sequential


async def test_reprocess_result_with_four_workers_equals_the_sequential_result(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    """Revision 1 -> current revision: the same rows, hours and revisions for N=1 and N=4."""
    host = make_host(router, events, fixtures=(F_SOL10, F_4842, F_4843))
    assert (await loader.run(workers=1)).exit_code == 0
    loaded = await snapshot(clean_db)

    await age_hours(clean_db)
    host.requests.clear()
    one = await loader.run(reprocess=True, workers=1)
    assert one.exit_code == 0
    sequential = await snapshot(clean_db)
    requested_one = sorted(r.path for r in host.calls("files"))

    await age_hours(clean_db)
    host.requests.clear()
    four = await loader.run(reprocess=True, workers=4)

    assert four.exit_code == 0
    assert sorted(r.path for r in host.calls("files")) == requested_one
    assert len(requested_one) == len(SOLS) + 3
    assert await snapshot(clean_db) == sequential
    assert sequential == loaded  # a reprocess of the same files restores the loaded state
    assert {r["hourly_revision"] for r in sequential["daily"]} == {REV}


async def test_reprocess_with_four_workers_overlaps_and_reads_each_file_once(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    assert (await loader.run(workers=1)).exit_code == 0
    await reset_hourly(clean_db)
    host.requests.clear()
    router.routes.clear()
    gate = ConcurrencyGate(host, target=4).mount(router)

    result = await loader.run(reprocess=True, workers=4)

    assert result.exit_code == 0
    assert 2 <= gate.max_active <= 4
    assert sorted(r.path for r in host.calls("files")) == sorted(P.values())
    assert host.calls("probes") == []


async def test_sync_with_many_workers_is_idempotent(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    assert (await loader.run(workers=4)).exit_code == 0
    first = await snapshot(clean_db)
    host.requests.clear()

    again = await loader.run(workers=4)

    assert again.exit_code == 0
    assert host.calls("files") == []
    assert await snapshot(clean_db) == first


# --- failures ------------------------------------------------------------------------------------


def _break_combine(monkeypatch: pytest.MonkeyPatch, bad: set[int]) -> None:
    real = rad_sync.combine_hours

    def broken(used: Any, file_sol: int, *args: Any, **kwargs: Any) -> Any:
        if file_sol in bad:
            raise OverflowError("date value out of range")
        return real(used, file_sol, *args, **kwargs)

    monkeypatch.setattr(rad_sync, "combine_hours", broken)


async def test_failing_sols_with_four_workers_are_listed_sorted_and_the_rest_is_loaded(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    loader: RadLoader,
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_host(router, events)
    _break_combine(monkeypatch, {107, 103, 101})

    result = await loader.run(workers=4)

    assert result.exit_code == 1
    assert result.sols_failed == [101, 103, 107]
    assert [r["sol"] for r in await rad_daily(clean_db)] == [100, 102, 104, 105, 106]
    statuses = {f["path"]: f["status"] for f in await rad_files(clean_db)}
    assert {sol: statuses[P[sol]] for sol in SOLS} == {
        100: "loaded",
        101: "failed",
        102: "loaded",
        103: "failed",
        104: "loaded",
        105: "loaded",
        106: "loaded",
        107: "failed",
    }
    assert {h["sol"] for h in await rad_hourly(clean_db)} <= {100, 102, 104, 105, 106}


async def test_failing_sol_with_workers_via_cli_exits_1_and_loads_the_others(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_host(router, events)
    _break_combine(monkeypatch, {103})

    code = await run_rad("sync", "--workers", "4")

    assert code == 1
    assert [r["sol"] for r in await rad_daily(clean_db)] == [s for s in SOLS if s != 103]


class ServerDownGate:
    """Sol 100 answers 503 for good; every other file request waits until the loader cancels it
    (the 10 s timeout is only a safety net against a hang). `waiting` counts the held requests."""

    def __init__(self, host: RadHost, failing: str) -> None:
        self.host = host
        self.failing = failing
        self.waiting = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        if rel in (CHECKSUMS, TIMING) or rel == self.failing:
            return self.host.handle(request)
        self.waiting += 1
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.Event().wait(), 10.0)
        finally:
            self.waiting -= 1
        return self.host.handle(request)


async def test_server_down_with_three_workers_stops_the_run_and_the_next_run_finishes_it(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    host.fail[P[100]] = ["503"] * 40
    gate = ServerDownGate(host, P[100])
    router.routes.clear()
    router.route(host=_host_name()).mock(side_effect=gate)

    result = await loader.run(workers=3)

    assert result.exit_code == 3
    assert result.stopped == "server_down"
    assert gate.waiting == 0  # the loader cancelled and awaited the held workers
    # No new sol was taken after the server gave up: 103..107 were never requested.
    for sol in range(103, 108):
        assert host.file_calls(P[sol]) == [], sol
    # The sol that hit the failure left nothing behind; what is stored is consistent.
    stored = [r["sol"] for r in await rad_daily(clean_db)]
    assert 100 not in stored
    loaded = {f["path"] for f in await rad_files(clean_db) if f["status"] == "loaded"}
    assert loaded == {P[sol] for sol in stored}
    assert {h["sol"] for h in await rad_hourly(clean_db)} <= set(stored)
    assert P[100] not in loaded

    # The server is back: the next run loads exactly what is missing.
    host.fail.clear()
    host.requests.clear()
    router.routes.clear()
    host.mount(router)

    again = await loader.run(workers=3)

    assert again.exit_code == 0
    assert sorted(r.path for r in host.calls("files")) == sorted(
        P[sol] for sol in SOLS if sol not in stored
    )
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS


async def test_server_down_result_equals_the_sequential_one_after_the_rerun(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    assert (await loader.run(workers=1)).exit_code == 0
    expected = await snapshot(clean_db)
    await wipe_rad(clean_db)
    host.fail[P[104]] = ["503"] * 40

    interrupted = await loader.run(workers=3)
    assert interrupted.exit_code == 3
    host.fail.clear()
    again = await loader.run(workers=3)

    assert again.exit_code == 0
    assert await snapshot(clean_db) == expected


# --- the lock ------------------------------------------------------------------------------------


async def test_second_run_during_a_parallel_run_gets_the_lock_answer(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    host = make_host(router, events)
    proceed = asyncio.Event()
    entered: list[str] = []
    first_entered = asyncio.Event()

    async def hold(request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        if rel not in (CHECKSUMS, TIMING):
            entered.append(rel)
            first_entered.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proceed.wait(), 10.0)
        return host.handle(request)

    router.routes.clear()
    router.route(host=_host_name()).mock(side_effect=hold)
    first = asyncio.create_task(loader.run(workers=3))
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(first_entered.wait(), 10.0)
    assert entered, "the first run never requested a file"
    host.requests.clear()

    second = await loader.run(workers=3)
    async with clean_db() as session:
        free = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        if free:
            await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    proceed.set()
    result = await first

    assert second.locked is True
    assert second.exit_code == 2
    assert free is False  # one lock for the whole run, held while the workers are busy
    assert result.exit_code == 0
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS


async def test_cli_with_workers_while_another_run_holds_the_lock_exits_2_without_requests(
    router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host = make_host(router, events)
    session = clean_db()
    await session.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
    try:
        code = await run_rad("sync", "--workers", "3")
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        await session.rollback()
        await session.close()

    assert code == 2
    assert host.calls() == []
    assert await rad_daily(clean_db) == []


async def test_lock_is_free_after_a_parallel_run(
    router: respx.MockRouter, events: list[tuple[str, Any]], loader: RadLoader, clean_db: Factory
) -> None:
    make_host(router, events)

    assert (await loader.run(workers=4)).exit_code == 0

    async with clean_db() as session:
        free = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        assert free is True
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


# --- the CLI option ------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "9", "-1", "abc", "1.5", ""], ids=lambda v: v or "empty")
async def test_cli_invalid_workers_exits_2_without_any_request(
    router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory, value: str
) -> None:
    host = make_host(router, events)

    code = await run_rad("sync", f"--workers={value}")

    assert code == 2
    assert host.calls() == []
    assert await rad_daily(clean_db) == []


@pytest.mark.parametrize("value", ["1", "2", "8"])
async def test_cli_valid_workers_loads_everything(
    router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory, value: str
) -> None:
    make_host(router, events)

    code = await run_rad("sync", "--workers", value)

    assert code == 0
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS


async def test_cli_workers_is_passed_to_the_loader(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_host(router, events)
    seen: list[dict[str, Any]] = []
    real = RadLoader.run

    async def spy(self: RadLoader, *args: Any, **kwargs: Any) -> Any:
        seen.append({"args": args, "kwargs": kwargs})
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(RadLoader, "run", spy)

    assert await run_rad("sync", "--workers", "5", "--from-sol", "100", "--to-sol", "103") == 0

    assert len(seen) == 1
    workers = seen[0]["kwargs"].get("workers")
    if workers is None:  # positional call: from_sol, to_sol, reprocess, workers
        workers = seen[0]["args"][3]
    assert workers == 5
    assert [r["sol"] for r in await rad_daily(clean_db)] == [100, 101, 102, 103]


async def test_cli_without_workers_runs_one_sol_at_a_time(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = make_host(router, events)
    seen: list[Any] = []
    real = RadLoader.run

    async def spy(self: RadLoader, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("workers", args[3] if len(args) > 3 else 1))
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(RadLoader, "run", spy)

    assert await run_rad("sync") == 0

    assert seen == [1]
    assert host.max_inflight == 1


# --- Ctrl+C with several workers -----------------------------------------------------------------


async def test_ctrl_c_with_three_workers_rolls_back_the_interrupted_sol_and_the_rerun_finishes(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """Ctrl+C while the hours of sol 104 are written; sols 105+ are held in the host."""
    from app.repositories import rad as rad_repo

    host = make_host(router, events)
    held = {P[sol] for sol in (105, 106, 107)}

    async def gate(request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        if rel in held:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.Event().wait(), 10.0)
        return host.handle(request)

    router.routes.clear()
    router.route(host=_host_name()).mock(side_effect=gate)
    real = rad_repo.replace_hourly

    async def interrupt_on_104(session: Any, sol: int, *args: Any, **kwargs: Any) -> Any:
        if sol == 104:
            raise KeyboardInterrupt
        return await real(session, sol, *args, **kwargs)

    monkeypatch.setattr(rad_repo, "replace_hourly", interrupt_on_104)

    try:
        code = await run_rad("sync", "--workers", "3", crash_ok=True)
    except KeyboardInterrupt:
        code = None

    assert code != 0
    stored = [r["sol"] for r in await rad_daily(clean_db)]
    assert stored and set(stored) <= {100, 101, 102, 103}
    loaded = {f["path"] for f in await rad_files(clean_db) if f["status"] == "loaded"}
    assert loaded == {P[sol] for sol in stored}
    assert {h["sol"] for h in await rad_hourly(clean_db)} <= set(stored)
    async with clean_db() as session:
        free = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        assert free is True
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})

    monkeypatch.setattr(rad_repo, "replace_hourly", real)
    router.routes.clear()
    host.mount(router)
    host.requests.clear()

    assert await run_rad("sync", "--workers", "3") == 0

    assert sorted(r.path for r in host.calls("files")) == sorted(
        P[sol] for sol in SOLS if sol not in stored
    )
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS
    assert not [w for w in recwarn if "destroyed" in str(w.message).lower()]


# --- one shared cool-down ------------------------------------------------------------------------


async def test_cooldown_of_one_worker_holds_back_new_file_requests_of_the_others(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    loader: RadLoader,
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sol 100 fails three times and enters the cool-down (900 s, a controllable fake sleep);
    sols 101 and 102 are in flight. While it lasts, no request for sol 103+ goes out."""
    host = make_host(router, events)
    host.fail[P[100]] = ["503"] * 3
    cooling = asyncio.Event()
    end_cooling = asyncio.Event()
    entered: list[str] = []
    prev_sleep = asyncio.sleep

    async def cooldown_sleep(seconds: float, result: Any = None) -> Any:
        if seconds == 900:
            cooling.set()
            await end_cooling.wait()
            return result
        return await prev_sleep(seconds, result)

    async def gate(request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        if rel not in (CHECKSUMS, TIMING):
            entered.append(rel)
            if rel in (P[101], P[102]):
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(cooling.wait(), 10.0)
        return host.handle(request)

    async def real_pause(seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), seconds)

    router.routes.clear()
    router.route(host=_host_name()).mock(side_effect=gate)
    monkeypatch.setattr(asyncio, "sleep", cooldown_sleep)

    run = asyncio.create_task(loader.run(workers=3))
    await asyncio.wait_for(cooling.wait(), 10.0)
    await real_pause(0.5)  # 101 and 102 are released now, finish and want the next sols

    during = set(entered)
    end_cooling.set()
    result = await run

    assert during == {P[100], P[101], P[102]}
    assert result.exit_code == 0
    assert len(host.file_calls(P[100])) == 4
    assert [r["sol"] for r in await rad_daily(clean_db)] == SOLS
