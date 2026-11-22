"""`local_time` of Photo in v2 lists and PhotoDetail (docs/API.md, "Объекты").

`{"time": "HH:MM:SS", "kind": "lmst"|"ltst", "planet": "mars"}` or null; the last key of Photo
(in PhotoDetail it keeps its place after `location`). v1 never shows it.
Dataset: `meta_world` (tests/api/v2/_meta.py): known stored local times and "no time" rows.
"""

from typing import Any

import httpx
import pytest

from tests.api.v2._meta import ALL_ROVERS, LMST_ROVERS, MetaWorld, MRow
from tests.api.v2.conftest import PHOTO_DETAIL_KEYS, PHOTO_KEYS, V2

pytestmark = pytest.mark.api


def expected(row: MRow) -> dict[str, str] | None:
    if row.local_time is None:
        return None
    return {
        "time": row.local_time,
        "kind": "lmst" if row.rover in LMST_ROVERS else "ltst",
        "planet": "mars",
    }


def check_items(items: list[dict[str, Any]], world: MetaWorld) -> None:
    by_id = {r.id: r for r in world.rows}
    assert items
    for item in items:
        assert list(item) == PHOTO_KEYS
        assert list(item)[-2:] == ["local_time", "mast"]
        assert item["local_time"] == expected(by_id[item["id"]]), item["id"]


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_local_time_rover_photos_items_have_the_exact_object(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"per_page": 100})

    assert response.status_code == 200
    check_items(response.json()["items"], meta_world)


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_local_time_photos_list_items_have_the_exact_object(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    response = await client.get(f"{V2}/photos", params={"rovers": rover, "per_page": 100})

    assert response.status_code == 200
    check_items(response.json()["items"], meta_world)


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "opportunity"])
async def test_local_time_latest_items_have_the_exact_object(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos/latest", params={"per_page": 100})

    assert response.status_code == 200
    check_items(response.json()["items"], meta_world)


async def test_local_time_batch_items_have_the_exact_object(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    picked = [r for r in meta_world.rows if r.sol == 100 and r.source_id.endswith(("-0", "-13"))]
    ids = ",".join(str(r.id) for r in picked)

    response = await client.get(f"{V2}/photos/batch", params={"ids": ids})

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == len(picked) == 10
    check_items(items, meta_world)


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_local_time_unknown_source_time_is_null(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    row = next(r for r in meta_world.rows if r.rover == rover and r.local_time is None)

    item = (await client.get(f"{V2}/photos/batch", params={"ids": str(row.id)})).json()["items"][0]

    assert "local_time" in item
    assert item["local_time"] is None


@pytest.mark.parametrize(
    ("rover", "kind"),
    [
        pytest.param("curiosity", "lmst", id="curiosity"),
        pytest.param("perseverance", "lmst", id="perseverance"),
        pytest.param("ingenuity", "lmst", id="ingenuity"),
        pytest.param("spirit", "ltst", id="spirit"),
        pytest.param("opportunity", "ltst", id="opportunity"),
    ],
)
async def test_local_time_kind_depends_on_the_rover_and_planet_is_mars(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str, kind: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"per_page": 100})

    values = [i["local_time"] for i in response.json()["items"] if i["local_time"] is not None]
    assert values
    assert {v["kind"] for v in values} == {kind}
    assert {v["planet"] for v in values} == {"mars"}
    assert all(list(v) == ["time", "kind", "planet"] for v in values)


@pytest.mark.parametrize("rover", ["curiosity", "opportunity"])
async def test_local_time_list_value_equals_photo_detail_value(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    rows = [r for r in meta_world.rows if r.rover == rover and r.sol == 100]
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"sol": 100, "per_page": 100})
    listed = {i["id"]: i["local_time"] for i in response.json()["items"]}
    assert len(listed) == len(rows)

    for row in rows:
        detail = (await client.get(f"{V2}/photos/{row.id}")).json()
        assert detail["local_time"] == listed[row.id] == expected(row), row.source_id


async def test_local_time_photo_detail_key_order_is_unchanged_apart_from_planet(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    row = next(r for r in meta_world.rows if r.rover == "perseverance" and r.local_time)

    body = (await client.get(f"{V2}/photos/{row.id}")).json()

    keys = list(body)
    assert keys == PHOTO_DETAIL_KEYS
    assert keys.index("location") + 1 == keys.index("local_time")
    assert keys.index("local_time") + 1 == keys.index("mast")  # stage 19
    assert keys.index("mast") + 1 == keys.index("in_sol")
    assert list(body["local_time"]) == ["time", "kind", "planet"]


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "spirit"])
async def test_v1_photos_have_no_local_time_or_near_stop_keys(
    client: httpx.AsyncClient, meta_world: MetaWorld, prefix: str, rover: str
) -> None:
    response = await client.get(f"{prefix}/rovers/{rover}/photos", params={"sol": 100})

    assert response.status_code == 200
    photos = response.json()["photos"]
    assert photos
    for photo in photos:
        assert not {"local_time", "near_stop", "planet"} & set(photo)


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_latest_photos_and_photo_by_id_have_no_local_time_key(
    client: httpx.AsyncClient, meta_world: MetaWorld, prefix: str
) -> None:
    latest = await client.get(f"{prefix}/rovers/curiosity/latest_photos")
    assert latest.status_code == 200
    assert latest.json()["latest_photos"]
    for photo in latest.json()["latest_photos"]:
        assert not {"local_time", "near_stop"} & set(photo)

    row = next(r for r in meta_world.rows if r.rover == "curiosity")
    one = await client.get(f"{prefix}/photos/{row.id}")
    assert one.status_code == 200
    assert not {"local_time", "near_stop"} & set(one.json()["photo"])
