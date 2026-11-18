"""`total` of /api/v2/rovers/{rover}/photos with a camera filter (stage 9).

With only `camera` (optionally `sample_type=full`) the total may be read from
camera_sol_counts instead of count(*) over photos; on consistent data (what the sync keeps)
it must equal the old count. Every other combination keeps counting photos: inflating
camera_sol_counts must not change those totals.

Uses its own data via `clean_db` (never `v2_world`).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.api.v2.conftest import V2, Factory
from tests.conftest import build_record

pytestmark = pytest.mark.api

ROVER = "curiosity"
URL = f"{V2}/rovers/{ROVER}/photos"
# (sol, camera, sample_type, count)
LAYOUT = [
    (10, "FHAZ", "full", 3),
    (10, "FHAZ", "subframe", 2),
    (10, "NAVCAM", "full", 4),
    (11, "FHAZ", "full", 1),
    (11, "FHAZ", "downsampled", 2),
    (11, "FHAZ", "mixed", 1),
    (12, "NAVCAM", "subframe", 5),
    (13, "FHAZ", "chemcam prc", 1),
]
BASE = datetime(2012, 8, 16, tzinfo=UTC)


def _records() -> list[Any]:
    records = []
    for sol, camera, sample_type, count in LAYOUT:
        for n in range(count):
            taken_at = BASE + timedelta(days=sol - 10, minutes=n)
            records.append(
                build_record(
                    source_id=f"ct-{sol}-{camera}-{sample_type}-{n}",
                    sol=sol,
                    camera_code=camera,
                    sample_type=sample_type,
                    taken_at=taken_at,
                    earth_date=taken_at.date(),
                )
            )
    return records


def expected(camera: str, sample_type: str | None = None, sol: int | None = None) -> int:
    return sum(
        n
        for s, c, t, n in LAYOUT
        if c == camera and (sample_type is None or t == sample_type) and (sol is None or s == sol)
    )


@pytest.fixture
async def data(clean_db: Factory) -> Factory:
    records = _records()
    async with clean_db() as session:
        await upsert_photos(session, ROVER, records)
        await recompute_sol_summaries(session, ROVER, {r.sol for r in records})
        await refresh_rover_stats(session, ROVER)
        await session.commit()
    return clean_db


async def sql_count(factory: Factory, where: str, params: dict[str, Any]) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM photos p JOIN cameras c ON c.id = p.camera_id "
                    f"WHERE p.rover_id = '{ROVER}' AND {where}"
                ),
                params,
            )
        )


async def total(client: httpx.AsyncClient, **params: str) -> int:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return int(response.json()["total"])


@pytest.mark.parametrize("camera", ["FHAZ", "NAVCAM", "fhaz"], ids=["fhaz", "navcam", "lower"])
async def test_total_camera_only_equals_count_of_photos(
    client: httpx.AsyncClient, data: Factory, camera: str
) -> None:
    old = await sql_count(data, "c.code = :code", {"code": camera.upper()})

    assert await total(client, camera=camera) == old == expected(camera.upper())


@pytest.mark.parametrize("camera", ["FHAZ", "NAVCAM"])
async def test_total_camera_and_full_equals_count_of_full_photos(
    client: httpx.AsyncClient, data: Factory, camera: str
) -> None:
    old = await sql_count(data, "c.code = :code AND p.sample_type = 'full'", {"code": camera})

    assert await total(client, camera=camera, sample_type="full") == old == expected(camera, "full")


async def test_total_camera_without_photos_is_zero(
    client: httpx.AsyncClient, data: Factory
) -> None:
    response = await client.get(URL, params={"camera": "MAHLI"})

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


async def test_total_camera_matches_items_over_all_pages(
    client: httpx.AsyncClient, data: Factory
) -> None:
    n = expected("FHAZ")
    seen = []
    for page in range(1, 10):
        body = (
            await client.get(URL, params={"camera": "FHAZ", "per_page": "3", "page": str(page)})
        ).json()
        assert body["total"] == n
        if not body["items"]:
            break
        seen += [item["id"] for item in body["items"]]
    assert len(seen) == len(set(seen)) == n


@pytest.mark.parametrize(
    ("params", "camera", "sample_type", "sol"),
    [
        ({"camera": "FHAZ", "sample_type": "subframe"}, "FHAZ", "subframe", None),
        ({"camera": "FHAZ", "sample_type": "downsampled"}, "FHAZ", "downsampled", None),
        ({"camera": "FHAZ", "sol": "11"}, "FHAZ", None, 11),
        ({"camera": "NAVCAM", "sol": "10", "sample_type": "full"}, "NAVCAM", "full", 10),
        ({"camera": "FHAZ", "earth_date": "2012-08-17"}, "FHAZ", None, 11),
    ],
    ids=["subframe", "downsampled", "sol", "sol-full", "earth-date"],
)
async def test_total_other_combinations_still_count_photos(
    client: httpx.AsyncClient,
    data: Factory,
    params: dict[str, str],
    camera: str,
    sample_type: str | None,
    sol: int | None,
) -> None:
    before = await total(client, **params)
    # Inflate the counters: totals that do not come from them must not move.
    async with data() as session:
        await session.execute(
            text(
                "UPDATE camera_sol_counts SET total_photos = total_photos + 100, "
                "full_photos = full_photos + 100 WHERE rover_id = :rover"
            ),
            {"rover": ROVER},
        )
        await session.commit()

    after = await total(client, **params)

    assert before == after == expected(camera, sample_type, sol)


@pytest.mark.parametrize(
    "params",
    [{}, {"sol": "10"}, {"sample_type": "full"}, {"sample_type": "subframe"}],
    ids=["none", "sol", "full", "subframe"],
)
async def test_total_without_camera_unchanged(
    client: httpx.AsyncClient, data: Factory, params: dict[str, str]
) -> None:
    where = ["TRUE"]
    if "sol" in params:
        where.append(f"p.sol = {int(params['sol'])}")
    if "sample_type" in params:
        where.append("p.sample_type = :t")
    old = await sql_count(data, " AND ".join(where), {"t": params.get("sample_type")})

    assert await total(client, **params) == old


async def counter_sum(factory: Factory, camera: str, column: str) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                text(
                    f"SELECT coalesce(sum(s.{column}), 0) FROM camera_sol_counts s "
                    "JOIN cameras c ON c.id = s.camera_id "
                    "WHERE s.rover_id = :rover AND c.code = :code"
                ),
                {"rover": ROVER, "code": camera},
            )
        )


@pytest.mark.parametrize(
    ("params", "column", "sample_type"),
    [
        ({"camera": "FHAZ"}, "total_photos", None),
        ({"camera": "fhaz"}, "total_photos", None),
        ({"camera": "FHAZ", "sample_type": "full"}, "full_photos", "full"),
        ({"camera": "FHAZ", "sample_type": "FULL"}, "full_photos", "full"),
        ({"camera": "fhaz", "sample_type": "Full"}, "full_photos", "full"),
    ],
    ids=["camera", "camera-lower", "full", "FULL", "Full-lower-camera"],
)
async def test_total_camera_shortcut_reads_camera_sol_counts(
    client: httpx.AsyncClient,
    data: Factory,
    params: dict[str, str],
    column: str,
    sample_type: str | None,
) -> None:
    """Camera (+ full in any case) is served from camera_sol_counts: on consistent data it
    equals count(*); after inflating the counters the total follows them."""
    assert await total(client, **params) == expected("FHAZ", sample_type)

    async with data() as session:
        await session.execute(
            text(
                "UPDATE camera_sol_counts SET total_photos = total_photos + 100, "
                "full_photos = full_photos + 100 WHERE rover_id = :rover"
            ),
            {"rover": ROVER},
        )
        await session.commit()

    inflated = await counter_sum(data, "FHAZ", column)
    assert inflated > expected("FHAZ", sample_type)
    assert await total(client, **params) == inflated
