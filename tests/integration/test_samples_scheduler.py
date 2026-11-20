"""Scheduler hook: `SyncScheduler.run_once` -> `SampleSyncService.refresh_due` (stage 13).

The photo sync runs on in-memory adapters; the samples page is either replaced by a recording
stub of `refresh_due` or mocked with respx (end to end). A failing samples check must never stop
the photo sync or the location refresh; `SAMPLES_SYNC_ENABLED=false` makes no request at all.
"""

from datetime import datetime
from typing import Any

import pytest
import respx

from app.core.config import Settings
from app.main import create_app
from app.services import scheduler as scheduler_module
from app.services.location_sync import LocationSyncService
from app.services.sample_sync import SampleSyncService
from tests._samples import EXPECTED, NUMBERS, Factory, as_plain, count, dataset, mock_page
from tests._samples import sample_rows as stored_rows
from tests.integration._fakes import FakeAdapter
from tests.integration.test_location_sync import run_job_once

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Photo adapters become in-memory fakes; returns the order of events."""
    log: list[Any] = []

    def build_adapter(rover_id: str, client: Any, _settings: Any) -> FakeAdapter:
        log.append(("photos", rover_id))
        return FakeAdapter(rover_id, latest=0)

    monkeypatch.setattr(scheduler_module, "build_adapter", build_adapter)
    return log


@pytest.fixture
def stub_locations(monkeypatch: pytest.MonkeyPatch, events: list[Any]) -> None:
    async def refresh_due(self: LocationSyncService, now: datetime | None = None) -> list[Any]:
        events.append("locations")
        return []

    monkeypatch.setattr(LocationSyncService, "refresh_due", refresh_due)


def enabled(settings: Settings, **extra: Any) -> Settings:
    return settings.model_copy(
        update={"sync_enabled": True, "upstream_max_retries": 0, "upstream_max_rps": 0, **extra}
    )


@pytest.mark.usefixtures("stub_locations")
async def test_run_once_refreshes_samples_after_the_photos_and_locations(
    settings: Settings, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refresh_due(self: SampleSyncService, now: datetime | None = None) -> Any:
        events.append("samples")
        return None

    monkeypatch.setattr(SampleSyncService, "refresh_due", refresh_due)

    results = await run_job_once(create_app(enabled(settings)))

    assert [r.rover_id for r in results] == ["perseverance", "curiosity"]
    assert events == [("photos", "perseverance"), ("photos", "curiosity"), "locations", "samples"]


@pytest.mark.usefixtures("stub_locations")
async def test_run_once_survives_a_crash_of_the_samples_refresh(
    settings: Settings, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refresh_due(self: SampleSyncService, now: datetime | None = None) -> Any:
        events.append("samples")
        raise RuntimeError("samples source exploded")

    monkeypatch.setattr(SampleSyncService, "refresh_due", refresh_due)

    results = await run_job_once(create_app(enabled(settings)))

    assert [r.status for r in results] == ["success", "success"]
    assert events[-2:] == ["locations", "samples"]


@pytest.mark.usefixtures("stub_locations")
async def test_run_once_does_not_touch_the_samples_when_the_sync_is_disabled(
    settings: Settings,
    events: list[Any],
    upstream_router: respx.MockRouter,
    clean_db: Factory,
) -> None:
    route = mock_page(upstream_router)
    app = create_app(enabled(settings, samples_sync_enabled=False))

    results = await run_job_once(app)

    assert len(results) == 2
    assert route.call_count == 0
    assert await count(clean_db, "sample_datasets") == 0
    assert await count(clean_db, "rover_samples") == 0


async def test_run_once_loads_the_samples_end_to_end(
    settings: Settings,
    events: list[Any],
    upstream_router: respx.MockRouter,
    clean_db: Factory,
) -> None:
    route = mock_page(upstream_router)
    app = create_app(enabled(settings, location_sync_enabled=False))

    results = await run_job_once(app)

    assert [r.status for r in results] == ["success", "success"]
    assert route.call_count == 1
    assert [as_plain(r) for r in await stored_rows(clean_db)] == [EXPECTED[n] for n in NUMBERS]
    assert (await dataset(clean_db))["items"] == 7


async def test_failing_samples_source_does_not_stop_the_photo_sync(
    settings: Settings,
    events: list[Any],
    upstream_router: respx.MockRouter,
    clean_db: Factory,
) -> None:
    import httpx

    from tests._samples import SAMPLES_URL

    route = upstream_router.get(SAMPLES_URL).mock(return_value=httpx.Response(500))
    app = create_app(enabled(settings, location_sync_enabled=False))

    results = await run_job_once(app)

    assert [r.status for r in results] == ["success", "success"]
    assert route.call_count == 1
    assert await count(clean_db, "rover_samples") == 0
    assert (await dataset(clean_db))["last_error"]
