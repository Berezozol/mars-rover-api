"""GET /api/v2/photos: cross-rover list (stage 18B-1).

Expected results come from the reference model in `_multi.py` (filter, then sort by the
documented keys), never from the application. Test names carry the acceptance criterion
(`test_ac<N>_...`).
"""

from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from tests.api.v2._multi import ALL_ROVERS, SORTS, Multi, Row, ids
from tests.api.v2.conftest import INT32_MAX, PHOTO_KEYS, V2, assert_validation_error

pytestmark = pytest.mark.api

URL = f"{V2}/photos"
LIST_KEYS = ["items", "page", "per_page", "total"]


async def get_all(
    client: httpx.AsyncClient, params: dict[str, str], per_page: int = 100
) -> tuple[list[int], int]:
    """Walk every page; check the envelope on each; return (ids in order, total)."""
    got: list[int] = []
    total: int | None = None
    page = 1
    while True:
        response = await client.get(URL, params={**params, "page": page, "per_page": per_page})
        assert response.status_code == 200, response.text
        body = response.json()
        assert list(body) == LIST_KEYS
        assert body["page"] == page
        assert body["per_page"] == per_page
        if total is None:
            total = body["total"]
        assert body["total"] == total, "total must not change between pages"
        got += [item["id"] for item in body["items"]]
        if len(body["items"]) < per_page:
            return got, total
        page += 1
        assert page < 500, "runaway pagination"


def d(value: str) -> date:
    return date.fromisoformat(value)


# --- AC-1 / AC-2: basic selection --------------------------------------------------------


async def test_ac1_two_rovers_sol_range_exact_order_and_summed_total(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    params = {
        "rovers": "curiosity,perseverance",
        "sol_min": "101",
        "sol_max": "102",
        "per_page": "100",
    }
    response = await client.get(URL, params=params)

    assert response.status_code == 200
    body = response.json()
    expected = multi_world.select(
        rovers=["curiosity", "perseverance"], where=lambda r: 101 <= r.sol <= 102
    )
    assert list(body) == LIST_KEYS  # no nearest_sols
    assert "nearest_sols" not in body
    assert [item["id"] for item in body["items"]] == ids(expected)
    assert body["total"] == len(expected) == 24
    assert {item["rover"] for item in body["items"]} == {"curiosity", "perseverance"}
    assert {item["sol"] for item in body["items"]} == {101, 102}
    assert (body["page"], body["per_page"]) == (1, 100)


async def test_ac1_photo_objects_have_the_v2_photo_shape(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"rovers": "spirit", "per_page": "5"})).json()

    assert body["items"]
    for item in body["items"]:
        assert list(item) == PHOTO_KEYS
        assert item["rover"] == "spirit"
        assert item["credit"]


async def test_ac1_defaults_are_page_1_per_page_25(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL)).json()

    assert (body["page"], body["per_page"]) == (1, 25)
    assert len(body["items"]) == 25
    assert body["total"] == len(multi_world.rows)


async def test_ac1_no_match_is_an_empty_list_with_zero_total(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(URL, params={"sol_min": "9999"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "per_page": 25, "total": 0}


async def test_ac2_without_rovers_all_five_rovers_are_listed(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(client, {})

    assert got == ids(multi_world.select())
    assert total == len(multi_world.rows)
    assert {multi_world.by_id(i).rover for i in got} == set(ALL_ROVERS)
    assert any(multi_world.by_id(i).rover == "ingenuity" for i in got)


@pytest.mark.parametrize(
    "variant",
    ["Spirit", "SPIRIT", "spirit,spirit", "Spirit,SPIRIT,spirit"],
    ids=["capitalized", "upper", "duplicate", "mixed-case-duplicates"],
)
async def test_ac2_rovers_case_insensitive_and_duplicates_collapse(
    client: httpx.AsyncClient, multi_world: Multi, variant: str
) -> None:
    reference = (await client.get(URL, params={"rovers": "spirit", "per_page": "100"})).json()
    response = await client.get(URL, params={"rovers": variant, "per_page": "100"})

    assert response.status_code == 200
    assert response.json() == reference
    assert reference["total"] == 30  # not doubled by the duplicate


async def test_ac2_duplicates_do_not_double_total_with_two_rovers(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"rovers": "spirit,opportunity,spirit"})).json()

    assert body["total"] == 60


async def test_ac2_rovers_order_in_the_query_does_not_change_the_result(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    first = (await client.get(URL, params={"rovers": "spirit,curiosity", "per_page": "50"})).json()
    second = (await client.get(URL, params={"rovers": "curiosity,spirit", "per_page": "50"})).json()

    assert first == second


async def test_ac2_ingenuity_alone_returns_ingenuity_photos(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(client, {"rovers": "ingenuity"})

    assert got == ids(multi_world.select(rovers=["ingenuity"]))
    assert total == 30


# --- AC-3: rovers parsing -------------------------------------------------------------------

_LONG = ",".join(["spirit"] * 29)  # 202 characters
_OK_LONG = ",".join(["spirit"] * 28)  # 195 characters, collapses to one rover


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "mars",
        "curiosity,mars",
        "curiosity,,spirit",
        "curiosity, spirit",
        " curiosity",
        ",spirit",
        "spirit,",
        ",",
        "curiosity;spirit",
        "curiositý",
        "спирит",
        "all",
        _LONG,
    ],
    ids=[
        "empty",
        "space",
        "unknown",
        "unknown-in-list",
        "empty-element",
        "space-after-comma",
        "leading-space",
        "leading-comma",
        "trailing-comma",
        "comma-only",
        "semicolon",
        "unicode-latin",
        "cyrillic",
        "all-keyword",
        "longer-than-200",
    ],
)
async def test_ac3_rovers_invalid_value_is_422_and_lists_valid_slugs(
    client: httpx.AsyncClient, multi_world: Multi, value: str
) -> None:
    response = await client.get(URL, params={"rovers": value})

    details = assert_validation_error(response, "rovers")
    message = " ".join(item["message"] for item in details if item["field"] == "rovers")
    for slug in ALL_ROVERS:
        assert slug in message, (slug, message)


async def test_ac3_long_but_valid_list_up_to_200_characters_is_accepted(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    assert len(_OK_LONG) <= 200 < len(_LONG)

    response = await client.get(URL, params={"rovers": _OK_LONG, "per_page": "1"})

    assert response.status_code == 200
    assert response.json()["total"] == 30


# --- AC-4: sort ----------------------------------------------------------------------------

ROVER_SETS = [
    pytest.param(None, id="all"),
    pytest.param(["curiosity", "perseverance"], id="two"),
    pytest.param(["spirit"], id="one"),
]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("rovers", ROVER_SETS)
async def test_ac4_sort_exact_order_with_id_tiebreak(
    client: httpx.AsyncClient, multi_world: Multi, sort: str, rovers: list[str] | None
) -> None:
    params = {"sort": sort}
    if rovers:
        params["rovers"] = ",".join(rovers)

    got, total = await get_all(client, params)

    expected = multi_world.select(rovers=rovers, sort=sort)
    assert got == ids(expected)
    assert total == len(expected)


async def test_ac4_default_sort_is_minus_sol(client: httpx.AsyncClient, multi_world: Multi) -> None:
    default, _ = await get_all(client, {})
    explicit, _ = await get_all(client, {"sort": "-sol"})

    assert default == explicit == ids(multi_world.select(sort="-sol"))


@pytest.mark.parametrize("sort", ["-taken_at", "taken_at"])
async def test_ac4_equal_taken_at_across_rovers_are_ordered_by_id_in_the_sort_direction(
    client: httpx.AsyncClient, multi_world: Multi, sort: str
) -> None:
    got, _ = await get_all(client, {"sort": sort})

    rows = [multi_world.by_id(i) for i in got]
    groups: dict[Any, list[int]] = {}
    for row in rows:
        groups.setdefault(row.taken_at, []).append(row.id)
    big = [
        group for group in groups.values() if len({multi_world.by_id(i).rover for i in group}) > 1
    ]
    assert big, "precondition: ties across rovers"
    for group in big:
        assert group == sorted(group, reverse=(sort == "-taken_at"))


async def test_ac4_sol_ascending_uses_sol_then_taken_at_then_id_ascending(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, _ = await get_all(client, {"sort": "sol", "rovers": "curiosity,spirit"})

    keys = [(r.sol, r.taken_at, r.id) for r in (multi_world.by_id(i) for i in got)]
    assert keys == sorted(keys)


@pytest.mark.parametrize("sort", ["-taken_at", "taken_at"])
async def test_ac4_taken_at_sort_ignores_sol_order(
    client: httpx.AsyncClient, multi_world: Multi, sort: str
) -> None:
    """The two date-trap photos (sol 105) are interleaved by time, not placed by sol."""
    got, _ = await get_all(client, {"sort": sort, "rovers": "curiosity"})

    times = [multi_world.by_id(i).taken_at for i in got]
    assert times == sorted(times, reverse=(sort == "-taken_at"))


@pytest.mark.parametrize(
    "value",
    ["asc", "desc", "id", "-id", "+sol", "sol,-taken_at", "taken", "", "--sol", "-earth_date"],
)
async def test_ac4_unknown_sort_is_422_with_the_allowed_values(
    client: httpx.AsyncClient, multi_world: Multi, value: str
) -> None:
    response = await client.get(URL, params={"sort": value})

    details = assert_validation_error(response, "sort")
    message = " ".join(item["message"] for item in details)
    for allowed in SORTS:
        assert allowed in message, (allowed, message)


# --- merge across pages (UNION ALL merge vs reference model) ---------------------------------


@pytest.mark.parametrize("per_page", [7, 10, 13], ids=["pp7", "pp10", "pp13"])
@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("rovers", ROVER_SETS)
async def test_merge_pages_concatenate_to_the_reference_without_gaps_or_duplicates(
    client: httpx.AsyncClient,
    multi_world: Multi,
    sort: str,
    rovers: list[str] | None,
    per_page: int,
) -> None:
    params = {"sort": sort}
    if rovers:
        params["rovers"] = ",".join(rovers)

    got, total = await get_all(client, params, per_page=per_page)

    expected = ids(multi_world.select(rovers=rovers, sort=sort))
    assert len(got) == len(set(got)), "duplicate across pages"
    assert got == expected
    assert total == len(expected)


@pytest.mark.parametrize("sort", SORTS)
async def test_merge_one_by_one_page_walk_on_a_tie_heavy_slice(
    client: httpx.AsyncClient, multi_world: Multi, sort: str
) -> None:
    params = {
        "sort": sort,
        "rovers": "curiosity,perseverance,spirit",
        "sol_min": "103",
        "sol_max": "104",
    }

    got, total = await get_all(client, params, per_page=1)

    expected = multi_world.select(
        rovers=["curiosity", "perseverance", "spirit"],
        sort=sort,
        where=lambda r: 103 <= r.sol <= 104,
    )
    assert got == ids(expected)
    assert total == len(expected) == 36


async def test_merge_page_past_the_end_is_empty_with_the_same_total(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"page": "50", "per_page": "100"})).json()

    assert body["items"] == []
    assert body["total"] == len(multi_world.rows)


# --- filters: table of cases checked against the reference, every sort ------------------------


def _case(
    name: str,
    params: dict[str, str],
    where: Callable[[Row], bool],
    rovers: list[str] | None = None,
) -> Any:
    return pytest.param(params, rovers, where, id=name)


CASES = [
    _case("none", {}, lambda r: True),
    _case("sol-range", {"sol_min": "101", "sol_max": "103"}, lambda r: 101 <= r.sol <= 103),
    _case("sol-single", {"sol": "102"}, lambda r: r.sol == 102),
    _case("sol-min-only", {"sol_min": "103"}, lambda r: r.sol >= 103),
    _case("sol-max-only", {"sol_max": "100"}, lambda r: r.sol <= 100),
    _case("sol-min-eq-max", {"sol_min": "104", "sol_max": "104"}, lambda r: r.sol == 104),
    _case(
        "date-range",
        {"date_min": "2021-03-02", "date_max": "2021-03-04"},
        lambda r: d("2021-03-02") <= r.earth_date <= d("2021-03-04"),
    ),
    _case("date-min-only", {"date_min": "2021-03-04"}, lambda r: r.earth_date >= d("2021-03-04")),
    _case("date-max-only", {"date_max": "2021-03-02"}, lambda r: r.earth_date <= d("2021-03-02")),
    _case(
        "earth-date-single", {"earth_date": "2021-03-03"}, lambda r: r.earth_date == d("2021-03-03")
    ),
    _case(
        "date-trap-behind",
        {"date_min": "2021-03-06", "date_max": "2021-03-06"},
        lambda r: r.earth_date == d("2021-03-06"),
    ),
    _case(
        "date-trap-ahead",
        {"date_min": "2021-03-07", "date_max": "2021-03-07"},
        lambda r: r.earth_date == d("2021-03-07"),
    ),
    _case(
        "camera-list",
        {"camera": "FHAZ,pancam,HELI_RTE"},
        lambda r: r.camera in {"FHAZ", "PANCAM", "HELI_RTE"},
    ),
    _case("sample-type", {"sample_type": "subframe"}, lambda r: r.sample_type == "subframe"),
    _case(
        "camera-sample-sol",
        {"camera": "navcam", "sample_type": "full", "sol_min": "101"},
        lambda r: r.camera == "NAVCAM" and r.sample_type == "full" and r.sol >= 101,
    ),
    _case(
        "date-and-camera-two-rovers",
        {"date_min": "2021-03-02", "camera": "PANCAM"},
        lambda r: r.earth_date >= d("2021-03-02") and r.camera == "PANCAM",
        ["spirit", "opportunity"],
    ),
    _case(
        "site-range",
        {"site_min": "11", "site_max": "12"},
        lambda r: r.site is not None and 11 <= r.site <= 12,
        ["curiosity"],
    ),
    _case(
        "site-min-only",
        {"site_min": "13"},
        lambda r: r.site is not None and r.site >= 13,
        ["perseverance"],
    ),
    _case(
        "site-drive",
        {"site": "12", "drive_from": "10", "drive_to": "30"},
        lambda r: r.site == 12 and r.drive is not None and 10 <= r.drive <= 30,
        ["perseverance"],
    ),
    _case(
        "site-range-and-sol",
        {"site_max": "12", "sol_min": "101"},
        lambda r: r.site is not None and r.site <= 12 and r.sol >= 101,
        ["curiosity"],
    ),
]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize(("params", "rovers", "where"), CASES)
async def test_filters_match_the_reference_for_every_sort_and_total_is_exact(
    client: httpx.AsyncClient,
    multi_world: Multi,
    params: dict[str, str],
    rovers: list[str] | None,
    where: Callable[[Row], bool],
    sort: str,
) -> None:
    query = {**params, "sort": sort}
    if rovers:
        query["rovers"] = ",".join(rovers)

    got, total = await get_all(client, query, per_page=13)

    expected = multi_world.select(rovers=rovers, sort=sort, where=where)
    assert got == ids(expected)
    assert total == len(expected)


# --- AC-5: dates and ranges -----------------------------------------------------------------


async def test_ac5_date_range_is_inclusive_on_both_ends(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, _ = await get_all(client, {"date_min": "2021-03-02", "date_max": "2021-03-03"})

    dates = {multi_world.by_id(i).earth_date for i in got}
    assert dates == {d("2021-03-02"), d("2021-03-03")}


async def test_ac5_date_filter_goes_by_earth_date_not_by_utc_date_of_taken_at(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    """Two sol-105 photos have earth_date one day off the UTC date of taken_at."""
    behind = next(r for r in multi_world.rows if r.source_id == "curiosity-trap-behind")
    ahead = next(r for r in multi_world.rows if r.source_id == "curiosity-trap-ahead")

    on_behind, _ = await get_all(client, {"earth_date": "2021-03-06", "rovers": "curiosity"})
    on_ahead, _ = await get_all(client, {"earth_date": "2021-03-07", "rovers": "curiosity"})

    assert behind.id in on_behind and behind.id not in on_ahead
    assert ahead.id in on_ahead and ahead.id not in on_behind


@pytest.mark.parametrize(
    "params",
    [
        {"date_min": "2021-03-05", "date_max": "2021-03-04"},
        {"sol_min": "103", "sol_max": "102"},
        {"rovers": "curiosity", "site_min": "13", "site_max": "12"},
    ],
    ids=["date", "sol", "site"],
)
async def test_ac5_min_greater_than_max_is_422(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert_validation_error(response, *params.keys())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("date_min", "2021-02-30"),
        ("date_max", "yesterday"),
        ("date_min", "2021-3-1"),
        ("sol_min", "abc"),
        ("sol_max", "1.5"),
    ],
    ids=["feb-30", "word", "no-padding", "sol-min-text", "sol-max-float"],
)
async def test_ac5_malformed_range_value_is_422(
    client: httpx.AsyncClient, multi_world: Multi, name: str, value: str
) -> None:
    response = await client.get(URL, params={name: value})

    assert_validation_error(response, name)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("sol_min", "-1"),
        ("sol_max", "-1"),
        ("sol_min", str(INT32_MAX + 1)),
        ("sol_max", str(INT32_MAX + 1)),
        ("sol_max", "9" * 40),
    ],
    ids=["min-negative", "max-negative", "min-over", "max-over", "max-huge"],
)
async def test_ac5_number_bounds_outside_0_to_int32_max_are_422(
    client: httpx.AsyncClient, multi_world: Multi, name: str, value: str
) -> None:
    response = await client.get(URL, params={name: value})

    assert_validation_error(response, name)


@pytest.mark.parametrize(
    ("name", "value", "total"),
    [("sol_min", "0", 152), ("sol_max", str(INT32_MAX), 152), ("sol_min", str(INT32_MAX), 0)],
    ids=["min-zero", "max-int32", "min-int32"],
)
async def test_ac5_number_bounds_inclusive_limits_are_accepted(
    client: httpx.AsyncClient, multi_world: Multi, name: str, value: str, total: int
) -> None:
    response = await client.get(URL, params={name: value, "per_page": "1"})

    assert response.status_code == 200
    assert response.json()["total"] == total


@pytest.mark.parametrize("name", ["site_min", "site_max"])
async def test_ac5_site_bounds_outside_range_are_422(
    client: httpx.AsyncClient, multi_world: Multi, name: str
) -> None:
    for value in ("-1", str(INT32_MAX + 1)):
        response = await client.get(URL, params={"rovers": "curiosity", name: value})

        assert_validation_error(response, name)


async def test_ac5_site_bounds_int32_max_is_accepted(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(
        URL, params={"rovers": "curiosity", "site_min": "0", "site_max": str(INT32_MAX)}
    )

    assert response.status_code == 200
    assert response.json()["total"] == len(
        multi_world.select(rovers=["curiosity"], where=lambda r: r.site is not None)
    )


# --- AC-6: sols and dates do not mix ---------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"sol": "101", "sol_min": "100"},
        {"sol": "101", "sol_max": "102"},
        {"sol": "101", "sol_min": "100", "sol_max": "102"},
        {"earth_date": "2021-03-02", "date_min": "2021-03-01"},
        {"earth_date": "2021-03-02", "date_max": "2021-03-03"},
        {"sol": "101", "earth_date": "2021-03-02"},
        {"sol": "101", "date_min": "2021-03-01"},
        {"sol": "101", "date_max": "2021-03-03"},
        {"sol_min": "100", "earth_date": "2021-03-02"},
        {"sol_max": "104", "earth_date": "2021-03-02"},
        {"sol_min": "100", "date_min": "2021-03-01"},
        {"sol_max": "104", "date_max": "2021-03-05"},
        {"sol_min": "100", "sol_max": "104", "date_min": "2021-03-01", "date_max": "2021-03-05"},
    ],
    ids=[
        "sol+sol_min",
        "sol+sol_max",
        "sol+both",
        "earth_date+date_min",
        "earth_date+date_max",
        "sol+earth_date",
        "sol+date_min",
        "sol+date_max",
        "sol_min+earth_date",
        "sol_max+earth_date",
        "sol_min+date_min",
        "sol_max+date_max",
        "ranges-both",
    ],
)
async def test_ac6_incompatible_filter_combinations_are_422(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert_validation_error(response, *params.keys())


# --- AC-7: camera list ------------------------------------------------------------------------


async def test_ac7_camera_list_selects_each_rovers_own_cameras(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    params = {"rovers": "curiosity,spirit", "camera": "NAV_LEFT_B,navcam"}

    got, total = await get_all(client, params)

    expected = multi_world.select(
        rovers=["curiosity", "spirit"], where=lambda r: r.camera in {"NAV_LEFT_B", "NAVCAM"}
    )
    assert got == ids(expected)
    assert total == len(expected) == 30
    rows = [multi_world.by_id(i) for i in got]
    assert {r.camera for r in rows if r.rover == "curiosity"} == {"NAV_LEFT_B"}
    assert {r.camera for r in rows if r.rover == "spirit"} == {"NAVCAM"}


async def test_ac7_codes_valid_for_only_one_of_the_rovers_leave_the_other_with_its_own_match(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    """PANCAM exists for spirit only (not curiosity), NAV_LEFT_B for curiosity only."""
    got, _ = await get_all(client, {"rovers": "curiosity,spirit", "camera": "NAV_LEFT_B,PANCAM"})

    rows = [multi_world.by_id(i) for i in got]
    assert {(r.rover, r.camera) for r in rows} == {
        ("curiosity", "NAV_LEFT_B"),
        ("spirit", "PANCAM"),
    }


async def test_ac7_camera_codes_are_case_insensitive(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    lower, total_lower = await get_all(client, {"rovers": "curiosity", "camera": "nav_left_b"})
    upper, total_upper = await get_all(client, {"rovers": "curiosity", "camera": "NAV_LEFT_B"})

    assert lower == upper
    assert total_lower == total_upper == 15


async def test_ac7_ten_codes_are_accepted(client: httpx.AsyncClient, multi_world: Multi) -> None:
    codes = "FHAZ,RHAZ,MAST,CHEMCAM,MAHLI,MARDI,NAVCAM,NAV_LEFT_B,NAVCAM_LEFT,MCZ_LEFT"
    assert len(codes.split(",")) == 10

    response = await client.get(URL, params={"camera": codes, "per_page": "1"})

    assert response.status_code == 200
    expected = multi_world.select(where=lambda r: r.camera in set(codes.split(",")))
    assert response.json()["total"] == len(expected)


async def test_ac7_more_than_ten_codes_is_422(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    codes = "FHAZ,RHAZ,MAST,CHEMCAM,MAHLI,MARDI,NAVCAM,NAV_LEFT_B,NAVCAM_LEFT,MCZ_LEFT,HELI_NAV"
    assert len(codes.split(",")) == 11

    response = await client.get(URL, params={"camera": codes})

    assert_validation_error(response, "camera")


@pytest.mark.parametrize(
    ("params", "named"),
    [
        ({"rovers": "curiosity", "camera": "PANCAM"}, ["curiosity"]),
        ({"rovers": "curiosity,spirit", "camera": "HELI_NAV"}, ["curiosity", "spirit"]),
        ({"rovers": "curiosity,spirit", "camera": "MCZ_LEFT"}, ["curiosity", "spirit"]),
        ({"camera": "NO_SUCH_CAMERA"}, list(ALL_ROVERS)),
        ({"rovers": "curiosity", "camera": "NAV_LEFT_B,NO_SUCH_CAMERA"}, ["curiosity"]),
    ],
    ids=[
        "unknown-for-the-only-rover",
        "unknown-for-both",
        "known-only-to-an-unselected-rover",
        "unknown-everywhere",
        "one-bad-code-in-a-list",
    ],
)
async def test_ac7_code_unknown_to_every_selected_rover_is_422_with_codes_per_rover(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str], named: list[str]
) -> None:
    response = await client.get(URL, params=params)

    details = assert_validation_error(response, "camera")
    text = " ".join(f"{item['field']} {item['message']}" for item in details)
    for rover in named:
        assert rover in text, (rover, details)


@pytest.mark.parametrize(
    "value", ["FHAZ,,NAVCAM", ",FHAZ", "FHAZ,", ""], ids=["middle", "leading", "trailing", "empty"]
)
async def test_ac7_empty_camera_element_is_422(
    client: httpx.AsyncClient, multi_world: Multi, value: str
) -> None:
    response = await client.get(URL, params={"camera": value})

    assert_validation_error(response, "camera")


async def test_ac7_sample_type_filter_and_unknown_value(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(client, {"sample_type": "downsampled"})
    bad = await client.get(URL, params={"sample_type": "huge"})

    expected = multi_world.select(where=lambda r: r.sample_type == "downsampled")
    assert got == ids(expected)
    assert total == len(expected)
    assert_validation_error(bad, "sample_type")


# --- AC-8: site -------------------------------------------------------------------------------


async def test_ac8_site_range_with_one_rover_skips_photos_without_site(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(client, {"rovers": "curiosity", "site_min": "10", "site_max": "14"})

    expected = multi_world.select(rovers=["curiosity"], where=lambda r: r.site is not None)
    assert got == ids(expected)
    assert total == len(expected) == 15
    assert all(multi_world.by_id(i).site is not None for i in got)


async def test_ac8_site_range_does_not_leak_other_rovers_with_the_same_site(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, _ = await get_all(client, {"rovers": "perseverance", "site_min": "10", "site_max": "14"})

    assert {multi_world.by_id(i).rover for i in got} == {"perseverance"}


@pytest.mark.parametrize(
    "params",
    [
        {"site_min": "10"},
        {"site_max": "14"},
        {"site": "12"},
        {"site": "12", "drive_from": "0"},
        {"rovers": "curiosity,perseverance", "site_min": "10"},
        {"rovers": "curiosity,perseverance", "site_max": "14"},
        {"rovers": "curiosity,perseverance", "site": "12"},
    ],
    ids=[
        "min-no-rovers",
        "max-no-rovers",
        "site-no-rovers",
        "site-drive-no-rovers",
        "min-two-rovers",
        "max-two-rovers",
        "site-two-rovers",
    ],
)
async def test_ac8_site_filters_need_exactly_one_rover(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert_validation_error(response, *params.keys())


async def test_ac8_duplicated_rover_counts_as_exactly_one_rover(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(
        URL, params={"rovers": "curiosity,Curiosity", "site_min": "10", "per_page": "1"}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 15


async def test_ac8_site_with_site_range_is_422(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    for extra in ({"site_min": "10"}, {"site_max": "14"}, {"site_min": "10", "site_max": "14"}):
        response = await client.get(URL, params={"rovers": "curiosity", "site": "12", **extra})

        assert_validation_error(response, "site", *extra.keys())


async def test_ac8_drive_bounds_without_site_are_422(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    for name in ("drive_from", "drive_to"):
        response = await client.get(URL, params={"rovers": "curiosity", name: "10"})

        # "site" is the missing dependency, so naming it is legitimate here (pre-stage behaviour).
    assert_validation_error(response, name, "site")


async def test_ac8_drive_from_greater_than_drive_to_is_422(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(
        URL,
        params={"rovers": "curiosity", "site": "12", "drive_from": "30", "drive_to": "10"},
    )

    assert_validation_error(response, "drive_from", "drive_to")


# --- AC-9: result window ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(100, 100), (10000, 1), (1, 100), (400, 25), (2000, 5), (1, 1)],
    ids=["100x100", "10000x1", "1x100", "400x25", "2000x5", "1x1"],
)
async def test_ac9_page_times_per_page_up_to_10000_is_200(
    client: httpx.AsyncClient, multi_world: Multi, page: int, per_page: int
) -> None:
    assert page * per_page <= 10_000

    response = await client.get(URL, params={"page": page, "per_page": per_page})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(multi_world.rows)
    assert (body["page"], body["per_page"]) == (page, per_page)


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(101, 100), (10001, 1), (401, 25), (2001, 5), (1_000_000, 100), (1_000_000, 1)],
    ids=["101x100", "10001x1", "401x25", "2001x5", "1e6x100", "1e6x1"],
)
async def test_ac9_page_times_per_page_over_10000_is_422_on_page(
    client: httpx.AsyncClient, multi_world: Multi, page: int, per_page: int
) -> None:
    assert page * per_page > 10_000

    response = await client.get(URL, params={"page": page, "per_page": per_page})

    assert_validation_error(response, "page")


@pytest.mark.parametrize("sort", SORTS)
async def test_ac9_the_window_applies_to_every_sort_and_to_filtered_queries(
    client: httpx.AsyncClient, multi_world: Multi, sort: str
) -> None:
    ok = await client.get(
        URL, params={"sort": sort, "rovers": "spirit", "page": 100, "per_page": 100}
    )
    over = await client.get(
        URL, params={"sort": sort, "rovers": "spirit", "page": 101, "per_page": 100}
    )

    assert ok.status_code == 200
    assert_validation_error(over, "page")


@pytest.mark.parametrize(
    ("name", "value"),
    [("page", "0"), ("page", "-1"), ("page", "x"), ("per_page", "0"), ("per_page", "101")],
)
async def test_ac9_invalid_page_and_per_page_are_422(
    client: httpx.AsyncClient, multi_world: Multi, name: str, value: str
) -> None:
    response = await client.get(URL, params={name: value})

    assert_validation_error(response, name)


# --- misc -------------------------------------------------------------------------------------


async def test_unknown_query_params_are_ignored_on_the_cross_rover_list(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    body = (await client.get(URL, params={"api_key": "DEMO_KEY", "foo": "bar"})).json()

    assert body["total"] == len(multi_world.rows)


# --- site range together with a camera list (was a 500) ---------------------------------------


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize(
    "camera",
    ["FHAZ,NAV_LEFT_B", "fhaz,nav_left_b,MAST"],
    ids=["two-codes", "three-codes-one-foreign"],
)
@pytest.mark.parametrize(
    "site", [{"site_min": "11", "site_max": "13"}, {"site_min": "12"}, {"site_max": "11"}]
)
async def test_site_range_with_a_camera_list_matches_the_reference(
    client: httpx.AsyncClient, multi_world: Multi, site: dict[str, str], camera: str, sort: str
) -> None:
    got, total = await get_all(
        client, {"rovers": "curiosity", "camera": camera, "sort": sort, **site}, per_page=9
    )

    codes = {c.upper() for c in camera.split(",")}
    lo, hi = int(site.get("site_min", 0)), int(site.get("site_max", INT32_MAX))
    expected = multi_world.select(
        rovers=["curiosity"],
        sort=sort,
        where=lambda r: r.camera in codes and r.site is not None and lo <= r.site <= hi,
    )
    assert expected
    assert got == ids(expected)
    assert total == len(expected)


async def test_site_exact_with_a_camera_list_and_drive_bounds(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    got, total = await get_all(
        client,
        {
            "rovers": "perseverance",
            "camera": "NAVCAM_LEFT,MCZ_LEFT",
            "site": "12",
            "drive_from": "10",
            "drive_to": "40",
        },
    )

    expected = multi_world.select(
        rovers=["perseverance"],
        where=lambda r: r.site == 12 and r.drive is not None and 10 <= r.drive <= 40,
    )
    assert got == ids(expected)
    assert total == len(expected) > 0


# --- total stays exact for sol/date ranges with and without other filters ----------------------


@pytest.mark.parametrize(
    ("params", "where"),
    [
        ({"sol_min": "100", "sol_max": "104"}, lambda r: 100 <= r.sol <= 104),
        ({"sol_min": "104"}, lambda r: r.sol >= 104),
        (
            {"sol_max": "101", "sample_type": "full"},
            lambda r: r.sol <= 101 and r.sample_type == "full",
        ),
        (
            {"date_min": "2021-03-03", "date_max": "2021-03-05"},
            lambda r: d("2021-03-03") <= r.earth_date <= d("2021-03-05"),
        ),
        (
            {"date_min": "2021-03-06", "camera": "FHAZ,HELI_NAV"},
            lambda r: r.earth_date >= d("2021-03-06") and r.camera in {"FHAZ", "HELI_NAV"},
        ),
    ],
    ids=["all-sols", "tail", "sols-and-sample", "dates", "dates-and-cameras"],
)
@pytest.mark.parametrize("sort", ["-sol", "-taken_at"])
async def test_total_is_exact_for_ranges_whatever_path_the_service_takes(
    client: httpx.AsyncClient,
    multi_world: Multi,
    params: dict[str, str],
    where: Callable[[Row], bool],
    sort: str,
) -> None:
    body = (await client.get(URL, params={**params, "sort": sort, "per_page": "3"})).json()

    assert body["total"] == len(multi_world.select(sort=sort, where=where))
