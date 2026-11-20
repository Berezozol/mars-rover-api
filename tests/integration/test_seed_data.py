"""Seed data from migration 0002: rovers and the camera dictionary in the original order."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ROVERS = [
    # id, legacy_id, name, mission, launch_date, landing_date, status
    ("curiosity", 5, "Curiosity", "msl", date(2011, 11, 26), date(2012, 8, 6), "active"),
    ("opportunity", 6, "Opportunity", "mer", date(2003, 7, 7), date(2004, 1, 25), "complete"),
    ("spirit", 7, "Spirit", "mer", date(2003, 6, 10), date(2004, 1, 4), "complete"),
    ("perseverance", 8, "Perseverance", "mars2020", date(2020, 7, 30), date(2021, 2, 18), "active"),
    ("ingenuity", 9, "Ingenuity", "mars2020", date(2020, 7, 30), date(2021, 2, 18), "complete"),
]

MER_CAMERAS = ["FHAZ", "RHAZ", "NAVCAM", "PANCAM", "MINITES", "ENTRY"]

CAMERAS = {
    "perseverance": [
        "EDL_RUCAM", "EDL_RDCAM", "EDL_DDCAM", "EDL_PUCAM1", "EDL_PUCAM2",
        "NAVCAM_LEFT", "NAVCAM_RIGHT", "MCZ_RIGHT", "MCZ_LEFT",
        "FRONT_HAZCAM_LEFT_A", "FRONT_HAZCAM_RIGHT_A", "REAR_HAZCAM_LEFT", "REAR_HAZCAM_RIGHT",
        "SKYCAM", "SHERLOC_WATSON", "SUPERCAM_RMI", "LCAM",
    ],
    "curiosity": ["FHAZ", "RHAZ", "MAST", "CHEMCAM", "MAHLI", "MARDI", "NAVCAM"],
    "opportunity": MER_CAMERAS,
    "spirit": MER_CAMERAS,
    "ingenuity": ["HELI_NAV", "HELI_RTE"],  # stage 12
}  # fmt: skip

# Names pinned by the contract for the legacy codes.
LEGACY_CAMERA_NAMES = {
    "FHAZ": "Front Hazard Avoidance Camera",
    "RHAZ": "Rear Hazard Avoidance Camera",
    "NAVCAM": "Navigation Camera",
    "MAST": "Mast Camera",
    "CHEMCAM": "Chemistry and Camera Complex",
    "MAHLI": "Mars Hand Lens Imager",
    "MARDI": "Mars Descent Imager",
    "PANCAM": "Panoramic Camera",
    "MINITES": "Miniature Thermal Emission Spectrometer (Mini-TES)",
    "ENTRY": "Entry, Descent, and Landing Camera",
}


async def test_seed_rovers_match_legacy_table(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT id, legacy_id, name, mission, launch_date, landing_date, status "
            "FROM rovers ORDER BY legacy_id"
        )
    )
    assert [tuple(row) for row in result] == ROVERS


async def test_seed_rovers_have_empty_counters(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT id, total_photos, full_total_photos, max_sol, full_max_sol FROM rovers")
    )
    for row in result:
        assert (row.total_photos, row.full_total_photos) == (0, 0), row.id
        assert (row.max_sol, row.full_max_sol) == (None, None), row.id


@pytest.mark.parametrize("rover", list(CAMERAS), ids=list(CAMERAS))
async def test_seed_cameras_codes_in_original_order(db_session: AsyncSession, rover: str) -> None:
    result = await db_session.execute(
        text("SELECT code, sort_order FROM cameras WHERE rover_id = :rover ORDER BY sort_order"),
        {"rover": rover},
    )
    rows = result.all()
    assert [r.code for r in rows] == CAMERAS[rover]
    assert [r.sort_order for r in rows] == list(range(1, len(CAMERAS[rover]) + 1))


async def test_seed_cameras_names_non_empty(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT rover_id, code, name FROM cameras"))
    empty = [(r.rover_id, r.code) for r in result if not (r.name or "").strip()]
    assert empty == []


@pytest.mark.parametrize("rover", ["curiosity", "opportunity", "spirit"])
async def test_seed_cameras_legacy_full_names(db_session: AsyncSession, rover: str) -> None:
    result = await db_session.execute(
        text("SELECT code, name FROM cameras WHERE rover_id = :rover"), {"rover": rover}
    )
    names = dict(result.all())
    assert names == {code: LEGACY_CAMERA_NAMES[code] for code in CAMERAS[rover]}


async def test_seed_rhaz_full_name_is_rear_not_read(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT name FROM cameras WHERE code = 'RHAZ'"))
    names = list(result.scalars())
    assert len(names) == 3
    assert all(name.startswith("Rear") for name in names), names


async def test_seed_camera_codes_are_upper_case(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT code FROM cameras WHERE code <> upper(code)"))
    assert result.scalars().all() == []
