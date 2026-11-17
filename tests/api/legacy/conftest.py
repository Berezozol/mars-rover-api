"""Fixtures and expected-object builders for API v1 (docs/LEGACY-API.md).

Data is committed through the repositories in the same order the sync uses
(upsert_photos -> recompute_sol_summaries -> refresh_rover_stats), so the rover counters and
sol_summaries agree with the photos. Every data fixture depends on `clean_db`, which wipes
synced data before and after the test (seed rovers and cameras survive).

Expected JSON is built from the contract, not from the application code: static rover data
comes from the seed table in LEGACY-API.md; only database-generated ids (photos.id,
cameras.id) and the camera catalog order are read back from the database.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.legacy_dates import legacy_earth_date
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats

MakeRecord = Callable[..., Any]
Factory = async_sessionmaker[AsyncSession]

PREFIXES = ["/api/v1", "/mars-photos/api/v1"]

# Static rover data (LEGACY-API.md, "Имя марсохода"); "id" is rovers.legacy_id.
ROVERS: dict[str, dict[str, Any]] = {
    "curiosity": {
        "id": 5,
        "name": "Curiosity",
        "landing_date": "2012-08-06",
        "launch_date": "2011-11-26",
        "status": "active",
    },
    "opportunity": {
        "id": 6,
        "name": "Opportunity",
        "landing_date": "2004-01-25",
        "launch_date": "2003-07-07",
        "status": "complete",
    },
    "spirit": {
        "id": 7,
        "name": "Spirit",
        "landing_date": "2004-01-04",
        "launch_date": "2003-06-10",
        "status": "complete",
    },
    "perseverance": {
        "id": 8,
        "name": "Perseverance",
        "landing_date": "2021-02-18",
        "launch_date": "2020-07-30",
        "status": "active",
    },
}

ROVER_KEYS = [
    "id",
    "name",
    "landing_date",
    "launch_date",
    "status",
    "max_sol",
    "max_date",
    "total_photos",
    "cameras",
]
ROVER_CAMERA_KEYS = ["name", "full_name"]
PHOTO_KEYS = ["id", "sol", "camera", "img_src", "earth_date", "rover"]
PHOTO_CAMERA_KEYS = ["id", "name", "rover_id", "full_name"]
MANIFEST_KEYS = [
    "name",
    "landing_date",
    "launch_date",
    "status",
    "max_sol",
    "max_date",
    "total_photos",
    "photos",
]
MANIFEST_SOL_KEYS = ["sol", "earth_date", "total_photos", "cameras"]

# Seed camera catalog (migration 0002), in sort order.
_MER = ["FHAZ", "RHAZ", "NAVCAM", "PANCAM", "MINITES", "ENTRY"]
SEED_CAMERAS: dict[str, list[str]] = {
    "curiosity": ["FHAZ", "RHAZ", "MAST", "CHEMCAM", "MAHLI", "MARDI", "NAVCAM"],
    "opportunity": _MER,
    "spirit": _MER,
    "perseverance": [
        "EDL_RUCAM",
        "EDL_RDCAM",
        "EDL_DDCAM",
        "EDL_PUCAM1",
        "EDL_PUCAM2",
        "NAVCAM_LEFT",
        "NAVCAM_RIGHT",
        "MCZ_RIGHT",
        "MCZ_LEFT",
        "FRONT_HAZCAM_LEFT_A",
        "FRONT_HAZCAM_RIGHT_A",
        "REAR_HAZCAM_LEFT",
        "REAR_HAZCAM_RIGHT",
        "SKYCAM",
        "SHERLOC_WATSON",
        "SUPERCAM_RMI",
        "LCAM",
    ],
}

LANDING = {rover: date.fromisoformat(data["landing_date"]) for rover, data in ROVERS.items()}


@pytest.fixture(params=PREFIXES, ids=["api-v1", "mars-photos-api-v1"])
def prefix(request: pytest.FixtureRequest) -> str:
    """Every v1 endpoint is served under both prefixes."""
    return str(request.param)


@dataclass
class Camera:
    id: int
    code: str
    name: str


@dataclass
class World:
    """Seeded data plus what the contract says v1 must answer for it."""

    # (max_sol, max_date, total_photos) of full-frame photos per rover, from the contract.
    stats: dict[str, tuple[int | None, str | None, int]] = field(default_factory=dict)
    photo_ids: dict[str, int] = field(default_factory=dict)  # source_id -> photos.id
    records: dict[str, tuple[str, Any]] = field(default_factory=dict)  # source_id -> (rover, rec)
    cameras: dict[str, list[Camera]] = field(default_factory=dict)  # rover -> catalog by order

    def camera(self, rover_id: str, code: str) -> Camera:
        return next(cam for cam in self.cameras[rover_id] if cam.code == code)

    def rover(self, rover_id: str) -> dict[str, Any]:
        max_sol, max_date, total = self.stats.get(rover_id, (None, None, 0))
        return rover_json(rover_id, self.cameras[rover_id], max_sol, max_date, total)

    def photo(self, source_id: str) -> dict[str, Any]:
        rover_id, record = self.records[source_id]
        cam = self.camera(rover_id, record.camera_code.upper())
        if rover_id == "perseverance":
            img_src = record.img_large or record.img_full
        else:
            img_src = record.img_full
        return {
            "id": self.photo_ids[source_id],
            "sol": record.sol,
            "camera": {
                "id": cam.id,
                "name": cam.code,
                "rover_id": ROVERS[rover_id]["id"],
                "full_name": cam.name,
            },
            "img_src": img_src,
            "earth_date": legacy_earth_date(LANDING[rover_id], record.sol).isoformat(),
            "rover": self.rover(rover_id),
        }

    def photos(self, source_ids: Sequence[str]) -> list[dict[str, Any]]:
        return [self.photo(source_id) for source_id in source_ids]


def rover_json(
    rover_id: str,
    cameras: Sequence[Camera],
    max_sol: int | None,
    max_date: str | None,
    total_photos: int,
) -> dict[str, Any]:
    return {
        **ROVERS[rover_id],
        "max_sol": max_sol,
        "max_date": max_date,
        "total_photos": total_photos,
        "cameras": [{"name": cam.code, "full_name": cam.name} for cam in cameras],
    }


async def seed(
    factory: Factory, rover_id: str, records: Sequence[Any], *, one_by_one: bool = False
) -> None:
    """Commit photos the way the sync does; `one_by_one` makes photos.id follow list order."""
    async with factory() as session:
        if one_by_one:
            for record in records:
                await upsert_photos(session, rover_id, [record])
        else:
            await upsert_photos(session, rover_id, records)
        await recompute_sol_summaries(session, rover_id, {record.sol for record in records})
        await refresh_rover_stats(session, rover_id)
        await session.commit()


async def load_world(
    factory: Factory,
    records: dict[str, Sequence[Any]],
    stats: dict[str, tuple[int | None, str | None, int]],
) -> World:
    world = World(stats=stats)
    for rover_id, rover_records in records.items():
        for record in rover_records:
            world.records[record.source_id] = (rover_id, record)
    async with factory() as session:
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        world.photo_ids = {source_id: photo_id for source_id, photo_id in rows}
        rows = await session.execute(
            text("SELECT rover_id, id, code, name FROM cameras ORDER BY rover_id, sort_order")
        )
        for rover_id, camera_id, code, name in rows:
            world.cameras.setdefault(rover_id, []).append(Camera(camera_id, code, name))
    return world


# Curiosity, sol 1: full photos inserted one by one in this order, so photos.id grows along
# the list while the camera order differs (seed cameras first, new codes appended in order
# of first appearance by get_or_create_cameras).
CURIOSITY_SOL1 = [
    ("c1-nav-rb", "NAV_RIGHT_B"),
    ("c1-mardi", "MARDI"),
    ("c1-mast-r", "MAST_RIGHT"),
    ("c1-fhaz-lb", "FHAZ_LEFT_B"),
    ("c1-mahli", "MAHLI"),
    ("c1-fhaz", "FHAZ"),
    ("c1-mast-l", "MAST_LEFT"),
    ("c1-chem", "CHEMCAM_RMI"),
    ("c1-nav-lb", "NAV_LEFT_B"),
    ("c1-rhaz-lb", "RHAZ_LEFT_B"),
    ("c1-fhaz-rb", "FHAZ_RIGHT_B"),
]
# New Curiosity camera codes in the order they are appended to the catalog.
CURIOSITY_NEW_CODES = [
    "NAV_RIGHT_B",
    "MAST_RIGHT",
    "FHAZ_LEFT_B",
    "MAST_LEFT",
    "CHEMCAM_RMI",
    "NAV_LEFT_B",
    "RHAZ_LEFT_B",
    "FHAZ_RIGHT_B",
]
# Expected v1 order of sol 1: cameras.sort_order, then photos.id.
CURIOSITY_SOL1_ORDER = [
    "c1-fhaz",  # FHAZ (1)
    "c1-mahli",  # MAHLI (5)
    "c1-mardi",  # MARDI (6)
    "c1-nav-rb",  # NAV_RIGHT_B (8)
    "c1-mast-r",  # MAST_RIGHT (9)
    "c1-fhaz-lb",  # FHAZ_LEFT_B (10)
    "c1-mast-l",  # MAST_LEFT (11)
    "c1-chem",  # CHEMCAM_RMI (12)
    "c1-nav-lb",  # NAV_LEFT_B (13)
    "c1-rhaz-lb",  # RHAZ_LEFT_B (14)
    "c1-fhaz-rb",  # FHAZ_RIGHT_B (15)
]
CURIOSITY_SOL1_FULL_CODES = sorted(code for _, code in CURIOSITY_SOL1)


def curiosity_records(make_record: MakeRecord) -> list[Any]:
    """Full photos on sols 1, 30, 100, 829 (the legacy date vectors); sol 900 has none.

    Every hidden photo (subframe/downsampled/thumbnail) would change a counter, the max sol
    or a list if it leaked into v1.
    """
    sol1 = [
        make_record(
            source_id=source_id,
            sol=1,
            camera_code=code,
            # Curiosity always serves img_full, even when a large rendition exists.
            img_large=(
                f"https://images.example.test/{source_id}_large.jpg"
                if source_id == "c1-mahli"
                else None
            ),
        )
        for source_id, code in CURIOSITY_SOL1
    ]
    hidden_and_later = [
        make_record(source_id="c1-fhaz-lb-sub", sol=1, camera_code="FHAZ_LEFT_B",
                    sample_type="subframe"),
        make_record(source_id="c1-mast-l-down", sol=1, camera_code="MAST_LEFT",
                    sample_type="downsampled"),
        make_record(source_id="c30-nav-a", sol=30, camera_code="NAV_LEFT_B"),
        make_record(source_id="c30-nav-b", sol=30, camera_code="NAV_LEFT_B"),
        make_record(source_id="c100-mahli", sol=100, camera_code="MAHLI"),
        make_record(source_id="c829-mast-l", sol=829, camera_code="MAST_LEFT"),
        make_record(source_id="c829-chem-sub", sol=829, camera_code="CHEMCAM_RMI",
                    sample_type="subframe"),
        make_record(source_id="c900-nav-sub", sol=900, camera_code="NAV_LEFT_B",
                    sample_type="subframe"),
        make_record(source_id="c900-nav-thumb", sol=900, camera_code="NAV_LEFT_B",
                    sample_type="thumbnail"),
    ]  # fmt: skip
    return sol1 + hidden_and_later


def perseverance_records(make_record: MakeRecord) -> list[Any]:
    return [
        make_record(source_id="p-left", sol=100, camera_code="NAVCAM_LEFT",
                    img_large="https://images.example.test/p-left_1200.jpg",
                    img_small="https://images.example.test/p-left_320.jpg"),
        make_record(source_id="p-right", sol=100, camera_code="NAVCAM_RIGHT", img_large=None),
        make_record(source_id="p-left-sub", sol=100, camera_code="NAVCAM_LEFT",
                    sample_type="subframe",
                    img_large="https://images.example.test/p-left-sub_1200.jpg"),
        make_record(source_id="p-thumb", sol=101, camera_code="NAVCAM_RIGHT",
                    sample_type="thumbnail"),
    ]  # fmt: skip


def spirit_records(make_record: MakeRecord) -> list[Any]:
    # FHAZ_LEFT_B is synthetic for Spirit: it proves Curiosity groups do not apply here.
    return [
        make_record(source_id="s-fhaz", sol=5, camera_code="FHAZ"),
        make_record(source_id="s-fhaz-lb", sol=5, camera_code="FHAZ_LEFT_B"),
    ]


# Full-frame stats per rover after `world`: (max_sol, max_date, total_photos).
WORLD_STATS: dict[str, tuple[int | None, str | None, int]] = {
    # 11 on sol 1 + 2 on sol 30 + 1 on sol 100 + 1 on sol 829; sol 900 has no full photos.
    "curiosity": (829, "2014-12-05", 15),
    # sol 100 -> 2021-02-18 + floor(102.749) days; sol 101 has only a thumbnail.
    "perseverance": (100, "2021-05-31", 2),
    # sol 5 -> 2004-01-04 + floor(5.137) days.
    "spirit": (5, "2004-01-09", 2),
}


@pytest.fixture
async def world(clean_db: Factory, make_record: MakeRecord) -> World:
    """Curiosity, Perseverance and Spirit data; Opportunity stays empty."""
    records = {
        "curiosity": curiosity_records(make_record),
        "perseverance": perseverance_records(make_record),
        "spirit": spirit_records(make_record),
    }
    for rover_id, rover_records in records.items():
        await seed(clean_db, rover_id, rover_records, one_by_one=True)
    return await load_world(clean_db, records, WORLD_STATS)


PAGED_SOL = 1000
PAGED_COUNT = 205


@pytest.fixture
async def paged_world(clean_db: Factory, make_record: MakeRecord) -> World:
    """Curiosity sol 1000: 205 full MAHLI photos (one camera, so v1 order = photos.id)
    plus one MAHLI subframe that must never be counted or listed."""
    records = [
        make_record(source_id=f"pg-{n:03d}", sol=PAGED_SOL, camera_code="MAHLI")
        for n in range(PAGED_COUNT)
    ]
    records.append(
        make_record(source_id="pg-sub", sol=PAGED_SOL, camera_code="MAHLI", sample_type="subframe")
    )
    await seed(clean_db, "curiosity", records)
    max_date = legacy_earth_date(LANDING["curiosity"], PAGED_SOL).isoformat()
    return await load_world(
        clean_db, {"curiosity": records}, {"curiosity": (PAGED_SOL, max_date, PAGED_COUNT)}
    )


def paged_ids(world: World) -> list[int]:
    """photos.id of the full photos of `paged_world`, in v1 order."""
    return sorted(
        photo_id
        for source_id, photo_id in world.photo_ids.items()
        if source_id.startswith("pg-") and source_id != "pg-sub"
    )
