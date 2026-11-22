"""GET /api/v2/rovers/{rover}/photos: stage 18B-1 parameters (docs/API.md).

New: sol_min/sol_max, date_min/date_max, site_min/site_max, sort, camera list. Old queries must
not change (AC-10): their bodies are compared with the reference order, with an explicit
`sort=-sol`, and `nearest_sols` keeps its rules. There is no `page x per_page` window here.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from tests.api.v2._multi import SORTS, Multi, Row, ids
from tests.api.v2.conftest import LIST_KEYS, V2, assert_validation_error

pytestmark = pytest.mark.api

ROVER = "curiosity"
URL = f"{V2}/rovers/{ROVER}/photos"


def d(value: str) -> date:
    return date.fromisoformat(value)


async def get_all(
    client: httpx.AsyncClient, params: dict[str, str], url: str = URL, per_page: int = 13
) -> tuple[list[int], int]:
    got: list[int] = []
    total: int | None = None
    page = 1
    while True:
        response = await client.get(url, params={**params, "page": page, "per_page": per_page})
        assert response.status_code == 200, response.text
        body = response.json()
        total = body["total"] if total is None else total
        assert body["total"] == total
        got += [item["id"] for item in body["items"]]
        if len(body["items"]) < per_page:
            return got, total
        page += 1


# --- AC-10: new parameters --------------------------------------------------------------------

CASES: list[Any] = [
    pytest.param(
        {"sol_min": "101", "sol_max": "102"}, lambda r: 101 <= r.sol <= 102, id="sol-range"
    ),
    pytest.param({"sol_min": "104"}, lambda r: r.sol >= 104, id="sol-min"),
    pytest.param({"sol_max": "100"}, lambda r: r.sol <= 100, id="sol-max"),
    pytest.param(
        {"date_min": "2021-03-02", "date_max": "2021-03-03"},
        lambda r: d("2021-03-02") <= r.earth_date <= d("2021-03-03"),
        id="date-range",
    ),
    pytest.param(
        {"date_min": "2021-03-07"}, lambda r: r.earth_date >= d("2021-03-07"), id="date-min-trap"
    ),
    pytest.param(
        {"site_min": "11", "site_max": "12"},
        lambda r: r.site is not None and 11 <= r.site <= 12,
        id="site-range",
    ),
    pytest.param({"site_max": "10"}, lambda r: r.site is not None and r.site <= 10, id="site-max"),
    pytest.param(
        {"camera": "FHAZ,nav_left_b"},
        lambda r: r.camera in {"FHAZ", "NAV_LEFT_B"},
        id="camera-list",
    ),
    pytest.param(
        {"camera": "nav_left_b", "sample_type": "subframe", "sol_min": "102"},
        lambda r: r.camera == "NAV_LEFT_B" and r.sample_type == "subframe" and r.sol >= 102,
        id="camera-sample-sol",
    ),
]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize(("params", "where"), CASES)
async def test_ac10_new_filters_and_sorts_match_the_reference(
    client: httpx.AsyncClient,
    multi_world: Multi,
    params: dict[str, str],
    where: Callable[[Row], bool],
    sort: str,
) -> None:
    got, total = await get_all(client, {**params, "sort": sort})

    expected = multi_world.select(rovers=[ROVER], sort=sort, where=where)
    assert got == ids(expected)
    assert total == len(expected)


@pytest.mark.parametrize("sort", SORTS)
async def test_ac10_sort_ties_on_taken_at_are_broken_by_id_in_the_sort_direction(
    client: httpx.AsyncClient, multi_world: Multi, sort: str
) -> None:
    got, _ = await get_all(client, {"sort": sort})

    expected = ids(multi_world.select(rovers=[ROVER], sort=sort))
    assert got == expected
    pairs = [(multi_world.by_id(i).taken_at, i) for i in got]
    assert any(a[0] == b[0] for a, b in zip(pairs, pairs[1:], strict=False)), "precondition: ties"


async def test_ac10_rovers_param_is_not_a_filter_of_the_per_rover_route(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"rovers": "spirit", "per_page": "100"})).json()

    assert {item["rover"] for item in body["items"]} == {ROVER}


@pytest.mark.parametrize("value", ["asc", "id", "-id", "", "sol,taken_at"])
async def test_ac10_unknown_sort_is_422(
    client: httpx.AsyncClient, multi_world: Multi, value: str
) -> None:
    response = await client.get(URL, params={"sort": value})

    details = assert_validation_error(response, "sort")
    message = " ".join(item["message"] for item in details)
    for allowed in SORTS:
        assert allowed in message


@pytest.mark.parametrize(
    "params",
    [
        {"sol": "101", "sol_min": "100"},
        {"sol": "101", "sol_max": "103"},
        {"earth_date": "2021-03-02", "date_min": "2021-03-01"},
        {"earth_date": "2021-03-02", "date_max": "2021-03-03"},
        {"sol": "101", "earth_date": "2021-03-02"},
        {"sol_min": "100", "date_min": "2021-03-01"},
        {"sol_max": "104", "earth_date": "2021-03-02"},
        {"sol": "101", "date_max": "2021-03-05"},
        {"sol_min": "104", "sol_max": "100"},
        {"date_min": "2021-03-05", "date_max": "2021-03-01"},
        {"site_min": "13", "site_max": "12"},
        {"site": "12", "site_min": "10"},
        {"site": "12", "site_max": "14"},
        {"drive_from": "1"},
        {"camera": "FHAZ,,NAV_LEFT_B"},
        {"camera": "PANCAM"},
        {"camera": "FHAZ,PANCAM"},
        {"sol_min": "-1"},
        {"sol_max": "2147483648"},
        {"site_min": "-1"},
    ],
    ids=[
        "sol+sol_min",
        "sol+sol_max",
        "earth_date+date_min",
        "earth_date+date_max",
        "sol+earth_date",
        "sol_min+date_min",
        "sol_max+earth_date",
        "sol+date_max",
        "sol-min>max",
        "date-min>max",
        "site-min>max",
        "site+site_min",
        "site+site_max",
        "drive-without-site",
        "camera-empty-element",
        "camera-unknown",
        "camera-one-unknown-in-list",
        "sol_min-negative",
        "sol_max-over-int32",
        "site_min-negative",
    ],
)
async def test_ac10_invalid_combinations_and_values_are_422(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    # "site" is the missing dependency of drive_*: naming it is legitimate.
    assert_validation_error(response, *params.keys(), *(["site"] if "drive_from" in params else []))


async def test_ac10_more_than_ten_camera_codes_is_422(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    codes = ",".join(["FHAZ"] * 11)

    response = await client.get(URL, params={"camera": codes})

    assert_validation_error(response, "camera")


async def test_ac10_site_range_needs_no_rovers_param_and_skips_photos_without_site(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(client, {"site_min": "10", "site_max": "14"})

    assert total == 15
    assert all(multi_world.by_id(i).site is not None for i in got)


# --- AC-9: no result window here ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(101, 100), (10001, 1), (1_000_000, 100)],
    ids=["101x100", "10001x1", "1e6x100"],
)
async def test_ac9_per_rover_route_has_no_page_times_per_page_ceiling(
    client: httpx.AsyncClient, multi_world: Multi, page: int, per_page: int
) -> None:
    response = await client.get(URL, params={"page": page, "per_page": per_page})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 32


# --- AC-10: nearest_sols ------------------------------------------------------------------------


async def test_ac10_nearest_sols_key_stays_on_the_per_rover_route(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"sol": "101"})).json()

    assert list(body) == LIST_KEYS
    assert body["nearest_sols"] is None


async def test_ac10_nearest_sols_is_filled_for_an_empty_sol(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"sol": "99"})).json()

    assert body["total"] == 0
    assert body["nearest_sols"] == {"before": None, "after": 100}


async def test_ac10_nearest_sols_is_filled_for_an_empty_earth_date_gap(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"earth_date": "2021-03-20"})).json()

    assert body["total"] == 0
    assert body["nearest_sols"] == {"before": 105, "after": None}


@pytest.mark.parametrize(
    "params",
    [{"sol_min": "500"}, {"sol_max": "50"}, {"date_min": "2030-01-01"}, {"site_min": "999"}],
    ids=["sol_min", "sol_max", "date_min", "site_min"],
)
async def test_ac10_nearest_sols_stays_null_for_range_params(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    body = (await client.get(URL, params=params)).json()

    assert body["total"] == 0
    assert body["nearest_sols"] is None


# --- AC-10: old queries are unchanged ------------------------------------------------------------

OLD_QUERIES: list[Any] = [
    pytest.param({}, lambda r: True, id="plain"),
    pytest.param({"sol": "101"}, lambda r: r.sol == 101, id="sol"),
    pytest.param(
        {"earth_date": "2021-03-02"}, lambda r: r.earth_date == d("2021-03-02"), id="date"
    ),
    pytest.param({"camera": "nav_left_b"}, lambda r: r.camera == "NAV_LEFT_B", id="camera"),
    pytest.param({"sample_type": "full"}, lambda r: r.sample_type == "full", id="sample-type"),
    pytest.param(
        {"site": "11", "drive_from": "10", "drive_to": "30"},
        lambda r: r.site == 11 and r.drive is not None and 10 <= r.drive <= 30,
        id="site-drive",
    ),
    pytest.param({"sol": "9999"}, lambda r: False, id="empty-sol"),
]


@pytest.mark.parametrize(("params", "where"), OLD_QUERIES)
async def test_ac10_old_queries_keep_order_and_equal_the_explicit_default_sort_byte_for_byte(
    client: httpx.AsyncClient,
    multi_world: Multi,
    params: dict[str, str],
    where: Callable[[Row], bool],
) -> None:
    query = {**params, "per_page": "100"}
    plain = await client.get(URL, params=query)
    explicit = await client.get(URL, params={**query, "sort": "-sol"})

    assert plain.status_code == explicit.status_code == 200
    assert plain.content == explicit.content
    body = plain.json()
    assert list(body) == LIST_KEYS
    assert [item["id"] for item in body["items"]] == ids(
        multi_world.select(rovers=[ROVER], where=where)
    )


async def test_ac10_old_pagination_is_unchanged(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    plain = await client.get(URL, params={"page": "3", "per_page": "7"})
    explicit = await client.get(URL, params={"page": "3", "per_page": "7", "sort": "-sol"})

    assert plain.content == explicit.content
    expected = ids(multi_world.select(rovers=[ROVER]))[14:21]
    assert [item["id"] for item in plain.json()["items"]] == expected


async def test_ac10_unknown_rover_is_still_404(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(f"{V2}/rovers/nope/photos", params={"sort": "taken_at"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "rover_not_found"


# --- site range together with a camera list (was a 500) ---------------------------------------


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("camera", ["FHAZ,NAV_LEFT_B", "nav_left_b,fhaz"])
@pytest.mark.parametrize(
    "site", [{"site_min": "11", "site_max": "13"}, {"site_min": "12"}, {"site_max": "11"}]
)
async def test_site_range_with_a_camera_list_matches_the_reference(
    client: httpx.AsyncClient, multi_world: Multi, site: dict[str, str], camera: str, sort: str
) -> None:
    got, total = await get_all(client, {"camera": camera, "sort": sort, **site}, per_page=9)

    codes = {c.upper() for c in camera.split(",")}
    lo, hi = int(site.get("site_min", 0)), int(site.get("site_max", 2**31 - 1))
    expected = multi_world.select(
        rovers=[ROVER],
        sort=sort,
        where=lambda r: r.camera in codes and r.site is not None and lo <= r.site <= hi,
    )
    assert expected
    assert got == ids(expected)
    assert total == len(expected)


# --- AC-10: exact JSON of old queries, built from the contract ---------------------------------


def expected_photo(world: Multi, row: Row) -> dict[str, Any]:
    url = f"https://images.example.test/{row.source_id}.png"
    return {
        "id": row.id,
        "rover": row.rover,
        "sol": row.sol,
        "earth_date": row.earth_date.isoformat(),
        "taken_at": row.taken_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "camera": {"code": row.camera, "name": world.cameras[(row.rover, row.camera)]},
        "sample_type": row.sample_type,
        "title": None,
        "img_src": url,
        "images": {"small": None, "medium": None, "large": None, "full": url},
        "credit": "NASA/JPL-Caltech",
        "source": {"id": row.source_id, "link": None},
        "width": None,
        "height": None,
        "local_time": None,  # the dataset has no local time in `extra`
        "mast": None,  # nor mast angles (stage 19)
    }


@pytest.mark.parametrize(("params", "where"), OLD_QUERIES)
async def test_ac10_old_queries_return_the_exact_contract_json(
    client: httpx.AsyncClient,
    multi_world: Multi,
    params: dict[str, str],
    where: Callable[[Row], bool],
) -> None:
    """Independent of the code under test: the whole body is rebuilt from the dataset."""
    response = await client.get(URL, params={**params, "per_page": "100"})

    rows = multi_world.select(rovers=[ROVER], where=where)
    nearest = {"before": 105, "after": None} if params == {"sol": "9999"} else None
    assert response.json() == {
        "items": [expected_photo(multi_world, r) for r in rows],
        "page": 1,
        "per_page": 100,
        "total": len(rows),
        "nearest_sols": nearest,
    }


# --- result window only for a camera list of two or more codes ---------------------------------

TWO_CAMERAS = "FHAZ,NAV_LEFT_B"


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(100, 100), (10000, 1), (400, 25), (1, 100)],
    ids=["100x100", "10000x1", "400x25", "1x100"],
)
async def test_camera_list_window_up_to_10000_is_200(
    client: httpx.AsyncClient, multi_world: Multi, page: int, per_page: int
) -> None:
    response = await client.get(
        URL, params={"camera": TWO_CAMERAS, "page": page, "per_page": per_page}
    )

    assert response.status_code == 200
    assert response.json()["total"] == len(
        multi_world.select(rovers=[ROVER], where=lambda r: r.camera in {"FHAZ", "NAV_LEFT_B"})
    )


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(101, 100), (10001, 1), (401, 25), (1_000_000, 100)],
    ids=["101x100", "10001x1", "401x25", "1e6x100"],
)
@pytest.mark.parametrize("sort", SORTS)
async def test_camera_list_window_over_10000_is_422_on_page(
    client: httpx.AsyncClient, multi_world: Multi, page: int, per_page: int, sort: str
) -> None:
    response = await client.get(
        URL, params={"camera": TWO_CAMERAS, "page": page, "per_page": per_page, "sort": sort}
    )

    assert_validation_error(response, "page")


async def test_camera_list_window_applies_together_with_other_filters(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(
        URL,
        params={"camera": "fhaz,nav_left_b", "sol_min": "100", "page": 101, "per_page": 100},
    )

    assert_validation_error(response, "page")


@pytest.mark.parametrize(
    "params",
    [{"camera": "FHAZ"}, {"camera": "nav_left_b"}, {}],
    ids=["one", "one-lower", "none"],
)
async def test_one_camera_or_none_has_no_window_a_deep_page_is_200(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params={**params, "page": 1_000_000, "per_page": 100})

    assert response.status_code == 200
    assert response.json()["items"] == []
