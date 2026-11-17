"""App lifespan with the sync scheduler: shutdown while a sync is running.

Real SyncService on the test database; the adapter is a blocking FakeAdapter, so no
network request is ever made.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.services import scheduler as scheduler_module
from app.services.sync import SYNC_LOCK_NAMESPACE
from app.sources.http import UpstreamClient
from tests.integration._fakes import FakeAdapter, legacy_id, scalar, sync_runs

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]


async def test_lifespan_shutdown_during_sync_fails_run_releases_lock_closes_client(
    settings: Settings,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    built: list[str] = []

    async def block() -> None:
        entered.set()
        await asyncio.Event().wait()

    def build_adapter(rover_id: str, client: Any, _settings: Any) -> FakeAdapter:
        built.append(rover_id)
        return FakeAdapter(rover_id, latest=10, on_latest=block)

    closed: list[UpstreamClient] = []
    original_aclose = UpstreamClient.aclose

    async def aclose(self: UpstreamClient) -> None:
        closed.append(self)
        await original_aclose(self)

    monkeypatch.setattr(scheduler_module, "build_adapter", build_adapter)
    monkeypatch.setattr(UpstreamClient, "aclose", aclose)
    app = create_app(settings.model_copy(update={"sync_enabled": True}))

    async with app.router.lifespan_context(app):
        client = app.state.upstream_client
        await asyncio.wait_for(entered.wait(), timeout=10)
        [running] = await sync_runs(session_factory)
        assert running["status"] == "running"

    [run] = await sync_runs(session_factory)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    # Shutdown stops the job: no other rover is started afterwards.
    assert built == [built[0]]
    key = await legacy_id(session_factory, built[0])
    holders = await scalar(
        session_factory,
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = :ns AND objid = :key AND objsubid = 2",
        ns=SYNC_LOCK_NAMESPACE,
        key=key,
    )
    assert holders == 0
    assert client in closed
    async with session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM sync_runs")) == 1
