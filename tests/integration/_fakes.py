"""Test doubles and DB helpers for the sync service tests (no network, no app code changes)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.sources.base import PhotoRecord, validate_sol_range
from tests.conftest import build_record

SessionFactory = async_sessionmaker[AsyncSession]
Hook = Callable[..., Awaitable[None]]

# Valid seed camera codes per rover (any code works, these avoid creating new cameras).
DEFAULT_CAMERA = {"perseverance": "NAVCAM_LEFT", "curiosity": "FHAZ"}
BASE_EARTH_DATE = date(2024, 1, 1)


def earth_date_for(sol: int) -> date:
    """Deterministic, strictly increasing earth date per sol for the fake data."""
    return BASE_EARTH_DATE + timedelta(days=sol)


def sol_records(
    rover_id: str,
    sol: int,
    count: int,
    *,
    sample_type: str = "full",
    camera_code: str | None = None,
    **overrides: Any,
) -> list[PhotoRecord]:
    """``count`` records of one sol with stable ids ``<rover>-<sol>-<n>[-<sample_type>]``."""
    suffix = "" if sample_type == "full" else f"-{sample_type}"
    return [
        build_record(
            source_id=f"{rover_id}-{sol}-{n}{suffix}",
            sol=sol,
            camera_code=camera_code or DEFAULT_CAMERA[rover_id],
            earth_date=earth_date_for(sol),
            sample_type=sample_type,
            **overrides,
        )
        for n in range(count)
    ]


class FakeAdapter:
    """In-memory ``SourceAdapter``.

    - ``latest``: value of ``latest_sol()``; ``latest_error`` is raised instead if set.
    - ``sols``: sol -> records; only sols with records are yielded, in descending order.
    - ``fail_at``: sol -> exception raised by the iterator when it reaches that sol (before
      yielding it), whether or not the sol has records. Sols above it in the same
      ``fetch_sols`` call were already yielded.
    - ``on_latest`` / ``on_fetch``: optional async hooks called at the start of
      ``latest_sol()`` / ``fetch_sols(from, to)`` (e.g. to inspect the DB mid-run or block).
    - ``calls``: request log, ``("latest_sol",)`` and ``("fetch_sols", from, to)``.
    """

    def __init__(
        self,
        rover_id: str,
        *,
        latest: int = 0,
        sols: dict[int, list[PhotoRecord]] | None = None,
        fail_at: dict[int, BaseException] | None = None,
        latest_error: BaseException | None = None,
        on_latest: Hook | None = None,
        on_fetch: Hook | None = None,
    ) -> None:
        self.rover_id = rover_id
        self.latest = latest
        self.sols = dict(sols or {})
        self.fail_at = dict(fail_at or {})
        self.latest_error = latest_error
        self.on_latest = on_latest
        self.on_fetch = on_fetch
        self.calls: list[tuple[Any, ...]] = []
        self.yielded: list[int] = []

    def add(self, records: Iterable[PhotoRecord]) -> FakeAdapter:
        for record in records:
            self.sols.setdefault(record.sol, []).append(record)
        return self

    @property
    def fetch_calls(self) -> list[tuple[int, int]]:
        return [(call[1], call[2]) for call in self.calls if call[0] == "fetch_sols"]

    async def latest_sol(self) -> int:
        self.calls.append(("latest_sol",))
        if self.on_latest is not None:
            await self.on_latest()
        if self.latest_error is not None:
            raise self.latest_error
        return self.latest

    async def fetch_sols(
        self, from_sol: int, to_sol: int
    ) -> AsyncIterator[tuple[int, list[PhotoRecord]]]:
        self.calls.append(("fetch_sols", from_sol, to_sol))
        validate_sol_range(from_sol, to_sol)
        if self.on_fetch is not None:
            await self.on_fetch(from_sol, to_sol)
        for sol in range(to_sol, from_sol - 1, -1):
            if sol in self.fail_at:
                raise self.fail_at[sol]
            records = self.sols.get(sol)
            if records:
                self.yielded.append(sol)
                yield sol, list(records)


# --- raw DB reads (fresh values, bypass any ORM identity map) ----------------------------


async def fetch_all(factory: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]


async def scalar(factory: SessionFactory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def sync_runs(factory: SessionFactory) -> list[dict[str, Any]]:
    return await fetch_all(factory, "SELECT * FROM sync_runs ORDER BY id")


async def rover_row(factory: SessionFactory, rover_id: str) -> dict[str, Any]:
    rows = await fetch_all(factory, "SELECT * FROM rovers WHERE id = :rover", rover=rover_id)
    return rows[0]


async def photo_sols(factory: SessionFactory, rover_id: str) -> dict[int, int]:
    """sol -> number of stored photos."""
    rows = await fetch_all(
        factory,
        "SELECT sol, count(*) AS n FROM photos WHERE rover_id = :rover GROUP BY sol",
        rover=rover_id,
    )
    return {row["sol"]: row["n"] for row in rows}


async def photo_count(factory: SessionFactory, rover_id: str | None = None) -> int:
    if rover_id is None:
        return await scalar(factory, "SELECT count(*) FROM photos")
    return await scalar(factory, "SELECT count(*) FROM photos WHERE rover_id = :r", r=rover_id)


async def summaries(factory: SessionFactory, rover_id: str) -> dict[int, dict[str, Any]]:
    rows = await fetch_all(
        factory, "SELECT * FROM sol_summaries WHERE rover_id = :rover", rover=rover_id
    )
    return {row["sol"]: row for row in rows}


async def legacy_id(factory: SessionFactory, rover_id: str) -> int:
    return await scalar(factory, "SELECT legacy_id FROM rovers WHERE id = :r", r=rover_id)
