"""Stage 19, AC-13: `fields` with `near` and `near_stop` (own data: `clean_db`, not `mast_world`).

Kept apart from test_photos_fields.py: `clean_db` truncates the tables, which would wipe the
module-scoped `mast_world` of the other module's tests.
`near_stop` is output only with `near` and only when selected.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._locations import Factory, insert_waypoints, waypoint
from tests.api.v2.conftest import V2
from tests.conftest import build_record

pytestmark = pytest.mark.api

NEAR_STOP_KEYS = ["sol", "site", "drive", "lat", "lon", "distance_km"]


async def get(client: httpx.AsyncClient, path: str, params: Any) -> httpx.Response:
    return await client.get(f"{V2}{path}", params=params)


# --- AC-13 -----------------------------------------------------------------------------------

C = "curiosity"
STOPS = [waypoint(1000, 12, 10, -4.0, 137.0), waypoint(1001, 12, 20, -4.01, 137.0)]
NEAR = {"near": "-4.0,137.0", "radius_km": 5, "per_page": 100}


async def seed_near(factory: Factory) -> dict[str, int]:
    records = []
    for index, (source_id, (site, drive)) in enumerate(
        {"c-k0": (12, 10), "c-k1": (12, 20)}.items()
    ):
        taken = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
        records.append(
            build_record(
                source_id=source_id,
                sol=1001,
                camera_code="FHAZ",
                taken_at=taken,
                earth_date=taken.date(),
                site=site,
                drive=drive,
            )
        )
    async with factory() as session:
        await upsert_photos(session, C, records)
        await recompute_sol_summaries(session, C, {1001})
        await refresh_rover_stats(session, C)
        await session.commit()
    await insert_waypoints(factory, C, STOPS)
    async with factory() as session:
        return dict((await session.execute(text("SELECT source_id, id FROM photos"))).all())


NEAR_PATHS = pytest.mark.parametrize(
    ("path", "extra"),
    [("/photos", {"rovers": C}), (f"/rovers/{C}/photos", {})],
    ids=["photos", "rover-photos"],
)


@NEAR_PATHS
async def test_ac13_near_stop_is_dropped_unless_selected(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict[str, Any]
) -> None:
    await seed_near(clean_db)

    response = await get(client, path, {**extra, **NEAR, "fields": "sol"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert list(item) == ["id", "sol", "credit"]


@NEAR_PATHS
async def test_ac13_near_stop_is_kept_when_selected_with_near(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict[str, Any]
) -> None:
    await seed_near(clean_db)
    full = (await get(client, path, {**extra, **NEAR})).json()["items"]

    response = await get(client, path, {**extra, **NEAR, "fields": "sol,near_stop"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["id"] for i in items] == [i["id"] for i in full]
    for item, whole in zip(items, full, strict=True):
        assert list(item) == ["id", "sol", "credit", "near_stop"]
        assert list(item["near_stop"]) == NEAR_STOP_KEYS
        assert item["near_stop"] == whole["near_stop"]


@NEAR_PATHS
async def test_ac13_near_stop_without_near_is_accepted_and_adds_nothing(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict[str, Any]
) -> None:
    await seed_near(clean_db)

    response = await get(client, path, {**extra, "per_page": 100, "fields": "near_stop"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert list(item) == ["id", "credit"]


async def test_ac13_full_photo_with_near_ends_with_mast_then_near_stop(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_near(clean_db)

    items = (await get(client, "/photos", {"rovers": C, **NEAR})).json()["items"]

    assert items
    for item in items:
        assert list(item)[-2:] == ["mast", "near_stop"]
