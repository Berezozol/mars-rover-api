"""Stage 19, AC-8..AC-15: the `fields` parameter of the four v2 photo lists.

Lists: `/photos`, `/rovers/{rover}/photos`, `/rovers/{rover}/photos/latest`, `/photos/batch`.
`fields` is a comma-separated selection of top-level Photo keys; `id` and `credit` are always
present; the order is the order of Photo; repeats collapse; the wrapper (`page`, `per_page`,
`total`, `nearest_sols`, `sol`, `missing`) is unchanged; unknown, empty, upper-case, nested keys,
a value over 300 characters and a repeated parameter give `422 validation_error`.
Dataset: `mast_world` (tests/api/v2/conftest.py); AC-13 uses its own stops (`clean_db`).
AC-12 (422 before 503) lives in tests/api/test_overload_gate.py.
"""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import (
    LATEST_KEYS,
    LIST_KEYS,
    PHOTO_KEYS,
    V2,
    MastWorld,
    assert_validation_error,
)

pytestmark = pytest.mark.api

ALL_KEYS = [*PHOTO_KEYS, "near_stop"]  # the 17 selectable keys, in the order of Photo
NEAR_STOP_KEYS = ["sol", "site", "drive", "lat", "lon", "distance_km"]
SHORT = "public, max-age=300"

# name -> (path, base params, wrapper keys)
ENDPOINTS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "photos": (
        "/photos",
        {"rovers": "curiosity", "per_page": 100},
        ["items", "page", "per_page", "total"],
    ),
    "rover-photos": ("/rovers/curiosity/photos", {"per_page": 100}, LIST_KEYS),
    "latest": ("/rovers/curiosity/photos/latest", {"per_page": 100}, LATEST_KEYS),
}
LIST_ENDPOINTS = pytest.mark.parametrize(
    ("path", "base", "wrapper"), list(ENDPOINTS.values()), ids=list(ENDPOINTS)
)
ALL_ENDPOINT_PATHS = pytest.mark.parametrize(
    "path",
    [
        "/photos?rovers=curiosity",
        "/rovers/curiosity/photos",
        "/rovers/curiosity/photos/latest",
        "/photos/batch?ids=1",
    ],
    ids=["photos", "rover-photos", "latest", "batch"],
)


def ordered(keys: set[str] | list[str]) -> list[str]:
    """The keys in the order of Photo, `id` and `credit` always included."""
    wanted = set(keys) | {"id", "credit"}
    return [k for k in ALL_KEYS if k in wanted]


async def get(client: httpx.AsyncClient, path: str, params: Any) -> httpx.Response:
    return await client.get(f"{V2}{path}", params=params)


# --- AC-8 ------------------------------------------------------------------------------------


@LIST_ENDPOINTS
async def test_ac8_fields_keep_only_the_named_keys_plus_id_and_credit_in_photo_order(
    client: httpx.AsyncClient,
    mast_world: MastWorld,
    path: str,
    base: dict[str, Any],
    wrapper: list[str],
) -> None:
    full = (await get(client, path, base)).json()
    response = await get(client, path, {**base, "fields": "sol,img_src"})

    assert response.status_code == 200
    body = response.json()
    assert list(body) == wrapper
    assert len(body["items"]) == len(full["items"]) > 0
    for item, whole in zip(body["items"], full["items"], strict=True):
        assert list(item) == ["id", "sol", "img_src", "credit"]
        assert item == {k: whole[k] for k in item}
    for key in wrapper:
        if key != "items":
            assert body[key] == full[key], key


async def test_ac8_request_order_does_not_matter(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    base = {"rovers": "curiosity", "per_page": 100}

    direct = await get(client, "/photos", {**base, "fields": "sol,img_src"})
    reverse = await get(client, "/photos", {**base, "fields": "img_src,sol"})

    assert direct.json() == reverse.json()
    assert list(direct.json()["items"][0]) == ["id", "sol", "img_src", "credit"]


async def test_ac8_pagination_wrapper_is_unchanged(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    params = {"rovers": "perseverance", "per_page": 2, "page": 2}

    full = (await get(client, "/photos", params)).json()
    cut = (await get(client, "/photos", {**params, "fields": "id,sol"})).json()

    assert (cut["page"], cut["per_page"], cut["total"]) == (2, 2, full["total"]) == (2, 2, 5)
    assert [i["id"] for i in cut["items"]] == [i["id"] for i in full["items"]]


async def test_ac8_every_key_selects_exactly_itself(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    """All keys but `near_stop` (needs `near`), each alone: key + `id` + `credit` only."""
    base = {"rovers": "curiosity", "per_page": 100}
    full = (await get(client, "/photos", base)).json()["items"]

    for key in PHOTO_KEYS:
        body = (await get(client, "/photos", {**base, "fields": key})).json()
        for item, whole in zip(body["items"], full, strict=True):
            assert list(item) == ordered([key]), key
            assert item == {k: whole[k] for k in item}, key


async def test_ac8_all_keys_selected_equals_the_full_photo(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    base = {"rovers": "curiosity", "per_page": 100}

    full = (await get(client, "/photos", base)).json()
    every = (await get(client, "/photos", {**base, "fields": ",".join(ALL_KEYS)})).json()

    assert every == full


# --- AC-9 ------------------------------------------------------------------------------------


async def test_ac9_rover_photos_keeps_nearest_sols_in_place(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    params = {"sol": 4999}  # no photos on this sol: `nearest_sols` points at the neighbours

    full = (await get(client, "/rovers/curiosity/photos", params)).json()
    cut = (await get(client, "/rovers/curiosity/photos", {**params, "fields": "sol"})).json()

    assert list(cut) == LIST_KEYS
    assert cut["items"] == []
    assert cut["nearest_sols"] == full["nearest_sols"] is not None


async def test_ac9_latest_keeps_sol_at_the_top(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    body = (await get(client, "/rovers/curiosity/photos/latest", {"fields": "camera"})).json()

    assert list(body) == LATEST_KEYS
    assert body["sol"] == 5000
    for item in body["items"]:
        assert list(item) == ["id", "camera", "credit"]
        assert list(item["camera"]) == ["code", "name"]


async def test_ac9_batch_keeps_missing_in_place(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    known = mast_world.ids["c-mast"]
    unknown = 999_999_999

    full = (await get(client, "/photos/batch", {"ids": f"{known},{unknown}"})).json()
    cut = (
        await get(client, "/photos/batch", {"ids": f"{known},{unknown}", "fields": "sol"})
    ).json()

    assert list(cut) == ["items", "missing"]
    assert cut["missing"] == full["missing"] == [unknown]
    assert cut["items"] == [{"id": known, "sol": 5000, "credit": "credit of curiosity"}]


# --- AC-10 -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("img_src,sol,img_src", ["id", "sol", "img_src", "credit"]),
        ("id", ["id", "credit"]),
        ("credit", ["id", "credit"]),
        ("credit,id", ["id", "credit"]),
        ("mast,local_time", ["id", "credit", "local_time", "mast"]),
    ],
    ids=["duplicates", "id-only", "credit-only", "credit-id", "photo-order"],
)
async def test_ac10_repeats_collapse_and_id_and_credit_are_always_there(
    client: httpx.AsyncClient, mast_world: MastWorld, fields: str, expected: list[str]
) -> None:
    response = await get(
        client, "/photos", {"rovers": "curiosity", "per_page": 100, "fields": fields}
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    for item in items:
        assert list(item) == expected
        assert item["credit"] == "credit of curiosity"


async def test_ac10_mast_selected_alone_carries_the_object_or_null(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    response = await get(
        client, "/photos", {"rovers": "curiosity", "per_page": 100, "fields": "mast"}
    )

    by_id = {i["id"]: i for i in response.json()["items"]}
    assert by_id[mast_world.ids["c-mast"]]["mast"] == {"azimuth": 328.44, "elevation": -44.41}
    assert by_id[mast_world.ids["c-none"]]["mast"] is None


# --- AC-11 -----------------------------------------------------------------------------------


def _value_of_300_chars() -> str:
    """A valid `fields` value that is exactly 300 characters long (4 * 73 + 8)."""
    return ",".join(["sol"] * 73 + ["title", "id"])


BAD_VALUES = [
    pytest.param("", id="empty"),
    pytest.param("sol,", id="trailing-comma"),
    pytest.param(",sol", id="leading-comma"),
    pytest.param("sol,,id", id="empty-element"),
    pytest.param("SOL", id="upper-case"),
    pytest.param("Sol,id", id="mixed-case"),
    pytest.param("sol, id", id="space-after-comma"),
    pytest.param("images.small", id="nested-path"),
    pytest.param("foo", id="unknown"),
    pytest.param("sol,foo", id="one-unknown-among-valid"),
    pytest.param("sol," + "x" * 300, id="over-300-chars"),
    pytest.param("sol" + ",sol" * 75, id="over-300-chars-of-valid-keys"),
]


@ALL_ENDPOINT_PATHS
@pytest.mark.parametrize("value", BAD_VALUES)
async def test_ac11_bad_fields_value_gives_422_validation_error(
    client: httpx.AsyncClient, mast_world: MastWorld, path: str, value: str
) -> None:
    url, _, query = path.partition("?")
    params = dict(p.split("=") for p in query.split("&")) if query else {}

    response = await get(client, url, {**params, "fields": value})

    details = assert_validation_error(response, "fields")
    assert {d["field"] for d in details} == {"fields"}


@ALL_ENDPOINT_PATHS
async def test_ac11_repeated_fields_parameter_gives_422(
    client: httpx.AsyncClient, mast_world: MastWorld, path: str
) -> None:
    url, _, query = path.partition("?")
    params = [tuple(p.split("=")) for p in query.split("&")] if query else []

    response = await get(client, url, [*params, ("fields", "sol"), ("fields", "id")])

    assert_validation_error(response, "fields")


async def test_ac11_unknown_key_message_lists_the_allowed_keys(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    response = await get(client, "/photos", {"rovers": "curiosity", "fields": "foo"})

    [detail] = assert_validation_error(response, "fields")
    assert detail["field"] == "fields"
    for key in ALL_KEYS:
        assert key in detail["message"], key


async def test_ac11_exactly_300_characters_are_accepted(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    value = _value_of_300_chars()
    assert len(value) == 300

    response = await get(client, "/photos", {"rovers": "curiosity", "fields": value})

    assert response.status_code == 200, response.text
    assert list(response.json()["items"][0]) == ["id", "sol", "title", "credit"]


async def test_ac11_bad_fields_never_returns_a_partial_body(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    response = await get(client, "/photos", {"rovers": "curiosity", "fields": "sol,foo"})

    assert response.status_code == 422
    assert "items" not in response.json()


# --- AC-14 -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/photos", {"rovers": "curiosity", "per_page": 100}),
        ("/rovers/curiosity/photos", {"per_page": 100}),
        ("/rovers/curiosity/photos/latest", {"per_page": 100}),
    ],
    ids=["photos", "rover-photos", "latest"],
)
async def test_ac14_etag_differs_with_fields_and_if_none_match_gives_304(
    client: httpx.AsyncClient, mast_world: MastWorld, path: str, params: dict[str, Any]
) -> None:
    plain = await get(client, path, params)
    cut = await get(client, path, {**params, "fields": "sol"})
    again = await get(client, path, {**params, "fields": "sol"})

    assert plain.status_code == cut.status_code == 200
    assert plain.headers["etag"] != cut.headers["etag"]
    assert cut.headers["etag"] == again.headers["etag"]
    assert plain.headers["cache-control"] == cut.headers["cache-control"] == SHORT

    revalidate = await client.get(
        f"{V2}{path}",
        params={**params, "fields": "sol"},
        headers={"If-None-Match": cut.headers["etag"]},
    )
    assert revalidate.status_code == 304
    assert revalidate.content == b""
    assert revalidate.headers["etag"] == cut.headers["etag"]
    assert revalidate.headers["cache-control"] == SHORT

    cross = await client.get(
        f"{V2}{path}", params=params, headers={"If-None-Match": cut.headers["etag"]}
    )
    assert cross.status_code == 200  # the ETag of the cut body does not match the full body


async def test_ac14_batch_etag_differs_with_fields(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    ids = str(mast_world.ids["c-mast"])

    plain = await get(client, "/photos/batch", {"ids": ids})
    cut = await get(client, "/photos/batch", {"ids": ids, "fields": "sol"})

    assert plain.headers["etag"] != cut.headers["etag"]
    assert plain.headers["cache-control"] == cut.headers["cache-control"] == SHORT


async def test_ac14_different_fields_give_different_etags(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    base = {"rovers": "curiosity", "per_page": 100}

    first = await get(client, "/photos", {**base, "fields": "sol"})
    second = await get(client, "/photos", {**base, "fields": "sol,camera"})

    assert first.headers["etag"] != second.headers["etag"]


# --- AC-15 -----------------------------------------------------------------------------------


async def test_ac15_photo_detail_ignores_fields(
    client: httpx.AsyncClient, mast_world: MastWorld
) -> None:
    url = f"{V2}/photos/{mast_world.ids['c-mast']}"

    plain = await client.get(url)
    cut = await client.get(url, params={"fields": "sol"})
    bad = await client.get(url, params={"fields": "foo"})

    assert plain.status_code == cut.status_code == bad.status_code == 200
    assert cut.json() == plain.json() == bad.json()
    assert cut.json()["mast"] == {"azimuth": 328.44, "elevation": -44.41}
    assert cut.headers["etag"] == plain.headers["etag"]


# --- other endpoints and v1 ignore the parameter ---------------------------------------------


@pytest.mark.parametrize("path", ["/rovers", "/rovers/curiosity", "/rovers/curiosity/cameras"])
async def test_fields_is_ignored_by_endpoints_that_do_not_know_it(
    client: httpx.AsyncClient, mast_world: MastWorld, path: str
) -> None:
    plain = await get(client, path, {})
    with_fields = await get(client, path, {"fields": "foo"})

    assert with_fields.status_code == 200
    assert with_fields.json() == plain.json()


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_ignores_fields(
    client: httpx.AsyncClient, mast_world: MastWorld, prefix: str
) -> None:
    params = {"sol": 5000}
    plain = await client.get(f"{prefix}/rovers/curiosity/photos", params=params)
    with_fields = await client.get(
        f"{prefix}/rovers/curiosity/photos", params={**params, "fields": "sol"}
    )

    assert with_fields.status_code == 200
    assert with_fields.content == plain.content
    for photo in with_fields.json()["photos"]:
        assert list(photo) == ["id", "sol", "camera", "img_src", "earth_date", "rover"]
