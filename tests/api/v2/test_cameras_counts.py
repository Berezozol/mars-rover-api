"""Stage 7: GET /api/v2/rovers/{rover}/cameras reads camera_sol_counts, not photos.

- totals are SUM(camera_sol_counts.total_photos) per camera (no GROUP BY over photos);
- they stay right after a resync that changes a sol (photo added, photo moved to another
  camera); cameras without rows show 0; the response format is unchanged.

Uses its own data via `clean_db` (never `v2_world`); data is written by the SyncService.
"""

import httpx
import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services.sync import SyncService
from tests.api.v2.conftest import CAMERA_KEYS, V2, Factory
from tests.integration._fakes import FakeAdapter, sol_records

pytestmark = pytest.mark.api

ROVER = "curiosity"


async def totals(client: httpx.AsyncClient) -> dict[str, int]:
    response = await client.get(f"{V2}/rovers/{ROVER}/cameras")
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    for camera in body["items"]:
        assert list(camera) == CAMERA_KEYS
    return {camera["code"]: camera["total_photos"] for camera in body["items"]}


def nonzero(values: dict[str, int]) -> dict[str, int]:
    return {code: n for code, n in values.items() if n}


@pytest.fixture
def service(clean_db: Factory, settings: Settings) -> SyncService:
    return SyncService(clean_db, settings)


async def test_cameras_totals_follow_resync_photo_added_and_camera_moved(
    client: httpx.AsyncClient, service: SyncService
) -> None:
    first = FakeAdapter(ROVER)
    first.add(sol_records(ROVER, 100, 2, camera_code="FHAZ"))  # curiosity-100-0, -1
    first.add(sol_records(ROVER, 100, 3, camera_code="NAVCAM")[2:])  # curiosity-100-2
    first.add(sol_records(ROVER, 101, 1, camera_code="NAVCAM", sample_type="subframe"))
    await service.run(first, from_sol=100, to_sol=101)
    before = await totals(client)
    assert nonzero(before) == {"FHAZ": 2, "NAVCAM": 2}
    assert before["MAST"] == 0  # a camera without rows

    second = FakeAdapter(ROVER)
    second.add(sol_records(ROVER, 100, 2, camera_code="FHAZ"))
    second.add(sol_records(ROVER, 100, 4, camera_code="FHAZ")[3:])  # new: curiosity-100-3
    second.add(sol_records(ROVER, 100, 3, camera_code="MAST")[2:])  # -2: NAVCAM -> MAST
    await service.run(second, from_sol=100, to_sol=100)

    after = await totals(client)
    assert nonzero(after) == {"FHAZ": 3, "MAST": 1, "NAVCAM": 1}  # sol 101 untouched
    assert set(after) == set(before)


async def test_cameras_totals_are_read_from_camera_sol_counts(
    client: httpx.AsyncClient, service: SyncService, clean_db: Factory
) -> None:
    await service.run(
        FakeAdapter(ROVER).add(sol_records(ROVER, 100, 2, camera_code="FHAZ")),
        from_sol=100,
        to_sol=100,
    )
    assert nonzero(await totals(client)) == {"FHAZ": 2}

    # Only the counter table changes: the endpoint must follow it, not photos.
    async with clean_db() as session:
        await session.execute(
            text(
                "UPDATE camera_sol_counts SET total_photos = 40 "
                "WHERE rover_id = :rover AND sol = 100"
            ),
            {"rover": ROVER},
        )
        await session.execute(
            text(
                "INSERT INTO camera_sol_counts (rover_id, sol, camera_id, total_photos, "
                "full_photos) SELECT :rover, 7, id, 2, 0 FROM cameras "
                "WHERE rover_id = :rover AND code = 'MAHLI'"
            ),
            {"rover": ROVER},
        )
        await session.commit()

    assert nonzero(await totals(client)) == {"FHAZ": 40, "MAHLI": 2}
