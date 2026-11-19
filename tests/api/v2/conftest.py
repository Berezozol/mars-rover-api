"""Shared seed and expected-object builders for API v2 (docs/API.md).

Read-only v2 tests share one dataset committed once per module (`v2_world`): the database is
wiped before seeding and again after the module's last test. A module that uses `v2_world`
must not also use `clean_db` (it would wipe the seed mid-module); modules that need their own
data use `clean_db` alone (e.g. test_total_regression.py).

Data is committed through the repositories in the order the sync uses
(upsert_photos -> recompute_sol_summaries -> refresh_rover_stats). Expected JSON is built from
the contract; only database-generated ids (photos.id) and camera names (catalog data) are read
back from the database.

Dataset:
- perseverance: sols 2030 and 2031, all image sizes, a tie on taken_at, a photo taken after
  UTC midnight (real earth_date differs inside one sol), PhotoDetail fields, last_synced_at
  with microseconds;
- curiosity: sols 1, 5, 10, 20; sample types full/subframe/downsampled/mixed/"chemcam prc";
  FHAZ and FHAZ_LEFT_B (no camera groups in v2); sol 20 has only a subframe, so the max sol
  over all types (20) differs from the max full-frame sol (10);
- opportunity: 30 PANCAM photos on sol 10 for pagination;
- spirit: no data.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import SEED_CAMERA_COUNTS, _clean_database, build_record

V2 = "/api/v2"
MER_ROVERS = ("spirit", "opportunity")

ROVER_IDS = ["curiosity", "opportunity", "perseverance", "spirit"]  # sorted by id

# Static rover data (migration 0002 seed snapshot).
ROVER_STATIC: dict[str, dict[str, Any]] = {
    "curiosity": {
        "id": "curiosity",
        "name": "Curiosity",
        "mission": "msl",
        "launch_date": "2011-11-26",
        "landing_date": "2012-08-06",
        "status": "active",
    },
    "opportunity": {
        "id": "opportunity",
        "name": "Opportunity",
        "mission": "mer",
        "launch_date": "2003-07-07",
        "landing_date": "2004-01-25",
        "status": "complete",
    },
    "perseverance": {
        "id": "perseverance",
        "name": "Perseverance",
        "mission": "mars2020",
        "launch_date": "2020-07-30",
        "landing_date": "2021-02-18",
        "status": "active",
    },
    "spirit": {
        "id": "spirit",
        "name": "Spirit",
        "mission": "mer",
        "launch_date": "2003-06-10",
        "landing_date": "2004-01-04",
        "status": "complete",
    },
}

ROVER_KEYS = [
    "id",
    "name",
    "mission",
    "launch_date",
    "landing_date",
    "status",
    "max_sol",
    "max_date",
    "total_photos",
    "cameras_count",
    "last_synced_at",
]
CAMERA_KEYS = ["code", "name", "total_photos"]
PHOTO_KEYS = [
    "id",
    "rover",
    "sol",
    "earth_date",
    "taken_at",
    "camera",
    "sample_type",
    "title",
    "img_src",
    "images",
    "credit",
    "source",
]
PHOTO_DETAIL_KEYS = [*PHOTO_KEYS, "caption", "received_at", "site", "drive", "extra"]
LIST_KEYS = ["items", "page", "per_page", "total"]
LATEST_KEYS = ["sol", "items", "page", "per_page", "total"]
MANIFEST_KEYS = ["rover", "sols"]
MANIFEST_SOL_KEYS = ["sol", "earth_date", "total_photos", "cameras"]
ERROR_KEYS = ["code", "message", "details"]

INT32_MAX = 2**31 - 1
HUGE = "9" * 5000

# Exact datetime format of v2: no fractional seconds, UTC, "Z".
LAST_SYNCED_PERSEVERANCE = "2026-11-17T06:00:12.345678+00:00"
LAST_SYNCED_PERSEVERANCE_JSON = "2026-11-17T06:00:12Z"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _img(source_id: str, suffix: str) -> str:
    return f"https://images.example.test/{source_id}{suffix}"


def _perseverance(source_id: str, **overrides: Any) -> Any:
    taken_at: datetime = overrides["taken_at"]
    values: dict[str, Any] = {
        "source_id": source_id,
        "earth_date": taken_at.date(),
        "img_small": _img(source_id, "_320.jpg"),
        "img_medium": _img(source_id, "_800.jpg"),
        "img_large": _img(source_id, "_1200.jpg"),
        "img_full": _img(source_id, ".png"),
        "title": f"Mars Perseverance Sol {overrides['sol']}: {source_id}",
        "credit": "NASA/JPL-Caltech/LANL/CNES/IRAP",
        "source_link": f"https://mars.nasa.gov/mars2020/multimedia/raw-images/{source_id}",
    }
    values.update(overrides)
    return build_record(**values)


def perseverance_records() -> list[Any]:
    """Inserted one by one: photos.id follows list order (tie1 < tie2 < early)."""
    return [
        _perseverance(
            "p2031-a",
            sol=2031,
            camera_code="NAVCAM_LEFT",
            sample_type="full",
            # .999999 must be dropped, not rounded up to :26.
            taken_at=utc(2026, 11, 6, 12, 16, 25, 999999),
            received_at=utc(2026, 11, 7, 1, 2, 3, 456789),
            caption="Raw image taken by NASA's Perseverance rover.",
            site=80,
            drive=1234,
            extra={"filter_name": "UNK", "attitude": "(0.1,0.2,0.3,0.4)"},
        ),
        _perseverance(
            "p2031-tie1",
            sol=2031,
            camera_code="NAVCAM_RIGHT",
            sample_type="full",
            taken_at=utc(2026, 11, 6, 10, 0, 0),
        ),
        _perseverance(
            "p2031-tie2",
            sol=2031,
            camera_code="NAVCAM_RIGHT",
            sample_type="subframe",
            taken_at=utc(2026, 11, 6, 10, 0, 0),
        ),
        _perseverance(
            "p2031-early",
            sol=2031,
            camera_code="MCZ_LEFT",
            sample_type="downsampled",
            taken_at=utc(2026, 11, 6, 8, 0, 0),
        ),
        # Same sol, taken after UTC midnight: real earth_date is the next day.
        _perseverance(
            "p2031-next-day",
            sol=2031,
            camera_code="NAVCAM_LEFT",
            sample_type="full",
            taken_at=utc(2026, 11, 7, 0, 30, 0),
        ),
        # Lower sol with a later taken_at: sol DESC wins over taken_at.
        _perseverance(
            "p2030-late",
            sol=2030,
            camera_code="NAVCAM_LEFT",
            sample_type="full",
            taken_at=utc(2026, 11, 8, 0, 0, 0),
        ),
        _perseverance(
            "p2030-sub",
            sol=2030,
            camera_code="SUPERCAM_RMI",
            sample_type="subframe",
            taken_at=utc(2026, 11, 5, 9, 0, 0),
        ),
    ]


PERSEVERANCE_ORDER = [
    "p2031-next-day",
    "p2031-a",
    "p2031-tie2",  # same taken_at as tie1, greater id
    "p2031-tie1",
    "p2031-early",
    "p2030-late",
    "p2030-sub",
]
PERSEVERANCE_SOL_2031 = PERSEVERANCE_ORDER[:5]


def _curiosity(source_id: str, sol: int, code: str, sample_type: str, hour: int) -> Any:
    taken_at = datetime.combine(date(2012, 8, 6) + timedelta(days=sol), datetime.min.time())
    taken_at = taken_at.replace(hour=hour, tzinfo=UTC)
    return build_record(
        source_id=source_id,
        sol=sol,
        camera_code=code,
        sample_type=sample_type,
        taken_at=taken_at,
        earth_date=taken_at.date(),
        img_full=_img(source_id, ".JPG"),
        credit="NASA/JPL-Caltech/MSSS",
        source_link=f"https://mars.nasa.gov/msl/multimedia/raw-images/?id={source_id}",
    )


def curiosity_records() -> list[Any]:
    return [
        _curiosity("c1-fhaz", 1, "FHAZ", "full", 1),
        _curiosity("c1-fhaz-lb", 1, "FHAZ_LEFT_B", "full", 2),
        _curiosity("c1-fhaz-lb-sub", 1, "FHAZ_LEFT_B", "subframe", 3),
        _curiosity("c1-mixed", 1, "MAST_LEFT", "mixed", 4),
        _curiosity("c1-chem", 1, "CHEMCAM_RMI", "chemcam prc", 5),
        _curiosity("c1-down", 1, "MAST_LEFT", "downsampled", 6),
        _curiosity("c5-mahli", 5, "MAHLI", "full", 7),
        _curiosity("c10-nav", 10, "NAVCAM", "full", 8),
        _curiosity("c20-sub", 20, "FHAZ", "subframe", 9),
    ]


CURIOSITY_ORDER = [
    "c20-sub",
    "c10-nav",
    "c5-mahli",
    "c1-down",
    "c1-chem",
    "c1-mixed",
    "c1-fhaz-lb-sub",
    "c1-fhaz-lb",
    "c1-fhaz",
]
CURIOSITY_NEW_CODES = ["FHAZ_LEFT_B", "MAST_LEFT", "CHEMCAM_RMI"]

OPPORTUNITY_COUNT = 30
OPPORTUNITY_SOL = 10


def opportunity_records() -> list[Any]:
    base = datetime(2004, 2, 4, tzinfo=UTC)
    return [
        build_record(
            source_id=f"o-{n:02d}",
            sol=OPPORTUNITY_SOL,
            camera_code="PANCAM",
            taken_at=base + timedelta(minutes=n),
            earth_date=base.date(),
            img_full=_img(f"o-{n:02d}", ".jpg"),
        )
        for n in range(OPPORTUNITY_COUNT)
    ]


# taken_at grows with n, so newest first is n descending.
OPPORTUNITY_ORDER = [f"o-{n:02d}" for n in reversed(range(OPPORTUNITY_COUNT))]

# Rover cache fields over ALL sample types: (max_sol, max_date, total_photos).
STATS: dict[str, tuple[int | None, str | None, int]] = {
    # max_date = earliest real earth_date of sol 2031 (sol_summaries.earth_date).
    "perseverance": (2031, "2026-11-06", 7),
    # sol 20 has only a subframe; it is still the max sol in v2.
    "curiosity": (20, "2012-08-26", 9),
    "opportunity": (OPPORTUNITY_SOL, "2004-02-04", OPPORTUNITY_COUNT),
    "spirit": (None, None, 0),
}
CAMERAS_COUNT = {
    **SEED_CAMERA_COUNTS,
    "curiosity": SEED_CAMERA_COUNTS["curiosity"] + len(CURIOSITY_NEW_CODES),
}
LAST_SYNCED = {"perseverance": LAST_SYNCED_PERSEVERANCE_JSON}


@dataclass
class World:
    photo_ids: dict[str, int] = field(default_factory=dict)  # source_id -> photos.id
    records: dict[str, tuple[str, Any]] = field(default_factory=dict)  # source_id -> (rover, r)
    # rover -> {code: name}, the whole catalog of the rover.
    cameras: dict[str, dict[str, str]] = field(default_factory=dict)

    def rover(self, rover_id: str) -> dict[str, Any]:
        max_sol, max_date, total = STATS[rover_id]
        return {
            **ROVER_STATIC[rover_id],
            "max_sol": max_sol,
            "max_date": max_date,
            "total_photos": total,
            "cameras_count": CAMERAS_COUNT[rover_id],
            "last_synced_at": LAST_SYNCED.get(rover_id),
        }

    def photo(self, source_id: str) -> dict[str, Any]:
        rover_id, record = self.records[source_id]
        code = record.camera_code.upper()
        images = {
            "small": record.img_small,
            "medium": record.img_medium,
            "large": record.img_large,
            "full": record.img_full,
        }
        img_src = record.img_full
        if rover_id in MER_ROVERS:
            # Stage 9a: the stored PDS browse JPEG is the preview (small); full is our
            # conversion. Without source_link there is nothing to convert: full = preview.
            if record.source_link:
                img_src = f"http://test{V2}/photos/{self.photo_ids[source_id]}/image?size=full"
            images = {"small": record.img_full, "medium": None, "large": None, "full": img_src}
        return {
            "id": self.photo_ids[source_id],
            "rover": rover_id,
            "sol": record.sol,
            "earth_date": record.earth_date.isoformat(),
            "taken_at": iso_z(record.taken_at),
            "camera": {"code": code, "name": self.cameras[rover_id][code]},
            "sample_type": record.sample_type,
            "title": record.title,
            "img_src": img_src,
            "images": images,
            "credit": record.credit,
            "source": {"id": record.source_id, "link": record.source_link},
        }

    def photos(self, source_ids: Sequence[str]) -> list[dict[str, Any]]:
        return [self.photo(source_id) for source_id in source_ids]

    def detail(self, source_id: str) -> dict[str, Any]:
        _, record = self.records[source_id]
        return {
            **self.photo(source_id),
            "caption": record.caption,
            "received_at": iso_z(record.received_at),
            "site": record.site,
            "drive": record.drive,
            "extra": record.extra,
        }

    def ids(self, source_ids: Sequence[str]) -> list[int]:
        return [self.photo_ids[source_id] for source_id in source_ids]


Factory = async_sessionmaker[AsyncSession]


async def _seed(factory: Factory, rover_id: str, records: Sequence[Any], one_by_one: bool) -> None:
    async with factory() as session:
        if one_by_one:
            for record in records:
                await upsert_photos(session, rover_id, [record])
        else:
            await upsert_photos(session, rover_id, records)
        await recompute_sol_summaries(session, rover_id, {record.sol for record in records})
        await refresh_rover_stats(session, rover_id)
        await session.commit()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def v2_world(migrated_db: str) -> AsyncIterator[World]:
    """The shared read-only v2 dataset (see the module docstring)."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean_database(factory)
        records = {
            "perseverance": perseverance_records(),
            "curiosity": curiosity_records(),
            "opportunity": opportunity_records(),
        }
        await _seed(factory, "perseverance", records["perseverance"], one_by_one=True)
        await _seed(factory, "curiosity", records["curiosity"], one_by_one=True)
        await _seed(factory, "opportunity", records["opportunity"], one_by_one=False)
        world = World()
        async with factory() as session:
            await session.execute(
                text("UPDATE rovers SET last_synced_at = :ts WHERE id = 'perseverance'"),
                {"ts": datetime.fromisoformat(LAST_SYNCED_PERSEVERANCE)},
            )
            await session.commit()
            rows = await session.execute(text("SELECT source_id, id FROM photos"))
            world.photo_ids = {source_id: photo_id for source_id, photo_id in rows}
            rows = await session.execute(text("SELECT rover_id, code, name FROM cameras"))
            for rover_id, code, name in rows:
                world.cameras.setdefault(rover_id, {})[code] = name
        for rover_id, rover_records in records.items():
            for record in rover_records:
                world.records[record.source_id] = (rover_id, record)
        yield world
    finally:
        try:
            await _clean_database(factory)
        finally:
            await engine.dispose()


@pytest.fixture
def world(v2_world: World) -> World:
    return v2_world


# --- assertion helpers -------------------------------------------------------------------


def assert_json(actual: Any, expected: Any, *, path: str = "$") -> None:
    """Equal values AND equal key order of every object (except the free-form `extra`)."""
    assert actual == expected, path
    if isinstance(expected, dict):
        assert list(actual) == list(expected), f"key order at {path}"
        for key, value in expected.items():
            if key != "extra":
                assert_json(actual[key], value, path=f"{path}.{key}")
    elif isinstance(expected, list):
        for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
            assert_json(got, want, path=f"{path}[{index}]")


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """The v2 error envelope with the given status and code; returns the `error` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert list(body) == ["error"]
    error = body["error"]
    assert list(error) == ERROR_KEYS
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    return error


def assert_validation_error(response: httpx.Response, *fields: str) -> list[dict[str, str]]:
    """422 validation_error whose details name exactly one of `fields` (or all of them)."""
    error = assert_error(response, 422, "validation_error")
    details = error["details"]
    assert isinstance(details, list) and details, error
    for item in details:
        assert list(item) == ["field", "message"], item
        assert isinstance(item["message"], str) and item["message"]
    named = {item["field"] for item in details}
    assert named and named <= set(fields), (named, fields)
    return details
