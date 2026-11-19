"""Admin logic of API v2 (docs/API.md, "Admin"): start a sync in the background, list runs.

A sync is started synchronously (validation, rover lock, run row) so the request can answer
409 or 202; the rest runs as a background task kept in a caller-owned set of tasks.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParam, NoSourceForRover
from app.db.models import SyncRun
from app.db.session import db_io_errors
from app.repositories import sync_runs as runs_repo
from app.services.rover_lookup import find_rover
from app.services.sync import SyncHandle, SyncService
from app.services.v2 import PageRequest
from app.sources.base import SourceAdapter
from app.sources.registry import SYNCABLE_ROVERS

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[str], SourceAdapter]
SyncTasks = set[asyncio.Task[None]]


@dataclass(frozen=True)
class SyncRequest:
    rover: str
    from_sol: int | None = None
    to_sol: int | None = None


@dataclass(frozen=True)
class RunPage:
    items: list[SyncRun]
    page: int
    per_page: int
    total: int


async def start_sync(
    session: AsyncSession,
    service: SyncService,
    adapter_factory: AdapterFactory,
    tasks: SyncTasks,
    request: SyncRequest,
) -> SyncHandle:
    """Start a sync and continue it in the background; raises 404 / 409 / 422 errors.

    Order: range combination, rover slug (no query), rover row, syncable rover, lock.
    """
    _check_range(request.from_sol, request.to_sol)
    rover = await find_rover(session, request.rover)
    if rover.id not in SYNCABLE_ROVERS:
        raise NoSourceForRover(rover.id)
    adapter = adapter_factory(rover.id)
    async with db_io_errors():
        handle = await service.start(adapter, from_sol=request.from_sol, to_sol=request.to_sol)
    launch_background(handle, tasks)
    return handle


async def list_runs(session: AsyncSession, rover: str | None, page: PageRequest) -> RunPage:
    """Runs newest first, optionally of one rover (unknown or malformed → 404)."""
    rover_id = (await find_rover(session, rover)).id if rover is not None else None
    total = await runs_repo.count_runs(session, rover_id=rover_id)
    items: list[SyncRun] = []
    if page.offset < total:
        items = await runs_repo.list_runs(
            session, rover_id=rover_id, limit=page.per_page, offset=page.offset
        )
    return RunPage(items, page.page, page.per_page, total)


def launch_background(handle: SyncHandle, tasks: SyncTasks) -> asyncio.Task[None]:
    """Run ``handle.wait()`` as a task held in ``tasks`` until it is done.

    Started eagerly, so ``wait()`` owns the lock before anyone can cancel the task: a task
    cancelled before its first step would never release it.
    """
    loop = asyncio.get_running_loop()
    task: asyncio.Task[None] = asyncio.Task(
        _wait_logged(handle), loop=loop, name=f"admin-sync-{handle.run_id}", eager_start=True
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def _wait_logged(handle: SyncHandle) -> None:
    try:
        result = await handle.wait()
    except asyncio.CancelledError:
        logger.warning("Admin sync cancelled run_id=%d rover=%s", handle.run_id, handle.rover_id)
        raise
    except Exception:
        logger.exception("Admin sync crashed run_id=%d rover=%s", handle.run_id, handle.rover_id)
        return
    logger.info(
        "Admin sync finished run_id=%d rover=%s status=%s",
        result.run_id,
        result.rover_id,
        result.status,
    )


async def cancel_background(tasks: SyncTasks) -> None:
    """Cancel every background sync and wait until each has marked its run and unlocked."""
    pending = list(tasks)
    for task in pending:
        task.cancel()
    results = await asyncio.gather(*pending, return_exceptions=True)
    for task, result in zip(pending, results, strict=True):
        if isinstance(result, Exception):
            logger.error("Background sync %s ended with %r", task.get_name(), result)


def _check_range(from_sol: int | None, to_sol: int | None) -> None:
    if from_sol is None and to_sol is not None:
        raise InvalidParam("from_sol", "from_sol and to_sol must be given together")
    if to_sol is None and from_sol is not None:
        raise InvalidParam("to_sol", "from_sol and to_sol must be given together")
    if from_sol is not None and to_sol is not None and from_sol > to_sol:
        raise InvalidParam("from_sol", "from_sol must be less than or equal to to_sol")
