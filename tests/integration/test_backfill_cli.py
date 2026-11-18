"""CLI ``python -m app.cli backfill`` (stage 7): batches, resume, retry, exit codes.

Contract (stage-7 brief):
``backfill --rover X [--from-sol A] [--to-sol B] [--batch-sols N] [--no-resume]``
- to_sol defaults to the source's latest sol, from_sol to 0, batch to BACKFILL_BATCH_SOLS
  (100); batches walk to_sol -> from_sol, each batch is one sync_run with mode ``backfill``;
- resume (default) skips a batch whose exact range already has a ``success`` backfill run;
  ``--no-resume`` redoes it;
- after the batches, the failed sols of this invocation are retried once;
- exit 0 all loaded, 1 failed sols remain, 2 the rover's advisory lock is held, 130 on
  Ctrl+C (the run ends ``failed``, a rerun resumes); progress is logged with an ETA.

The CLI is called in-process (``cli.main``) against the test database; the feeds are mocked
with respx on records trimmed from tests/fixtures/upstream/. ``cli.main`` runs its own event
loop, so async tests call it through ``asyncio.to_thread``; the Ctrl+C test is synchronous
because only the main thread receives SIGINT.
"""

import asyncio
import copy
import signal
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from app.core.config import Settings, get_settings
from tests.integration._fakes import fetch_all, photo_sols
from tests.integration.test_sync_service import HeldLock
from tests.unit._upstream import M2020_URL, MSL_URL, load_fixture, query

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
LATEST = 24
# Curiosity sols with photos (2 each); every other sol of 0..LATEST is empty upstream.
DATA_SOLS = (0, 3, 6, 7, 9, 12, 17, 21, 24)
# Fixed grid [k*N, k*N+N-1] clipped to the range, walked top down.
BATCHES_0_24_BY_10 = [(20, 24), (10, 19), (0, 9)]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    """The CLI reads Settings from the environment: test DB, no pauses, no HTTP retries."""
    env = {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "SYNC_CHUNK_SOLS": "10",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "UPSTREAM_RETRY_BASE_SECONDS": "0",
        "M2020_FEED_URL": M2020_URL,
        "MSL_FEED_URL": MSL_URL,
        "LOG_LEVEL": "INFO",
        # A held lock is not waited for unless a test says so.
        "BACKFILL_LOCK_RETRY_SECONDS": "0.05",
        "BACKFILL_LOCK_MAX_WAIT_SECONDS": "0",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BACKFILL_BATCH_SOLS", raising=False)
    monkeypatch.delenv("BACKFILL_MAX_FAILED_BATCHES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- MSL feed double ---------------------------------------------------------------------


def msl_items(sol: int) -> list[dict[str, Any]]:
    """Two real non-thumbnail items of sol 4327 re-labelled to ``sol`` with unique ids."""
    base = [i for i in load_fixture("msl/sol_4327_page0.json")["items"] if not i["is_thumbnail"]]
    items = []
    for n, raw in enumerate(base[:2]):
        item = copy.deepcopy(raw)
        item["sol"] = sol
        item["id"] = 7_000_000 + sol * 10 + n
        items.append(item)
    return items


class MslFeed:
    """respx side effect for the MSL feed.

    - latest-sol request (no ``condition_2``): ``latest`` as items[0].sol;
    - per-sol request: items of DATA_SOLS, empty otherwise;
    - ``fail``: sol -> number of leading 500 answers (``-1``: always 500);
    - ``on_sol``: optional hook called with the requested sol before answering.
    """

    def __init__(self, latest: int = LATEST, fail: dict[int, int] | None = None) -> None:
        self.latest = latest
        self.fail = dict(fail or {})
        self.sol_requests: list[int] = []
        self.latest_requests = 0
        self.on_sol: Callable[[int], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = query(request)
        if "condition_2" not in params:
            self.latest_requests += 1
            body = load_fixture("msl/latest.json")
            body["items"][0]["sol"] = self.latest
            return httpx.Response(200, json=body)
        sol = int(params["condition_2"].split(":")[0])
        self.sol_requests.append(sol)
        if self.on_sol is not None:
            self.on_sol(sol)
        remaining = self.fail.get(sol, 0)
        if remaining:
            if remaining > 0:
                self.fail[sol] = remaining - 1
            return httpx.Response(500, json={"error": "synthetic upstream failure"})
        items = msl_items(sol) if sol in DATA_SOLS else []
        return httpx.Response(
            200, json={"items": items, "more": False, "total": len(items), "page": 0}
        )

    @property
    def per_sol(self) -> Counter[int]:
        return Counter(self.sol_requests)


@pytest.fixture
def feed(upstream_router: respx.MockRouter) -> MslFeed:
    double = MslFeed()
    upstream_router.get(MSL_URL).mock(side_effect=double)
    return double


async def run_cli(argv: list[str]) -> int:
    """cli.main in a worker thread (it calls asyncio.run itself)."""
    try:
        return await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


async def backfill_runs(factory: SessionFactory) -> list[dict[str, Any]]:
    return await fetch_all(
        factory,
        "SELECT id, mode, from_sol, to_sol, status, failed_sols FROM sync_runs "
        "WHERE rover_id = 'curiosity' ORDER BY id",
    )


async def add_run(
    factory: SessionFactory,
    mode: str,
    from_sol: int,
    to_sol: int,
    status: str,
    failed: list[int] | None = None,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO sync_runs "
                "(rover_id, mode, from_sol, to_sol, status, finished_at, failed_sols) "
                "VALUES ('curiosity', :mode, :from_sol, :to_sol, :status, now(), :failed)"
            ),
            {
                "mode": mode,
                "from_sol": from_sol,
                "to_sol": to_sol,
                "status": status,
                "failed": failed or [],
            },
        )
        await session.commit()


def expected_photos(sols: range | list[int]) -> dict[int, int]:
    return {sol: 2 for sol in DATA_SOLS if sol in sols}


# --- batching ----------------------------------------------------------------------------


async def test_backfill_walks_batches_from_to_sol_down_one_backfill_run_each(
    feed: MslFeed, session_factory: SessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 0
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs] == BATCHES_0_24_BY_10
    assert {(r["mode"], r["status"]) for r in runs} == {("backfill", "success")}
    # Every sol fetched exactly once, newest first; no latest-sol request with --to-sol.
    assert feed.sol_requests == list(range(24, -1, -1))
    assert feed.latest_requests == 0
    assert await photo_sols(session_factory, "curiosity") == expected_photos(range(25))
    captured = capsys.readouterr()
    assert "eta" in (captured.out + captured.err).lower()


async def test_backfill_defaults_to_latest_sol_from_zero_and_batch_setting(
    feed: MslFeed, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BACKFILL_BATCH_SOLS", "10")
    get_settings.cache_clear()

    code = await run_cli(["backfill", "--rover", "curiosity"])

    assert code == 0
    assert feed.latest_requests >= 1
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs] == BATCHES_0_24_BY_10
    assert sorted(feed.per_sol.items()) == [(sol, 1) for sol in range(25)]


async def test_backfill_default_batch_is_100_sols(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    feed.latest = 150

    code = await run_cli(["backfill", "--rover", "curiosity", "--from-sol", "0"])

    assert code == 0
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs] == [(100, 150), (0, 99)]


async def test_backfill_partial_range_respects_from_sol(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "7", "--to-sol", "20",
         "--batch-sols", "5"]
    )  # fmt: skip

    assert code == 0
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs] == [(20, 20), (15, 19), (10, 14), (7, 9)]
    assert feed.sol_requests == list(range(20, 6, -1))
    assert await photo_sols(session_factory, "curiosity") == expected_photos(range(7, 21))


async def test_backfill_m2020_requests_follow_batches_without_duplicates(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    template = load_fixture("m2020/range_1000_page0.json")
    ranges: list[tuple[int, int, int]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        params = query(request)
        low = int(params["condition_2"].split(":")[0])
        high = int(params["condition_3"].split(":")[0])
        ranges.append((low, high, int(params["page"])))
        return httpx.Response(200, json={**template, "total_results": 0, "images": []})

    upstream_router.get(M2020_URL).mock(side_effect=respond)

    code = await run_cli(
        ["backfill", "--rover", "perseverance", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 0
    # Chunks (SYNC_CHUNK_SOLS=10) never cross a batch boundary; each range fetched once.
    assert ranges == [(20, 24, 0), (10, 19, 0), (0, 9, 0)]
    runs = await fetch_all(
        session_factory,
        "SELECT mode, from_sol, to_sol, status FROM sync_runs "
        "WHERE rover_id = 'perseverance' ORDER BY id",
    )
    assert [(r["mode"], r["from_sol"], r["to_sol"], r["status"]) for r in runs] == [
        ("backfill", low, high, "success") for low, high in BATCHES_0_24_BY_10
    ]


# --- resume ------------------------------------------------------------------------------


async def test_backfill_resume_skips_only_batches_fully_covered_by_backfill_runs(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    await add_run(session_factory, "backfill", 20, 24, "success")  # covered: skipped
    await add_run(session_factory, "incremental", 10, 19, "success")  # other mode: redone
    await add_run(session_factory, "backfill", 0, 3, "success")  # covers part only
    await add_run(session_factory, "backfill", 4, 9, "partial", [5])  # sol 5 failed: redone
    before = len(await backfill_runs(session_factory))

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 0
    assert feed.sol_requests == list(range(19, -1, -1))
    new_runs = (await backfill_runs(session_factory))[before:]
    assert [(r["mode"], r["from_sol"], r["to_sol"], r["status"]) for r in new_runs] == [
        ("backfill", 10, 19, "success"),
        ("backfill", 0, 9, "success"),
    ]


async def test_backfill_second_invocation_resumes_and_fetches_nothing(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    argv = ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
            "--batch-sols", "10"]  # fmt: skip
    assert await run_cli(argv) == 0
    feed.sol_requests.clear()

    assert await run_cli(argv) == 0

    assert feed.sol_requests == []
    assert len(await backfill_runs(session_factory)) == 3


async def test_backfill_no_resume_redoes_completed_batches(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    await add_run(session_factory, "backfill", 20, 24, "success")

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10", "--no-resume"]
    )  # fmt: skip

    assert code == 0
    assert feed.sol_requests == list(range(24, -1, -1))
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs[1:]] == BATCHES_0_24_BY_10


# --- failed sols: one retry ----------------------------------------------------------------


async def test_backfill_retries_failed_sols_once_and_exits_0_when_loaded(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    feed.fail = {7: 1}  # one 500, then OK (UPSTREAM_MAX_RETRIES=0: the sol fails)

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 0
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs[:3]] == BATCHES_0_24_BY_10
    assert runs[2]["status"] == "partial"
    assert 7 in runs[2]["failed_sols"]
    assert {r["mode"] for r in runs} == {"backfill"}
    retried = set(runs[2]["failed_sols"])
    # Sols after the failure in the chunk were never fetched by the batch: the retry fetches
    # every failed sol once more, nothing else is fetched twice.
    assert {sol for sol, n in feed.per_sol.items() if n == 2} <= retried
    assert set(feed.per_sol.values()) <= {1, 2}
    assert len(feed.per_sol) == 25
    assert feed.per_sol[7] == 2
    assert all(feed.per_sol[sol] == 1 for sol in set(range(25)) - retried)
    assert await photo_sols(session_factory, "curiosity") == expected_photos(range(25))


async def test_backfill_sol_still_failing_after_retry_exits_1(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    feed.fail = {7: -1}  # always 500

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 1
    assert feed.per_sol[7] == 2  # the batch + exactly one retry
    assert max(feed.per_sol.values()) == 2
    runs = await backfill_runs(session_factory)
    assert {r["mode"] for r in runs} == {"backfill"}
    assert 7 in runs[-1]["failed_sols"]
    assert 7 not in await photo_sols(session_factory, "curiosity")


# --- lock and Ctrl+C -------------------------------------------------------------------------


async def test_backfill_lock_held_exits_2_without_runs(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    async with HeldLock(session_factory, "curiosity"):
        code = await run_cli(
            ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24"]
        )

    assert code == 2
    assert feed.sol_requests == []
    assert await backfill_runs(session_factory) == []


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--from-sol", "10", "--to-sol", "5"], id="reversed-range"),
        pytest.param(["--from-sol", "-1", "--to-sol", "5"], id="negative-sol"),
        pytest.param(["--batch-sols", "0", "--to-sol", "5"], id="zero-batch"),
    ],
)
async def test_backfill_invalid_input_exits_2_without_runs(
    feed: MslFeed, session_factory: SessionFactory, argv: list[str]
) -> None:
    code = await run_cli(["backfill", "--rover", "curiosity", *argv])

    assert code == 2
    assert feed.sol_requests == []
    assert await backfill_runs(session_factory) == []


async def test_backfill_rover_without_source_exits_2(feed: MslFeed) -> None:
    assert await run_cli(["backfill", "--rover", "spirit", "--to-sol", "5"]) == 2


def _db_rows(url: str, sql: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return [dict(row) for row in (await conn.execute(text(sql))).mappings()]
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_backfill_ctrl_c_exits_130_run_failed_and_rerun_resumes(
    feed: MslFeed, settings: Settings
) -> None:
    argv = ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
            "--batch-sols", "10"]  # fmt: skip
    interrupted: list[int] = []

    def interrupt_once(sol: int) -> None:
        if sol == 10 and not interrupted:
            interrupted.append(sol)
            signal.raise_signal(signal.SIGINT)

    feed.on_sol = interrupt_once
    try:
        code = cli.main(argv)
    except KeyboardInterrupt:
        pytest.fail("backfill let KeyboardInterrupt escape instead of exiting with 130")

    assert code == 130
    sql = (
        "SELECT mode, from_sol, to_sol, status FROM sync_runs "
        "WHERE rover_id = 'curiosity' ORDER BY id"
    )
    runs = _db_rows(settings.database_url, sql)
    assert [(r["from_sol"], r["to_sol"], r["status"]) for r in runs] == [
        (20, 24, "success"),
        (10, 19, "failed"),
    ]

    feed.on_sol = None
    feed.sol_requests.clear()
    assert cli.main(argv) == 0

    assert feed.sol_requests == list(range(19, -1, -1))  # batch 20..24 not fetched again
    runs = _db_rows(settings.database_url, sql)
    assert [(r["from_sol"], r["to_sol"], r["status"]) for r in runs[2:]] == [
        (10, 19, "success"),
        (0, 9, "success"),
    ]


# --- review round: fixed grid, coverage resume, lock wait, circuit breaker ----------------


async def test_backfill_resume_after_latest_sol_moved_up_fetches_only_new_top(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    argv = ["backfill", "--rover", "curiosity", "--batch-sols", "10"]
    assert await run_cli(argv) == 0  # latest 24: batches 20..24, 10..19, 0..9
    feed.sol_requests.clear()
    feed.latest = 37

    assert await run_cli(argv) == 0

    # Grid batches 30..37 and 20..29 (only partly covered) are fetched, 0..19 are not.
    assert min(feed.sol_requests) >= 20
    assert set(range(25, 38)) <= set(feed.sol_requests)
    assert max(feed.per_sol.values()) == 1
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs[3:]] == [(30, 37), (20, 29)]


async def test_backfill_partial_batch_fixed_by_retry_is_not_refetched(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    feed.fail = {7: 1}
    argv = ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
            "--batch-sols", "10"]  # fmt: skip
    assert await run_cli(argv) == 0
    feed.sol_requests.clear()

    assert await run_cli(argv) == 0

    assert feed.sol_requests == []


@pytest.mark.parametrize(
    ("history", "refetched"),
    [
        pytest.param([("success", [])], False, id="success"),
        pytest.param([("partial", [])], False, id="partial-without-failed-sols"),
        pytest.param([("partial", [3])], True, id="partial-listing-a-sol"),
        pytest.param([("failed", [])], True, id="failed"),
        pytest.param([("success", []), ("failed", [])], True, id="newer-failed-wins"),
        pytest.param([("failed", []), ("success", [])], False, id="newer-success-wins"),
    ],
)
async def test_backfill_resume_uses_most_recent_run_covering_each_sol(
    feed: MslFeed,
    session_factory: SessionFactory,
    history: list[tuple[str, list[int]]],
    refetched: bool,
) -> None:
    for status, failed in history:
        await add_run(session_factory, "backfill", 0, 9, status, failed)

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "9",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 0
    assert feed.sol_requests == (list(range(9, -1, -1)) if refetched else [])


class BackgroundLock:
    """Takes the rover's advisory lock from another thread once ``start`` is set.

    The request queues behind the backfill's own lock (pg_advisory_lock blocks), so it is
    granted the moment the running batch releases it, i.e. between two batches.
    ``queued`` is set once the request waits in Postgres (or already got the lock).
    It is released after ``hold_seconds`` (None: when ``release`` is set).
    """

    NAMESPACE = 7_250_001

    def __init__(self, url: str, key: int, hold_seconds: float | None) -> None:
        self.url = url
        self.key = key
        self.hold_seconds = hold_seconds
        self.start = threading.Event()
        self.queued = threading.Event()
        self.acquired = threading.Event()
        self.release = threading.Event()
        self.thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)

    async def _main(self) -> None:
        await asyncio.to_thread(self.start.wait, 30)
        engine = create_async_engine(self.url, poolclass=NullPool)
        params = {"ns": self.NAMESPACE, "key": self.key}
        try:
            async with engine.connect() as conn, engine.connect() as probe:
                task = asyncio.create_task(
                    conn.execute(text("SELECT pg_advisory_lock(:ns, :key)"), params)
                )
                for _ in range(500):
                    if task.done():
                        break
                    waiting = await probe.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                            "AND NOT granted AND classid = :ns AND objid = :key"
                        ),
                        params,
                    )
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                self.queued.set()
                await task
                self.acquired.set()
                if self.hold_seconds is None:
                    await asyncio.to_thread(self.release.wait, 60)
                else:
                    await asyncio.sleep(self.hold_seconds)
                await conn.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
        finally:
            self.queued.set()
            await engine.dispose()


def take_lock_during_first_batch(
    feed: MslFeed, settings: Settings, hold_seconds: float | None
) -> BackgroundLock:
    lock = BackgroundLock(settings.database_url, 5, hold_seconds)  # curiosity legacy_id 5
    lock.thread.start()

    def hook(sol: int) -> None:
        if sol == 24 and not lock.start.is_set():
            lock.start.set()
            assert lock.queued.wait(10)

    feed.on_sol = hook
    return lock


async def test_backfill_lock_taken_between_batches_waits_and_continues_when_freed(
    feed: MslFeed,
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKFILL_LOCK_RETRY_SECONDS", "0.1")
    monkeypatch.setenv("BACKFILL_LOCK_MAX_WAIT_SECONDS", "20")
    get_settings.cache_clear()
    lock = take_lock_during_first_batch(feed, settings, hold_seconds=1.0)

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
         "--batch-sols", "10"]
    )  # fmt: skip
    lock.thread.join(10)

    assert lock.acquired.is_set()
    assert code == 0
    assert feed.sol_requests == list(range(24, -1, -1))
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"], r["status"]) for r in runs] == [
        (low, high, "success") for low, high in BATCHES_0_24_BY_10
    ]


async def test_backfill_lock_still_held_after_max_wait_exits_2_without_retry_phase(
    feed: MslFeed,
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKFILL_LOCK_RETRY_SECONDS", "0.05")
    monkeypatch.setenv("BACKFILL_LOCK_MAX_WAIT_SECONDS", "0.3")
    get_settings.cache_clear()
    feed.fail = {22: 1}  # batch 20..24 ends partial: a retry phase would refetch sol 22
    lock = take_lock_during_first_batch(feed, settings, hold_seconds=None)
    try:
        code = await run_cli(
            ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "24",
             "--batch-sols", "10"]
        )  # fmt: skip
    finally:
        lock.release.set()
        lock.thread.join(10)

    assert lock.acquired.is_set()
    assert code == 2
    assert feed.per_sol[22] == 1  # no retry phase
    assert min(feed.sol_requests) >= 20  # nothing after the first batch
    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"]) for r in runs] == [(20, 24)]


async def test_backfill_circuit_breaker_stops_after_3_failed_batches_and_rerun_resumes(
    feed: MslFeed, session_factory: SessionFactory
) -> None:
    feed.fail = dict.fromkeys(range(100), -1)  # the feed is down
    argv = ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "99",
            "--batch-sols", "10"]  # fmt: skip

    assert await run_cli(argv) == 1

    runs = await backfill_runs(session_factory)
    assert [(r["from_sol"], r["to_sol"], r["status"]) for r in runs] == [
        (90, 99, "failed"),
        (80, 89, "failed"),
        (70, 79, "failed"),
    ]
    # One chunk per batch fails on its first sol; no retry phase after the breaker.
    assert feed.sol_requests == [99, 89, 79]

    feed.fail = {}
    feed.sol_requests.clear()
    assert await run_cli(argv) == 0
    assert feed.sol_requests == list(range(99, -1, -1))


async def test_backfill_circuit_breaker_threshold_setting(
    feed: MslFeed, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BACKFILL_MAX_FAILED_BATCHES", "2")
    get_settings.cache_clear()
    feed.fail = dict.fromkeys(range(100), -1)

    code = await run_cli(
        ["backfill", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "99",
         "--batch-sols", "10"]
    )  # fmt: skip

    assert code == 1
    assert len(await backfill_runs(session_factory)) == 2
    assert feed.sol_requests == [99, 89]
