"""GET /api/v2/photos/batch?ids=... (stage 18C; docs/API.md and the 18C spec).

Covers AC-10 ... AC-14, AC-16 (batch part) and AC-17. The SQL-level AC-15 is in
tests/integration/test_stats_batch_sql.py, AC-18 in tests/api/test_stats_batch_rate_limit.py.

Dataset (module scope, own data): two curiosity, two perseverance, one spirit and one
opportunity (both with `source_link`, so their `images.full` is our own conversion URL) and one
ingenuity photo; every rover has its own `credit`.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.api.v2._stats import seed_records
from tests.api.v2.conftest import (
    PHOTO_KEYS,
    V2,
    assert_error,
    assert_json,
    assert_validation_error,
)
from tests.conftest import _clean_database, build_record

pytestmark = pytest.mark.api

URL = f"{V2}/photos/batch"
SHORT = "public, max-age=300"
INT64_MAX = 2**63 - 1

CREDITS = {
    "curiosity": "NASA/JPL-Caltech/MSSS",
    "perseverance": "NASA/JPL-Caltech/ASU",
    "spirit": "NASA/JPL-Caltech/Cornell",
    "opportunity": "NASA/JPL-Caltech/Cornell/USGS",
    "ingenuity": "NASA/JPL-Caltech/Ingenuity-Team",
}
# source_id -> (rover, sol, camera)
PHOTOS = {
    "c-1": ("curiosity", 10, "FHAZ"),
    "c-2": ("curiosity", 11, "NAVCAM"),
    "p-1": ("perseverance", 50, "NAVCAM_LEFT"),
    "p-2": ("perseverance", 51, "MCZ_LEFT"),
    "s-1": ("spirit", 5, "PANCAM"),
    "o-1": ("opportunity", 6, "PANCAM"),
    "i-1": ("ingenuity", 7, "HELI_NAV"),
}


def _record(source_id: str) -> Any:
    rover, sol, camera = PHOTOS[source_id]
    taken_at = datetime(2021, 5, sol % 28 + 1, 10, 0, tzinfo=UTC)
    mer = rover in ("spirit", "opportunity")
    return build_record(
        source_id=source_id,
        sol=sol,
        camera_code=camera,
        taken_at=taken_at,
        earth_date=taken_at.date(),
        img_full=f"https://images.example.test/{source_id}.jpg",
        credit=CREDITS[rover],
        source_link=(f"https://pds.example.test/{source_id}.img" if mer else None),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def ids(migrated_db: str) -> AsyncIterator[dict[str, int]]:
    """source_id -> photos.id of the module dataset."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean_database(factory)
        await seed_records(factory, [(PHOTOS[s][0], _record(s)) for s in PHOTOS])
        async with factory() as session:
            rows = await session.execute(text("SELECT source_id, id FROM photos"))
            yield {source_id: photo_id for source_id, photo_id in rows}
    finally:
        try:
            await _clean_database(factory)
        finally:
            await engine.dispose()


async def batch(client: httpx.AsyncClient, raw: str) -> httpx.Response:
    return await client.get(URL, params={"ids": raw})


def join(*values: int | str) -> str:
    return ",".join(str(v) for v in values)


async def list_element(client: httpx.AsyncClient, rover: str, photo_id: int) -> dict[str, Any]:
    """The Photo object as the list endpoint renders it."""
    body = (await client.get(f"{V2}/photos", params={"rovers": rover, "per_page": "100"})).json()
    return next(item for item in body["items"] if item["id"] == photo_id)


# --- AC-10 -------------------------------------------------------------------------------------


async def test_ac10_items_come_in_request_order_with_the_list_format(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    order = ["c-2", "c-1", "p-1"]  # neither ascending nor descending by id
    response = await batch(client, join(*(ids[s] for s in order)))

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items", "missing"]
    assert body["missing"] == []
    assert [item["id"] for item in body["items"]] == [ids[s] for s in order]
    for item in body["items"]:
        assert list(item) == PHOTO_KEYS
    for source_id in order:
        rover = PHOTOS[source_id][0]
        item = next(i for i in body["items"] if i["id"] == ids[source_id])
        assert_json(item, await list_element(client, rover, ids[source_id]))
        assert item["rover"] == rover and item["source"]["id"] == source_id


async def test_ac10_item_has_exact_photo_fields(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, str(ids["p-2"]))

    [item] = response.json()["items"]
    assert item["id"] == ids["p-2"]
    assert item["rover"] == "perseverance"
    assert item["sol"] == 51
    assert item["camera"]["code"] == "MCZ_LEFT"
    assert item["sample_type"] == "full"
    assert item["img_src"] == "https://images.example.test/p-2.jpg"
    assert item["images"]["full"] == "https://images.example.test/p-2.jpg"
    assert item["credit"] == CREDITS["perseverance"]
    assert item["source"] == {"id": "p-2", "link": None}
    assert "in_sol" not in item and "extra" not in item  # a list Photo, not PhotoDetail


@pytest.mark.parametrize("source_id", ["s-1", "o-1"])
async def test_ac10_mer_full_image_is_an_absolute_link_to_the_current_host(
    client: httpx.AsyncClient, ids: dict[str, int], source_id: str
) -> None:
    photo_id = ids[source_id]
    response = await batch(client, str(photo_id))

    [item] = response.json()["items"]
    expected = f"http://test{V2}/photos/{photo_id}/image?size=full"
    assert item["images"]["full"] == expected
    assert item["img_src"] == expected
    assert item["images"]["small"] == f"https://images.example.test/{source_id}.jpg"
    assert item["images"]["medium"] is None and item["images"]["large"] is None


# --- AC-11 -------------------------------------------------------------------------------------


async def test_ac11_photos_of_different_rovers_keep_their_rover_and_credit(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    order = ["i-1", "o-1", "p-1", "s-1", "c-1"]
    response = await batch(client, join(*(ids[s] for s in order)))

    items = response.json()["items"]
    assert [(i["id"], i["rover"], i["credit"]) for i in items] == [
        (ids["i-1"], "ingenuity", CREDITS["ingenuity"]),
        (ids["o-1"], "opportunity", CREDITS["opportunity"]),
        (ids["p-1"], "perseverance", CREDITS["perseverance"]),
        (ids["s-1"], "spirit", CREDITS["spirit"]),
        (ids["c-1"], "curiosity", CREDITS["curiosity"]),
    ]
    assert response.json()["missing"] == []


# --- AC-12 -------------------------------------------------------------------------------------


async def test_ac12_repeated_ids_collapse_in_order_of_first_appearance(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    a, b = ids["c-1"], ids["p-1"]
    response = await batch(client, join(a, b, a, a))

    body = response.json()
    assert [item["id"] for item in body["items"]] == [a, b]
    assert body["missing"] == []


async def test_ac12_repeated_missing_id_appears_once_in_missing(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    body = (await batch(client, join(999999999, ids["c-1"], 999999999))).json()

    assert body["missing"] == [999999999]
    assert [item["id"] for item in body["items"]] == [ids["c-1"]]


# --- AC-13 -------------------------------------------------------------------------------------


async def test_ac13_unknown_and_out_of_range_ids_go_to_missing_in_request_order(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    raw = join(ids["c-1"], 999999999, 0, 2**63)
    response = await batch(client, raw)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [ids["c-1"]]
    assert body["missing"] == [999999999, 0, 9223372036854775808]
    assert all(type(value) is int for value in body["missing"])


@pytest.mark.parametrize(
    "value",
    [0, 999999999, INT64_MAX, 2**63, 9999999999999999999, 10**18],
    ids=["zero", "unknown", "int64-max", "2^63", "19-nines", "10^18"],
)
async def test_ac13_when_every_id_is_unknown_items_is_empty_and_status_is_200(
    client: httpx.AsyncClient, ids: dict[str, int], value: int
) -> None:
    response = await batch(client, str(value))

    assert response.status_code == 200
    assert response.json() == {"items": [], "missing": [value]}


async def test_ac13_missing_ids_do_not_hide_the_found_ones_at_the_edges(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    body = (await batch(client, join(5555555, ids["s-1"], 6666666))).json()

    assert [item["id"] for item in body["items"]] == [ids["s-1"]]
    assert body["missing"] == [5555555, 6666666]


# --- AC-14 -------------------------------------------------------------------------------------


def _many(count: int, start: int = 10**18) -> str:
    return join(*(start + n for n in range(count)))


BAD_IDS = [
    pytest.param("", id="empty"),
    pytest.param("1,,2", id="empty-element"),
    pytest.param(",1", id="leading-comma"),
    pytest.param("1,", id="trailing-comma"),
    pytest.param(",", id="only-comma"),
    pytest.param("1,a", id="letters"),
    pytest.param("-1", id="negative"),
    pytest.param("1.5", id="decimal"),
    pytest.param("1, 2", id="space-after-comma"),
    pytest.param(" 1", id="leading-space"),
    pytest.param("+1", id="plus-sign"),
    pytest.param("0x10", id="hex"),
    pytest.param("1e3", id="exponent"),
    pytest.param("12345678901234567890", id="20-digits"),
    pytest.param("1," + "9" * 20, id="20-digits-second"),
    pytest.param(_many(101, start=1), id="101-ids"),
    pytest.param(",".join(["1"] * 101), id="101-repeats"),
    pytest.param(",".join(["1"] * 1000) + ",1", id="2001-chars"),
    pytest.param("1" * 2001, id="2001-digits"),
    # str.isdigit()/int() accept non-ASCII digits; the contract allows ASCII 0-9 only.
    pytest.param("١٢", id="arabic-indic-digits"),
    pytest.param("１２", id="full-width-digits"),
    pytest.param("1,١٢", id="arabic-indic-digits-second"),
]
# Note: the 2000-char limit is an early cut that cannot be hit on its own: the longest valid
# string is 100 ids of 19 digits joined by commas = 1999 chars. A string over 2000 chars always
# also breaks another rule (101+ ids or a 20+ digit id), so the "2001-*" cases above cover the
# limit only together with those rules.


@pytest.mark.parametrize("raw", BAD_IDS)
async def test_ac14_invalid_ids_get_422_validation_error(
    client: httpx.AsyncClient, ids: dict[str, int], raw: str
) -> None:
    response = await batch(client, raw)

    assert_validation_error(response, "ids")
    assert response.headers["cache-control"] == "no-store"


async def test_ac14_missing_ids_parameter_gets_422(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(URL)

    assert_validation_error(response, "ids")


async def test_ac14_repeated_ids_parameter_gets_422(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(URL, params=[("ids", str(ids["c-1"])), ("ids", str(ids["c-2"]))])

    assert_validation_error(response, "ids")


async def test_ac14_exactly_100_ids_of_19_digits_are_accepted(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    raw = _many(100)  # 100 * 19 digits + 99 commas = 1999 chars
    assert len(raw) == 1999

    response = await batch(client, raw)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["missing"] == [10**18 + n for n in range(100)]


async def test_ac14_100_elements_with_repeats_are_accepted_and_collapse(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, ",".join([str(ids["c-1"])] * 100))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [ids["c-1"]]


async def test_ac14_nineteen_digit_id_within_int64_is_missing_not_an_error(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, str(INT64_MAX))

    assert response.status_code == 200
    assert response.json()["missing"] == [INT64_MAX]


# --- AC-16 (batch) -----------------------------------------------------------------------------


async def test_ac16_batch_is_cached_five_minutes_not_an_hour(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, join(ids["c-1"], 999999999))

    assert response.status_code == 200
    assert response.headers["cache-control"] == SHORT
    assert response.headers["etag"]


async def test_ac16_batch_etag_differs_between_different_lists(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    first = await batch(client, str(ids["c-1"]))
    again = await batch(client, str(ids["c-1"]))
    other = await batch(client, str(ids["c-2"]))

    assert first.headers["etag"] == again.headers["etag"] != other.headers["etag"]


async def test_ac16_batch_matching_if_none_match_gets_304_without_a_body(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    raw = join(ids["c-1"], ids["p-1"])
    first = await batch(client, raw)
    response = await client.get(
        URL, params={"ids": raw}, headers={"If-None-Match": first.headers["etag"]}
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["cache-control"] == SHORT


async def test_ac16_batch_head_has_the_get_headers_and_no_body(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    raw = str(ids["c-1"])
    got = await batch(client, raw)
    head = await client.head(URL, params={"ids": raw})

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == SHORT
    assert head.headers["etag"] == got.headers["etag"]


async def test_ac16_batch_error_is_no_store_and_has_no_etag(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, "1,,2")

    assert_error(response, 422, "validation_error")
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


# --- AC-17 -------------------------------------------------------------------------------------


async def test_ac17_post_batch_is_method_not_allowed(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    for kwargs in ({"json": {"ids": [ids["c-1"]]}}, {"params": {"ids": str(ids["c-1"])}}):
        response = await client.post(URL, **kwargs)

        assert response.status_code == 405, response.text
        assert response.json()["error"]["code"] == "method_not_allowed"


async def test_ac17_batch_is_not_captured_by_the_photo_id_route(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await batch(client, str(ids["c-1"]))

    assert response.status_code == 200
    assert list(response.json()) == ["items", "missing"]
    assert response.headers["cache-control"] != "public, max-age=3600"


async def test_ac17_batch_without_ids_is_a_validation_error_not_photo_not_found(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(URL)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert [d["field"] for d in response.json()["error"]["details"]] == ["ids"]


async def test_ac17_photo_detail_route_still_serves_numeric_ids(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids['c-1']}")

    assert response.status_code == 200
    assert response.json()["id"] == ids["c-1"]
    assert response.headers["cache-control"] == "public, max-age=3600"
