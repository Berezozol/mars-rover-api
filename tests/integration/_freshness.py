"""Data helpers for the stage-9 freshness, `/health/sync` and alert tests.

Rows are committed (use with `clean_db`): sync runs are inserted directly, photos go through
the repositories in the order the sync uses them.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

Factory = async_sessionmaker[AsyncSession]

SYNCED_ROVERS = ["curiosity", "perseverance"]
CAMERA = {
    "curiosity": "FHAZ",
    "perseverance": "NAVCAM_LEFT",
    "spirit": "PANCAM",
    "opportunity": "PANCAM",
}
ROVER_KEYS = ["rover", "last_success_at", "newest_photo_date", "sync_stale", "photos_stale"]


async def add_run(
    factory: Factory,
    rover_id: str,
    *,
    status: str = "success",
    finished_at: datetime | None,
    mode: str = "incremental",
    started_at: datetime | None = None,
    error: str | None = None,
) -> int:
    started = started_at or finished_at
    if started is None:
        raise ValueError("started_at is required for a run without finished_at")
    async with factory() as session:
        run_id = await session.scalar(
            text(
                "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, started_at, "
                "finished_at, photos_upserted, failed_sols, error) VALUES (:rover_id, :mode, 0, "
                "10, :status, :started_at, :finished_at, 0, :failed_sols, :error) RETURNING id"
            ),
            {
                "rover_id": rover_id,
                "mode": mode,
                "status": status,
                "started_at": started,
                "finished_at": finished_at,
                "failed_sols": [3] if status == "partial" else [],
                "error": error,
            },
        )
        await session.commit()
    return int(run_id)


async def add_photos(
    factory: Factory, rover_id: str, dates: list[date], *, first_sol: int = 100
) -> None:
    """One photo per date; sols increase with the dates (the newest date is the max sol)."""
    ordered = sorted(dates)
    records = [
        build_record(
            sol=first_sol + n,
            earth_date=day,
            camera_code=CAMERA[rover_id],
            source_id=f"fresh-{rover_id}-{first_sol + n}-{day.isoformat()}",
        )
        for n, day in enumerate(ordered)
    ]
    async with factory() as session:
        await upsert_photos(session, rover_id, records)
        await recompute_sol_summaries(session, rover_id, sorted({r.sol for r in records}))
        await refresh_rover_stats(session, rover_id)
        await session.commit()


def to_jsonable(value: Any) -> Any:
    """Normalize the freshness result (pydantic model, dataclass, mapping) to plain data."""
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    return value


def by_rover(result: Any) -> dict[str, dict[str, Any]]:
    data = to_jsonable(result)
    rovers = data["rovers"] if isinstance(data, dict) else data
    return {item["rover"]: item for item in rovers}


def overall_status(result: Any) -> str | None:
    data = to_jsonable(result)
    return data.get("status") if isinstance(data, dict) else None
