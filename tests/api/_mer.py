"""Shared MER dataset for the v1 and v2 API tests of Spirit and Opportunity (stage 8).

``mer_world`` imports both rovers from the real index fixtures with ``import-mer`` (the
production path: MerAdapter -> BackfillService -> SyncService) and adds a few Curiosity and
Perseverance photos of every sample type, so the tests can also check that the MER-only
v1 rule ("all frame types") does not leak to the other rovers.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from app.core.config import get_settings
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import _clean_database, build_record
from tests.unit._mer import MER_FIXTURES, Frame, reference_frames

Factory = async_sessionmaker[AsyncSession]

# Curiosity / Perseverance photos on one sol, every sample type (full ones are visible).
OTHER_SOL = 100
OTHER_RECORDS: dict[str, list[tuple[str, str, str]]] = {
    "curiosity": [
        ("o-c-full", "FHAZ", "full"),
        ("o-c-sub", "FHAZ", "subframe"),
        ("o-c-down", "FHAZ", "downsampled"),
    ],
    "perseverance": [
        ("o-p-full", "NAVCAM_LEFT", "full"),
        ("o-p-sub", "NAVCAM_LEFT", "subframe"),
        ("o-p-thumb", "NAVCAM_LEFT", "thumbnail"),
    ],
}


@dataclass
class MerWorld:
    frames: dict[str, list[Frame]] = field(default_factory=dict)  # rover -> frames
    photo_ids: dict[str, int] = field(default_factory=dict)  # source_id -> photos.id
    camera_ids: dict[tuple[str, str], int] = field(default_factory=dict)  # (rover, code) -> id

    def frame(self, source_id: str) -> Frame:
        return next(f for fs in self.frames.values() for f in fs if f.source_id == source_id)

    def sol_frames(self, rover: str, sol: int) -> list[Frame]:
        return [f for f in self.frames[rover] if f.sol == sol]


def _import(rover: str, database_url: str) -> int:
    env = {
        "DATABASE_URL": database_url,
        "SYNC_ENABLED": "false",
        "MER_CACHE_DIR": str(MER_FIXTURES),
        "UPSTREAM_MAX_RPS": "0",
        "BACKFILL_LOCK_MAX_WAIT_SECONDS": "0",
    }
    with pytest.MonkeyPatch.context() as mp:
        for key, value in env.items():
            mp.setenv(key, value)
        get_settings.cache_clear()
        try:
            return cli.main(["import-mer", "--rover", rover, "--batch-sols", "100000"])
        finally:
            get_settings.cache_clear()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def mer_world(migrated_db: str) -> AsyncIterator[MerWorld]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean_database(factory)
        for rover in ("spirit", "opportunity"):
            assert await asyncio.to_thread(_import, rover, migrated_db) == 0, rover
        async with factory() as session:
            for rover, items in OTHER_RECORDS.items():
                records = [
                    build_record(source_id=s, sol=OTHER_SOL, camera_code=c, sample_type=t)
                    for s, c, t in items
                ]
                await upsert_photos(session, rover, records)
                await recompute_sol_summaries(session, rover, {OTHER_SOL})
                await refresh_rover_stats(session, rover)
            await session.commit()
        world = MerWorld(frames={r: reference_frames(r) for r in ("spirit", "opportunity")})
        async with factory() as session:
            rows = await session.execute(text("SELECT source_id, id FROM photos"))
            world.photo_ids = {source_id: photo_id for source_id, photo_id in rows}
            rows = await session.execute(text("SELECT rover_id, code, id FROM cameras"))
            world.camera_ids = {(rover, code): cam_id for rover, code, cam_id in rows}
        yield world
    finally:
        try:
            await _clean_database(factory)
        finally:
            await engine.dispose()


def ids_of(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]
