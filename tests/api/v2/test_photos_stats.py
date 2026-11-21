"""GET /api/v2/photos/stats (stage 18C, docs/API.md; spec docs/proposals/stage-18c-stats-batch.md).

Dataset: `multi_world` (five rovers, sols 100-104, six photos per sol, two cameras per rover, two
extra curiosity photos on sol 105). Expected JSON comes from the reference model in
`tests/api/v2/_stats.py`; the hard-coded numbers are checked by hand against the dataset.
Covers AC-1 ... AC-5 (rover/camera/month/year over one period), AC-7, AC-8, AC-16 (stats part),
AC-17 (route precedence). Multi-period cases are in test_photos_stats_periods.py.
"""

from datetime import date
from typing import Any

import httpx
import pytest

from tests.api.v2._multi import ALL_ROVERS, Multi
from tests.api.v2._stats import GROUP_KEYS, TOP_KEYS, SRow, expected_stats
from tests.api.v2.conftest import V2, assert_error, assert_json, assert_validation_error

pytestmark = pytest.mark.api

URL = f"{V2}/photos/stats"
SHORT = "public, max-age=300"


@pytest.fixture
def srows(multi_world: Multi) -> list[SRow]:
    return [SRow(r.rover, r.sol, r.camera, r.sample_type, r.earth_date) for r in multi_world.rows]


@pytest.fixture
async def camera_order(client: httpx.AsyncClient, multi_world: Multi) -> dict[str, list[str]]:
    order: dict[str, list[str]] = {}
    for rover in ALL_ROVERS:
        body = (await client.get(f"{V2}/rovers/{rover}/cameras")).json()
        order[rover] = [camera["code"] for camera in body["items"]]
    return order


async def get_stats(client: httpx.AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- AC-1 --------------------------------------------------------------------------------------


async def test_ac1_stats_without_params_groups_by_rover_with_exact_numbers(
    client: httpx.AsyncClient, srows: list[SRow], camera_order: dict[str, list[str]]
) -> None:
    body = await get_stats(client)

    assert_json(body, expected_stats(srows, camera_order))
    assert list(body) == TOP_KEYS
    assert body["group_by"] == "rover"
    assert [g["rover"] for g in body["groups"]] == [
        "curiosity",
        "ingenuity",
        "opportunity",
        "perseverance",
        "spirit",
    ]
    assert all(g["camera"] is None and g["period"] is None for g in body["groups"])
    # 5 rovers * 5 sols * 6 photos + 2 extra curiosity photos
    assert body["total_photos"] == 152
    assert body["sols_with_photos"] == 26  # sols of different rovers count separately
    assert {g["rover"]: g["total_photos"] for g in body["groups"]} == {
        "curiosity": 32,
        "ingenuity": 30,
        "opportunity": 30,
        "perseverance": 30,
        "spirit": 30,
    }


async def test_ac1_stats_total_equals_sum_of_groups_and_sum_of_rovers_total_photos(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client)
    rovers = (await client.get(f"{V2}/rovers")).json()["items"]

    assert body["total_photos"] == sum(g["total_photos"] for g in body["groups"])
    assert body["total_photos"] == sum(r["total_photos"] for r in rovers)
    for group in body["groups"]:
        rover = next(r for r in rovers if r["id"] == group["rover"])
        assert group["total_photos"] == rover["total_photos"]
    # stats is also the same number as the unfiltered list
    assert body["total_photos"] == (await client.get(f"{V2}/photos")).json()["total"]


async def test_ac1_rover_without_photos_has_no_group(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client, rovers="curiosity", sol_min="9999")
    assert body["groups"] == []


# --- AC-2, AC-3 --------------------------------------------------------------------------------

FILTER_PARAMS = {
    "rovers": "perseverance",
    "camera": "NAVCAM_LEFT",
    "sol_min": "101",
    "sol_max": "103",
}


async def test_ac2_stats_with_camera_and_sol_range_matches_list_total(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client, **FILTER_PARAMS)
    listing = (await client.get(f"{V2}/photos", params=FILTER_PARAMS)).json()

    assert body["total_photos"] == listing["total"] == 9  # n = 0..2 of three sols
    assert body["sols_with_photos"] == 3
    assert [
        (g["rover"], g["total_photos"], g["first_sol"], g["last_sol"]) for g in body["groups"]
    ] == [("perseverance", 9, 101, 103)]


async def test_ac3_stats_sample_type_full_matches_list_total(
    client: httpx.AsyncClient, srows: list[SRow], camera_order: dict[str, list[str]]
) -> None:
    params = {**FILTER_PARAMS, "sample_type": "full"}
    body = await get_stats(client, **params)
    listing = (await client.get(f"{V2}/photos", params=params)).json()

    assert body["total_photos"] == listing["total"]
    assert 0 < body["total_photos"] < 9
    expected = expected_stats(
        srows,
        camera_order,
        rovers=["perseverance"],
        cameras=["NAVCAM_LEFT"],
        sol_min=101,
        sol_max=103,
        full_only=True,
    )
    assert_json(body, expected)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="all"),
        pytest.param({"sample_type": "full"}, id="all-full"),
        pytest.param({"rovers": "curiosity,spirit", "sol_min": "102"}, id="two-rovers-sol-min"),
        pytest.param({"camera": "FHAZ,PANCAM", "sol_max": "101"}, id="cameras-sol-max"),
        pytest.param({"rovers": "opportunity", "camera": "navcam"}, id="lowercase-camera"),
    ],
)
async def test_ac2_stats_total_matches_list_total_for_the_same_filters(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    body = await get_stats(client, **params)
    listing = (await client.get(f"{V2}/photos", params=params)).json()

    assert body["total_photos"] == listing["total"]


async def test_ac2_stats_total_of_a_rover_equals_rover_total_photos_in_rovers(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    rovers = {r["id"]: r for r in (await client.get(f"{V2}/rovers")).json()["items"]}
    body = await get_stats(client, rovers="perseverance")

    assert body["total_photos"] == rovers["perseverance"]["total_photos"] == 30


# --- AC-4 --------------------------------------------------------------------------------------


@pytest.mark.parametrize("rovers", ["curiosity,perseverance", "perseverance,curiosity"])
async def test_ac4_group_by_camera_orders_by_rover_then_camera_sort_order(
    client: httpx.AsyncClient,
    srows: list[SRow],
    camera_order: dict[str, list[str]],
    rovers: str,
) -> None:
    body = await get_stats(client, rovers=rovers, group_by="camera")

    assert_json(
        body,
        expected_stats(
            srows,
            camera_order,
            rovers=rovers.split(","),
            group_by="camera",
        ),
    )
    assert [(g["rover"], g["camera"]) for g in body["groups"]] == [
        *[("curiosity", c) for c in camera_order["curiosity"] if c in ("FHAZ", "NAV_LEFT_B")],
        *[
            ("perseverance", c)
            for c in camera_order["perseverance"]
            if c in ("NAVCAM_LEFT", "MCZ_LEFT")
        ],
    ]
    assert all(g["period"] is None for g in body["groups"])
    # the same sol appears under two cameras, but is counted once at the top level
    assert body["sols_with_photos"] == 11
    assert body["total_photos"] == sum(g["total_photos"] for g in body["groups"]) == 62


async def test_ac4_group_by_camera_omits_cameras_without_photos_in_the_selection(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client, rovers="curiosity", group_by="camera", sol_min="105")

    assert [(g["camera"], g["total_photos"]) for g in body["groups"]] == [("FHAZ", 2)]


async def test_ac4_group_by_camera_with_camera_filter_keeps_only_those_cameras(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(
        client, rovers="curiosity,perseverance", group_by="camera", camera="FHAZ"
    )

    assert [(g["rover"], g["camera"]) for g in body["groups"]] == [("curiosity", "FHAZ")]


# --- AC-5 (one period; several periods in test_photos_stats_periods.py) -------------------------


@pytest.mark.parametrize(
    ("group_by", "period"), [("month", "2021-03"), ("year", "2021")], ids=["month", "year"]
)
async def test_ac5_group_by_period_formats_period_and_nulls_camera(
    client: httpx.AsyncClient,
    srows: list[SRow],
    camera_order: dict[str, list[str]],
    group_by: str,
    period: str,
) -> None:
    body = await get_stats(client, rovers="curiosity,spirit", group_by=group_by)

    assert_json(
        body,
        expected_stats(srows, camera_order, rovers=["curiosity", "spirit"], group_by=group_by),
    )
    assert [(g["rover"], g["camera"], g["period"]) for g in body["groups"]] == [
        ("curiosity", None, period),
        ("spirit", None, period),
    ]


# --- AC-7 --------------------------------------------------------------------------------------


async def test_ac7_group_has_first_and_last_sol_and_sol_dates(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client)

    by_rover = {g["rover"]: g for g in body["groups"]}
    assert list(by_rover["curiosity"]) == GROUP_KEYS
    # curiosity: sols 100-104 plus sol 105 (earliest earth_date of that sol is 2021-03-06)
    assert (
        by_rover["curiosity"]["first_sol"],
        by_rover["curiosity"]["last_sol"],
        by_rover["curiosity"]["first_date"],
        by_rover["curiosity"]["last_date"],
        by_rover["curiosity"]["sols_with_photos"],
    ) == (100, 105, "2021-03-01", "2021-03-06", 6)
    assert (
        by_rover["spirit"]["first_sol"],
        by_rover["spirit"]["last_sol"],
        by_rover["spirit"]["first_date"],
        by_rover["spirit"]["last_date"],
    ) == (100, 104, "2021-03-01", "2021-03-05")
    # the top level has dates but no sols (sols of different rovers are not comparable)
    assert (body["first_date"], body["last_date"]) == ("2021-03-01", "2021-03-06")
    assert "first_sol" not in body and "last_sol" not in body


async def test_ac7_empty_selection_returns_zeros_nulls_and_no_groups(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL, params={"sol_min": "999999"})

    assert response.status_code == 200
    assert response.json() == {
        "group_by": "rover",
        "total_photos": 0,
        "sols_with_photos": 0,
        "first_date": None,
        "last_date": None,
        "groups": [],
    }


async def test_ac7_empty_selection_when_sol_min_is_above_every_max_sol_for_a_group_by(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client, sol_min="106", group_by="month")

    assert body["groups"] == [] and body["total_photos"] == 0 and body["group_by"] == "month"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"sol_min": "102"}, id="sol-min"),
        pytest.param({"sol_max": "101"}, id="sol-max"),
        pytest.param({"sol_min": "101", "sol_max": "101"}, id="single-sol"),
        pytest.param({"date_min": "2021-03-03"}, id="date-min"),
        pytest.param({"date_max": "2021-03-02", "rovers": "spirit"}, id="date-max"),
        pytest.param({"date_min": "2021-03-02", "date_max": "2021-03-03"}, id="date-range"),
    ],
)
async def test_ac7_ranges_include_both_ends_and_match_the_model(
    client: httpx.AsyncClient,
    srows: list[SRow],
    camera_order: dict[str, list[str]],
    params: dict[str, str],
) -> None:
    body = await get_stats(client, **params)

    expected = expected_stats(
        srows,
        camera_order,
        rovers=[params["rovers"]] if "rovers" in params else None,
        sol_min=int(params["sol_min"]) if "sol_min" in params else None,
        sol_max=int(params["sol_max"]) if "sol_max" in params else None,
        date_min=date.fromisoformat(params["date_min"]) if "date_min" in params else None,
        date_max=date.fromisoformat(params["date_max"]) if "date_max" in params else None,
    )
    assert_json(body, expected)
    assert body["total_photos"] > 0


async def test_stats_ignores_unsupported_photo_filters_as_unknown_parameters(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    plain = await get_stats(client, rovers="spirit")
    noisy = await get_stats(
        client,
        rovers="spirit",
        min_width="5000",
        local_time_min="01:00",
        site="3",
        near="1,2",
        foo="bar",
        page="9",
    )

    assert noisy == plain


# --- AC-8 --------------------------------------------------------------------------------------

BAD_REQUESTS = [
    pytest.param({"rovers": "pluto"}, ("rovers",), id="unknown-rover"),
    pytest.param({"rovers": "curiosity,,spirit"}, ("rovers",), id="empty-rover-element"),
    pytest.param({"camera": "NOPE"}, ("camera",), id="unknown-camera"),
    pytest.param(
        {"rovers": "curiosity", "camera": "NAVCAM_LEFT"}, ("camera",), id="camera-of-other-rover"
    ),
    pytest.param({"group_by": "sol"}, ("group_by",), id="group-by-sol"),
    pytest.param({"group_by": "day"}, ("group_by",), id="group-by-other"),
    pytest.param({"group_by": ""}, ("group_by",), id="group-by-empty"),
    pytest.param({"sample_type": "subframe"}, ("sample_type",), id="sample-type-subframe"),
    pytest.param({"sample_type": "downsampled"}, ("sample_type",), id="sample-type-downsampled"),
    pytest.param({"sample_type": "bogus"}, ("sample_type",), id="sample-type-unknown"),
    pytest.param(
        {"sol_min": "1", "date_min": "2021-01-01"},
        ("sol_min", "date_min"),
        id="sol-min-with-date-min",
    ),
    pytest.param(
        {"sol_max": "9", "date_max": "2021-01-01"},
        ("sol_max", "date_max"),
        id="sol-max-with-date-max",
    ),
    pytest.param({"sol_min": "5", "sol_max": "4"}, ("sol_min", "sol_max"), id="sol-min-above-max"),
    pytest.param(
        {"date_min": "2021-03-02", "date_max": "2021-03-01"},
        ("date_min", "date_max"),
        id="date-min-above-max",
    ),
    pytest.param({"sol_min": "-1"}, ("sol_min",), id="sol-min-negative"),
    pytest.param({"sol_max": "2147483648"}, ("sol_max",), id="sol-max-above-int32"),
    pytest.param({"sol_min": "abc"}, ("sol_min",), id="sol-min-not-a-number"),
    pytest.param({"date_min": "2021-13-01"}, ("date_min",), id="date-min-bad-month"),
    pytest.param({"date_max": "yesterday"}, ("date_max",), id="date-max-not-a-date"),
    pytest.param({"camera": ",".join(f"C{n}" for n in range(11))}, ("camera",), id="11-cameras"),
]


@pytest.mark.parametrize(("params", "fields"), BAD_REQUESTS)
async def test_ac8_invalid_request_gets_422_validation_error(
    client: httpx.AsyncClient, params: dict[str, str], fields: tuple[str, ...]
) -> None:
    response = await client.get(URL, params=params)

    assert_validation_error(response, *fields)
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


async def test_ac8_unknown_camera_error_lists_valid_codes_per_rover(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL, params={"rovers": "curiosity,perseverance", "camera": "NOPE"})

    details = assert_validation_error(response, "camera")
    text = " ".join(f"{d['field']} {d['message']}" for d in details)
    assert "FHAZ" in text and "NAVCAM_LEFT" in text


async def test_ac8_camera_valid_for_one_of_several_rovers_is_accepted(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = await get_stats(client, rovers="curiosity,perseverance", camera="FHAZ")

    assert [g["rover"] for g in body["groups"]] == ["curiosity"]


# --- AC-16 (stats) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{}, {"group_by": "month"}, {"sol_min": "999999"}],
    ids=["plain", "month", "empty"],
)
async def test_ac16_stats_is_cached_five_minutes_not_an_hour(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert response.status_code == 200
    assert response.headers["cache-control"] == SHORT
    assert response.headers["etag"]


async def test_ac16_stats_etag_is_stable_and_differs_between_queries(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    first = await client.get(URL, params={"rovers": "spirit"})
    again = await client.get(URL, params={"rovers": "spirit"})
    other = await client.get(URL, params={"rovers": "opportunity"})

    assert first.headers["etag"] == again.headers["etag"]
    assert first.headers["etag"] != other.headers["etag"]


async def test_ac16_stats_matching_if_none_match_gets_304_without_a_body(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    first = await client.get(URL, params={"group_by": "camera"})
    response = await client.get(
        URL, params={"group_by": "camera"}, headers={"If-None-Match": first.headers["etag"]}
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == first.headers["etag"]
    assert response.headers["cache-control"] == SHORT


async def test_ac16_stats_non_matching_if_none_match_gets_the_full_body(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL, headers={"If-None-Match": 'W/"nope"'})

    assert response.status_code == 200
    assert response.json()["group_by"] == "rover"


async def test_ac16_stats_head_has_the_get_headers_and_no_body(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got = await client.get(URL, params={"group_by": "year"})
    head = await client.head(URL, params={"group_by": "year"})

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == SHORT
    assert head.headers["etag"] == got.headers["etag"]


async def test_ac16_stats_validation_error_is_not_cached(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL, params={"group_by": "sol"})

    assert_error(response, 422, "validation_error")
    assert response.headers["cache-control"] == "no-store"


# --- AC-17 -------------------------------------------------------------------------------------


async def test_ac17_stats_is_not_captured_by_the_photo_id_route(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL)

    assert response.status_code == 200
    assert "groups" in response.json()
    assert response.headers["cache-control"] != "public, max-age=3600"
