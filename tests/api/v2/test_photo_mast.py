"""Stage 19, AC-4..AC-7: `mast` of Photo in v2 lists and PhotoDetail; v1 stays unchanged.

`mast` is `{"azimuth", "elevation"}` (degrees, azimuth in [0, 360), both rounded to 0.01) or
null; in Photo it follows `local_time` (before `near_stop`), in PhotoDetail it follows
`local_time` and precedes `in_sol`. Dataset: `mast_world` (tests/api/v2/conftest.py).
"""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import (
    MAST_KEYS,
    MAST_ROVERS,
    MAST_ROWS,
    PHOTO_DETAIL_KEYS,
    PHOTO_KEYS,
    V2,
    MastWorld,
)

pytestmark = pytest.mark.api


def check_items(items: list[dict[str, Any]], world: MastWorld, want_ids: set[int]) -> None:
    assert {i["id"] for i in items} == want_ids
    for item in items:
        source_id = world.source_of(item["id"])
        assert list(item) == PHOTO_KEYS, source_id
        keys = list(item)
        assert keys[keys.index("local_time") + 1] == "mast"
        assert item["mast"] == MAST_ROWS[source_id][5], source_id
        if item["mast"] is not None:
            assert list(item["mast"]) == MAST_KEYS
            assert all(type(v) is float or type(v) is int for v in item["mast"].values())


# --- AC-4 / AC-5 / AC-6: lists ---------------------------------------------------------------


@pytest.mark.parametrize("rover", MAST_ROVERS)
async def test_ac4_ac6_rover_photos_items_carry_the_exact_mast_or_null(
    client: httpx.AsyncClient, mast_world: MastWorld, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"per_page": 100})

    assert response.status_code == 200
    check_items(
        response.json()["items"], mast_world, {mast_world.ids[s] for s in mast_world.rows_of(rover)}
    )


async def test_ac4_curiosity_sol_5000_example_values(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    response = await client.get(f"{V2}/rovers/curiosity/photos", params={"sol": 5000})

    by_source = {i["source"]["id"]: i for i in response.json()["items"]}
    assert by_source["c-mast"]["mast"] == {"azimuth": 328.44, "elevation": -44.41}
    assert by_source["c-none"]["mast"] is None
    assert "mast" in by_source["c-none"]
    assert by_source["c-only-az"]["mast"] is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
async def test_ac5_photos_list_items_carry_the_same_mast(
    client: httpx.AsyncClient, mast_world: MastWorld, rover: str
) -> None:
    response = await client.get(f"{V2}/photos", params={"rovers": rover, "per_page": 100})

    assert response.status_code == 200
    check_items(
        response.json()["items"], mast_world, {mast_world.ids[s] for s in mast_world.rows_of(rover)}
    )


@pytest.mark.parametrize(
    ("rover", "latest_sol_rows"),
    [
        ("curiosity", ["c-mast", "c-none", "c-only-az"]),
        ("perseverance", ["p-late", "p-bad"]),
    ],
)
async def test_ac5_latest_items_carry_the_same_mast(
    client: httpx.AsyncClient, mast_world: MastWorld, rover: str, latest_sol_rows: list[str]
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos/latest", params={"per_page": 100})

    assert response.status_code == 200
    check_items(response.json()["items"], mast_world, {mast_world.ids[s] for s in latest_sol_rows})


async def test_ac5_batch_items_carry_the_same_mast(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    ids = ",".join(str(i) for i in mast_world.ids.values())

    response = await client.get(f"{V2}/photos/batch", params={"ids": ids})

    assert response.status_code == 200
    body = response.json()
    assert body["missing"] == []
    check_items(body["items"], mast_world, set(mast_world.ids.values()))


# --- AC-5 / AC-6: PhotoDetail ----------------------------------------------------------------


@pytest.mark.parametrize("source_id", list(MAST_ROWS))
async def test_ac5_ac6_photo_detail_has_mast_after_local_time_before_in_sol(
    client: httpx.AsyncClient, mast_world: MastWorld, source_id: str
) -> None:
    response = await client.get(f"{V2}/photos/{mast_world.ids[source_id]}")

    assert response.status_code == 200
    body = response.json()
    keys = list(body)
    assert keys == PHOTO_DETAIL_KEYS
    assert keys.index("local_time") + 1 == keys.index("mast") == keys.index("in_sol") - 1
    assert body["mast"] == MAST_ROWS[source_id][5]
    if body["mast"] is not None:
        assert list(body["mast"]) == MAST_KEYS


@pytest.mark.parametrize("source_id", ["c-mast", "p-neg", "p-exp", "p-late"])
async def test_ac5_photo_detail_extra_keeps_the_raw_source_strings(
    client: httpx.AsyncClient, mast_world: MastWorld, source_id: str
) -> None:
    body = (await client.get(f"{V2}/photos/{mast_world.ids[source_id]}")).json()

    assert body["extra"] == MAST_ROWS[source_id][4]


@pytest.mark.parametrize("source_id", ["c-mast", "p-neg", "p-360", "p-late", "c-none", "s-keys"])
async def test_ac5_detail_and_list_agree_on_mast(
    client: httpx.AsyncClient, mast_world: MastWorld, source_id: str
) -> None:
    rover = MAST_ROWS[source_id][0]
    photo_id = mast_world.ids[source_id]
    listed = (await client.get(f"{V2}/rovers/{rover}/photos", params={"per_page": 100})).json()
    item = next(i for i in listed["items"] if i["id"] == photo_id)

    detail = (await client.get(f"{V2}/photos/{photo_id}")).json()

    assert detail["mast"] == item["mast"]


# --- AC-7: v1 unchanged ----------------------------------------------------------------------

V1_PHOTO_KEYS = ["id", "sol", "camera", "img_src", "earth_date", "rover"]
PREFIXES = ["/api/v1", "/mars-photos/api/v1"]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize(
    ("rover", "sol"), [("curiosity", 5000), ("perseverance", 600), ("spirit", 3)]
)
async def test_ac7_v1_photos_have_no_mast_key(
    client: httpx.AsyncClient, mast_world: MastWorld, prefix: str, rover: str, sol: int
) -> None:
    response = await client.get(f"{prefix}/rovers/{rover}/photos", params={"sol": sol})

    assert response.status_code == 200
    photos = response.json()["photos"]
    assert photos
    for photo in photos:
        assert list(photo) == V1_PHOTO_KEYS
        assert "mast" not in photo


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_ac7_v1_latest_photos_and_detail_have_no_mast_key(
    client: httpx.AsyncClient, mast_world: MastWorld, prefix: str
) -> None:
    latest = await client.get(f"{prefix}/rovers/curiosity/latest_photos")
    detail = await client.get(f"{prefix}/photos/{mast_world.ids['c-mast']}")

    assert latest.status_code == 200 and detail.status_code == 200
    for photo in [*latest.json()["latest_photos"], detail.json()["photo"]]:
        assert list(photo) == V1_PHOTO_KEYS
    assert '"mast":' not in latest.text and '"mast":' not in detail.text
