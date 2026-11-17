"""SyncScheduler (app/services/scheduler.py) and the sync part of the app lifespan.

The sync service is a fake; the real UpstreamClient is created but never sends a request
(the autouse respx guard would fail the test).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.main import create_app
from app.services import scheduler as scheduler_module
from app.services.scheduler import SyncScheduler
from app.services.sync import SyncResult
from app.sources.http import UpstreamClient
from app.sources.registry import SYNCABLE_ROVERS
from tests.unit._upstream import make_settings

pytestmark = pytest.mark.unit


def result_for(rover_id: str) -> SyncResult:
    return SyncResult(
        run_id=1,
        rover_id=rover_id,
        mode="incremental",
        from_sol=1,
        to_sol=2,
        status="success",
        photos_upserted=0,
        failed_sols=[],
        error=None,
    )


class FakeService:
    """Records which adapters were synced; per-rover outcome; checks runs never overlap."""

    def __init__(self, outcomes: dict[str, BaseException] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def run(self, adapter: Any, **kwargs: Any) -> SyncResult:
        self.calls.append((adapter.rover_id, kwargs))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if adapter.rover_id in self.outcomes:
                raise self.outcomes[adapter.rover_id]
            return result_for(adapter.rover_id)
        finally:
            self.in_flight -= 1


@pytest.fixture
async def client() -> AsyncIterator[UpstreamClient]:
    upstream = UpstreamClient(make_settings())
    yield upstream
    await upstream.aclose()


def test_syncable_rovers_are_pinned() -> None:
    assert SYNCABLE_ROVERS == ("perseverance", "curiosity")


async def test_run_once_syncs_every_rover_sequentially_incremental(
    client: UpstreamClient,
) -> None:
    service = FakeService()
    scheduler = SyncScheduler(service, client, make_settings())  # type: ignore[arg-type]

    results = await scheduler.run_once()

    assert [rover for rover, _ in service.calls] == list(SYNCABLE_ROVERS)
    # Incremental: no range passed (or both bounds None).
    for _, kwargs in service.calls:
        assert kwargs.get("from_sol") is None and kwargs.get("to_sol") is None
    assert service.max_in_flight == 1
    assert [r.rover_id for r in results] == list(SYNCABLE_ROVERS)


async def test_run_once_skips_rover_with_sync_in_progress(client: UpstreamClient) -> None:
    service = FakeService({"perseverance": SyncInProgress("perseverance")})
    scheduler = SyncScheduler(service, client, make_settings())  # type: ignore[arg-type]

    results = await scheduler.run_once()

    assert [rover for rover, _ in service.calls] == ["perseverance", "curiosity"]
    assert [r.rover_id for r in results] == ["curiosity"]


async def test_run_once_continues_after_unexpected_exception(
    client: UpstreamClient, caplog: pytest.LogCaptureFixture
) -> None:
    service = FakeService({"perseverance": RuntimeError("db is down")})
    scheduler = SyncScheduler(service, client, make_settings())  # type: ignore[arg-type]

    results = await scheduler.run_once()

    assert [rover for rover, _ in service.calls] == ["perseverance", "curiosity"]
    assert [r.rover_id for r in results] == ["curiosity"]
    assert any("perseverance" in r.getMessage() for r in caplog.records)


# --- app lifespan ------------------------------------------------------------------------


def apscheduler_of(scheduler: SyncScheduler) -> BaseScheduler:
    found = [v for v in vars(scheduler).values() if isinstance(v, BaseScheduler)]
    assert len(found) == 1, "SyncScheduler must wrap exactly one APScheduler instance"
    return found[0]


async def test_lifespan_sync_disabled_creates_no_scheduler(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"sync_enabled": False}))

    async with app.router.lifespan_context(app):
        assert app.state.scheduler is None

    assert app.state.scheduler is None


async def test_lifespan_sync_enabled_starts_interval_job_and_shuts_down(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = asyncio.Event()

    async def fake_run_once(self: SyncScheduler) -> list[SyncResult]:
        ran.set()
        return []

    monkeypatch.setattr(scheduler_module.SyncScheduler, "run_once", fake_run_once)
    app = create_app(
        settings.model_copy(update={"sync_enabled": True, "sync_interval_minutes": 45})
    )

    async with app.router.lifespan_context(app):
        scheduler = app.state.scheduler
        assert isinstance(scheduler, SyncScheduler)
        assert isinstance(app.state.upstream_client, UpstreamClient)
        aps = apscheduler_of(scheduler)
        assert aps.running
        [job] = aps.get_jobs()
        assert job.id == "sync-all"
        assert isinstance(job.trigger, IntervalTrigger)
        assert job.trigger.interval == timedelta(minutes=45)
        assert job.max_instances == 1
        assert job.coalesce is True
        # First run right after start.
        await asyncio.wait_for(ran.wait(), timeout=10)

    # AsyncIOScheduler.shutdown() is applied via call_soon on the loop: give it one tick.
    await asyncio.sleep(0)
    assert not aps.running
