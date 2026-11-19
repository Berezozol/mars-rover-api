"""Sync -> listeners chain (stage 9 alerts; SyncService.add_listener / SyncHandle.wait()).

- ``wait()`` calls every listener with the ``SyncResult`` after a successful, partial or
  upstream-failed sync, and with a ``failed`` result (with ``error``) when the sync crashed.
- A listener exception neither breaks the sync nor changes its result; later listeners run.
- The rover's advisory lock is already released when the listeners run.
- A cancelled sync does not notify.
- ``create_app`` with Telegram configured subscribes the alert monitor to the sync service:
  a failed sync posts to Telegram (mocked with respx).

The upstream is the in-memory ``FakeAdapter`` (no network), as in test_sync_start.py.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.main import create_app
from app.services.sync import SyncResult, SyncService
from app.sources import http
from tests.integration._fakes import FakeAdapter, sol_records, sync_runs

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

Factory = async_sessionmaker[AsyncSession]
WAIT = 10


class Recorder:
    def __init__(self, error: BaseException | None = None) -> None:
        self.results: list[SyncResult] = []
        self.error = error

    async def __call__(self, result: SyncResult) -> None:
        self.results.append(result)
        if self.error is not None:
            raise self.error


@pytest.fixture
def service(session_factory: Factory, settings: Settings) -> SyncService:
    return SyncService(
        session_factory, settings.model_copy(update={"sync_overlap_sols": 7, "sync_chunk_sols": 10})
    )


async def test_listener_called_once_after_successful_sync(service: SyncService) -> None:
    listener = Recorder()
    service.add_listener(listener)
    handle = await service.start(
        FakeAdapter("curiosity").add(sol_records("curiosity", 2, 3)), from_sol=0, to_sol=3
    )

    result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert result.status == "success"
    assert listener.results == [result]
    assert listener.results[0].run_id == handle.run_id
    assert listener.results[0].rover_id == "curiosity"
    assert listener.results[0].photos_upserted == 3


async def test_listener_called_after_upstream_failure_with_failed_result(
    service: SyncService,
) -> None:
    listener = Recorder()
    service.add_listener(listener)
    error = http.UpstreamError("https://feed.example.test/", 500, "boom")
    handle = await service.start(FakeAdapter("curiosity", latest_error=error))

    result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert result.status == "failed"
    assert listener.results == [result]


async def test_listener_called_after_partial_sync(service: SyncService) -> None:
    listener = Recorder()
    service.add_listener(listener)
    error = http.UpstreamError("https://feed.example.test/", 500, "boom")
    adapter = FakeAdapter("curiosity", fail_at={1: error}).add(sol_records("curiosity", 2, 1))
    handle = await service.start(adapter, from_sol=0, to_sol=3)

    result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert result.status == "partial"
    assert listener.results == [result]
    assert listener.results[0].failed_sols


async def test_listener_called_after_crash_with_failed_status_and_error(
    service: SyncService, session_factory: Factory
) -> None:
    listener = Recorder()
    service.add_listener(listener)
    handle = await service.start(
        FakeAdapter("curiosity", fail_at={1: RuntimeError("adapter bug")}), from_sol=0, to_sol=1
    )

    with pytest.raises(RuntimeError, match="adapter bug"):
        await asyncio.wait_for(handle.wait(), timeout=WAIT)

    [reported] = listener.results
    assert reported.status == "failed"
    assert reported.run_id == handle.run_id
    assert reported.rover_id == "curiosity"
    assert reported.error and "adapter bug" in reported.error
    [row] = await sync_runs(session_factory)
    assert row["status"] == "failed"


async def test_listener_exception_does_not_break_sync_nor_change_result(
    service: SyncService, session_factory: Factory
) -> None:
    broken = Recorder(error=RuntimeError("listener bug"))
    after = Recorder()
    service.add_listener(broken)
    service.add_listener(after)
    handle = await service.start(
        FakeAdapter("curiosity").add(sol_records("curiosity", 1, 2)), from_sol=0, to_sol=1
    )

    result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert result.status == "success"
    assert result.photos_upserted == 2
    assert broken.results == [result]
    assert after.results == [result], "a failing listener must not stop the next ones"
    [row] = await sync_runs(session_factory)
    assert row["status"] == "success"


async def test_listener_runs_after_lock_is_released(service: SyncService) -> None:
    outcome: list[Any] = []

    async def start_again(result: SyncResult) -> None:
        try:
            handle = await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
        except SyncInProgress as exc:
            outcome.append(exc)
            return
        outcome.append("started")
        await handle.close()

    service.add_listener(start_again)
    handle = await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert outcome == ["started"], "the lock must be free when the listeners run"


async def test_listener_not_called_for_cancelled_sync(service: SyncService) -> None:
    listener = Recorder()
    service.add_listener(listener)
    entered = asyncio.Event()

    async def hang(from_sol: int, to_sol: int) -> None:
        entered.set()
        await asyncio.Event().wait()

    handle = await service.start(FakeAdapter("curiosity", latest=10, on_fetch=hang))
    task = asyncio.create_task(handle.wait())
    await asyncio.wait_for(entered.wait(), timeout=WAIT)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert listener.results == []


async def test_listener_not_called_for_closed_handle(service: SyncService) -> None:
    listener = Recorder()
    service.add_listener(listener)
    handle = await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    await handle.close()

    assert listener.results == []


# --- wiring in create_app ------------------------------------------------------------------

TOKEN = "777000:AAListener-Wiring-Token"
CHAT_ID = "31337"
API_BASE = "https://telegram.example.test"


async def test_create_app_with_telegram_subscribes_monitor_to_sync(
    settings: Settings, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.post(f"{API_BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    app = create_app(
        settings.model_copy(
            update={
                "telegram_bot_token": SecretStr(TOKEN),
                "telegram_chat_id": CHAT_ID,
                "telegram_api_base": API_BASE,
            }
        )
    )
    try:
        error = http.UpstreamError("https://feed.example.test/", 500, "boom")
        result = await asyncio.wait_for(
            app.state.sync_service.run(FakeAdapter("perseverance", latest_error=error)),
            timeout=WAIT,
        )
    finally:
        await app.state.engine.dispose()

    assert result.status == "failed"
    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert str(payload["chat_id"]) == CHAT_ID
    assert payload["text"].startswith("[mars-rover-api]")
    assert "perseverance" in payload["text"].lower()


async def test_create_app_with_telegram_success_sync_sends_nothing(
    settings: Settings, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.post(f"{API_BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    app = create_app(
        settings.model_copy(
            update={
                "telegram_bot_token": SecretStr(TOKEN),
                "telegram_chat_id": CHAT_ID,
                "telegram_api_base": API_BASE,
            }
        )
    )
    try:
        adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 1, 1))
        result = await asyncio.wait_for(
            app.state.sync_service.run(adapter, from_sol=0, to_sol=1), timeout=WAIT
        )
    finally:
        await app.state.engine.dispose()

    assert result.status == "success"
    assert not route.called


async def test_create_app_without_telegram_has_no_monitor(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"telegram_bot_token": None}))
    try:
        assert getattr(app.state, "alert_monitor", None) is None
    finally:
        await app.state.engine.dispose()
