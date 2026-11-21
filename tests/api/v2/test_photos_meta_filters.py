"""Stage 18B-2 filters on GET /api/v2/photos and /rovers/{rover}/photos.

`local_time_min` / `local_time_max`, `golden_hour`, `min_width` / `min_height`
(docs/API.md, docs/proposals/stage-18b-filters.md AC-17 .. AC-20). Expected results come from the
reference model in `_meta.py`; every test name carries its acceptance criterion.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.api.v2._meta import (
    ALL_ROVERS,
    LMST_ROVERS,
    MER,
    SORTS,
    MetaWorld,
    MRow,
    ids,
    in_window,
    is_golden,
    min_size,
)
from tests.api.v2.conftest import V2, assert_validation_error

pytestmark = pytest.mark.api

URL = f"{V2}/photos"
Where = Callable[[MRow], bool]


def rover_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/photos"


async def walk(
    client: httpx.AsyncClient, params: dict[str, str], *, path: str = URL, per_page: int = 100
) -> tuple[list[int], int]:
    """Walk every page; `total` must not change; return (ids in order, total)."""
    got: list[int] = []
    total: int | None = None
    page = 1
    while True:
        response = await client.get(path, params={**params, "page": page, "per_page": per_page})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["page"] == page
        if total is None:
            total = body["total"]
        assert body["total"] == total, "total must not change between pages"
        got += [item["id"] for item in body["items"]]
        if len(body["items"]) < per_page:
            return got, total
        page += 1
        assert page < 500, "runaway pagination"


async def check(
    client: httpx.AsyncClient,
    world: MetaWorld,
    params: dict[str, str],
    where: Where,
    *,
    rovers: tuple[str, ...] | None = None,
    sort: str = "-sol",
    path: str = URL,
    per_page: int = 100,
) -> list[MRow]:
    """The API result equals the reference model: ids, order and total."""
    expected = world.select(rovers=rovers, sort=sort, where=where)
    full = {**params, **({"sort": sort} if sort != "-sol" else {})}
    got, total = await walk(client, full, path=path, per_page=per_page)
    assert got == ids(expected)
    assert total == len(expected)
    return expected


def by_id(world: MetaWorld, photo_ids: list[int]) -> list[MRow]:
    index = {r.id: r for r in world.rows}
    return [index[i] for i in photo_ids]


# --- AC-17: local time window ------------------------------------------------------------

WINDOWS = [
    ("17:00", "18:00"),
    ("05:30", "07:00"),
    ("12:00", "12:01"),
    ("00:00", "23:59"),
    ("06:59", "07:01"),
    ("23:00", "01:00"),
    ("23:59", "00:00"),
    ("18:00", "17:00"),
    ("17:00", "00:00"),
    ("00:00", "00:01"),
]
WINDOW_IDS = [
    "evening-hour",
    "morning-golden",
    "noon-minute",
    "almost-all-day",
    "around-0700",
    "wrap-23-01",
    "wrap-last-minute",
    "wrap-18-17",
    "evening-to-midnight",
    "first-minute",
]


@pytest.mark.parametrize(("lo", "hi"), WINDOWS, ids=WINDOW_IDS)
async def test_ac17_window_all_rovers_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, lo: str, hi: str
) -> None:
    expected = await check(
        client,
        meta_world,
        {"local_time_min": lo, "local_time_max": hi},
        lambda r: in_window(r, lo, hi),
    )

    assert expected  # a vacuous window would prove nothing


async def test_ac17_window_17_to_18_min_inclusive_max_exclusive(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, total = await walk(client, {"local_time_min": "17:00", "local_time_max": "18:00"})

    # 17:00:00 is in, 17:59:59 is in, 18:00:00 and 16:59:59 are out.
    assert {r.local_time for r in by_id(meta_world, got)} == {"17:00:00", "17:59:59"}
    assert total == len(got) == 5 * 2 * 2


async def test_ac17_photo_exactly_at_max_is_not_in_the_window(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, _ = await walk(client, {"local_time_min": "17:00", "local_time_max": "18:00"})

    assert not [r for r in by_id(meta_world, got) if r.local_time == "18:00:00"]


async def test_ac17_fraction_of_second_is_truncated_not_rounded(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    """05:29:59.999 stays 05:29:59: it is outside `05:30-07:00`; 06:59:59.999 stays inside."""
    got, _ = await walk(client, {"local_time_min": "05:30", "local_time_max": "07:00"})

    assert {r.local_time for r in by_id(meta_world, got)} == {"05:30:00", "06:59:59"}


async def test_ac17_wrap_through_midnight_takes_both_sides(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, _ = await walk(client, {"local_time_min": "23:00", "local_time_max": "01:00"})

    assert {r.local_time for r in by_id(meta_world, got)} == {"23:59:59", "00:00:00"}


async def test_ac17_max_midnight_with_min_is_the_whole_evening(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, _ = await walk(client, {"local_time_min": "17:00", "local_time_max": "00:00"})

    assert {r.local_time for r in by_id(meta_world, got)} == {
        "17:00:00",
        "17:59:59",
        "18:00:00",
        "18:29:59",
        "18:30:00",
        "23:59:59",
    }


@pytest.mark.parametrize(
    ("params", "where"),
    [
        ({"local_time_min": "17:00"}, lambda r: in_window(r, "17:00", None)),
        ({"local_time_max": "07:00"}, lambda r: in_window(r, None, "07:00")),
        ({"local_time_min": "00:00"}, lambda r: r.local_time is not None),
        ({"local_time_max": "23:59"}, lambda r: in_window(r, None, "23:59")),
    ],
    ids=["min-only", "max-only", "min-midnight-keeps-all-with-time", "max-last-minute"],
)
async def test_ac17_open_ended_window_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, params: dict[str, str], where: Where
) -> None:
    expected = await check(client, meta_world, params, where)

    assert expected


async def test_ac17_max_midnight_alone_matches_nothing(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    """`t < 00:00` is empty: a valid request with a zero total, not an error."""
    response = await client.get(URL, params={"local_time_max": "00:00"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_ac17_photos_without_local_time_never_match(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, total = await walk(client, {"local_time_min": "00:00"})

    rows = by_id(meta_world, got)
    assert rows and all(r.local_time is not None for r in rows)
    without = [r for r in meta_world.rows if r.local_time is None]
    assert len(without) == 5 * 2  # the dataset does contain such photos
    assert total == len(meta_world.rows) - len(without)


async def test_ac17_lmst_and_mer_ltst_share_one_window_as_is(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    """The window is applied to every scale as written (curiosity LMST, MER LTST)."""
    got, _ = await walk(client, {"local_time_min": "12:00", "local_time_max": "12:01"})

    rows = by_id(meta_world, got)
    assert {r.rover for r in rows} == set(ALL_ROVERS)
    assert {r.rover for r in rows} >= set(MER) | set(LMST_ROVERS)
    assert {r.local_time for r in rows} == {"12:00:00"}


@pytest.mark.parametrize("rovers", [("spirit", "opportunity"), ("curiosity", "spirit")], ids=str)
async def test_ac17_multi_rover_query_with_mixed_scales(
    client: httpx.AsyncClient, meta_world: MetaWorld, rovers: tuple[str, ...]
) -> None:
    await check(
        client,
        meta_world,
        {"rovers": ",".join(rovers), "local_time_min": "17:00", "local_time_max": "18:30"},
        lambda r: in_window(r, "17:00", "18:30"),
        rovers=rovers,
    )


BAD_TIMES = [
    "24:00",
    "7:5",
    "12:60",
    "abc",
    "7:00",
    "17:00:00",
    "17:60",
    "1700",
    "25:00",
    "17-00",
    "-1:00",
    "17:0",
]


@pytest.mark.parametrize("param", ["local_time_min", "local_time_max"])
@pytest.mark.parametrize("value", BAD_TIMES, ids=BAD_TIMES)
async def test_ac17_bad_time_format_is_422_on_the_named_field(
    client: httpx.AsyncClient, meta_world: MetaWorld, param: str, value: str
) -> None:
    response = await client.get(URL, params={param: value})

    assert_validation_error(response, param)


@pytest.mark.parametrize("value", ["24:00", "7:5", "12:60", "abc"])
async def test_ac17_bad_time_format_is_422_on_the_rover_endpoint_too(
    client: httpx.AsyncClient, meta_world: MetaWorld, value: str
) -> None:
    response = await client.get(
        rover_url("perseverance"), params={"local_time_min": "01:00", "local_time_max": value}
    )

    assert_validation_error(response, "local_time_max")


@pytest.mark.parametrize("clock", ["12:00", "00:00", "23:59"])
@pytest.mark.parametrize("path", [URL, rover_url("curiosity")], ids=["photos", "rover-photos"])
async def test_ac17_min_equals_max_is_422(
    client: httpx.AsyncClient, meta_world: MetaWorld, path: str, clock: str
) -> None:
    response = await client.get(path, params={"local_time_min": clock, "local_time_max": clock})

    assert_validation_error(response, "local_time_min", "local_time_max")


# --- AC-17 / AC-18 / AC-19 on /rovers/{rover}/photos ---------------------------------------


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_ac17_rover_endpoint_window_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    expected = await check(
        client,
        meta_world,
        {"local_time_min": "17:00", "local_time_max": "18:30"},
        lambda r: in_window(r, "17:00", "18:30"),
        rovers=(rover,),
        path=rover_url(rover),
    )

    assert len(expected) == 2 * 4


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_ac18_rover_endpoint_golden_hour_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    expected = await check(
        client,
        meta_world,
        {"golden_hour": "true"},
        is_golden,
        rovers=(rover,),
        path=rover_url(rover),
    )

    assert len(expected) == 2 * 6


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_ac19_rover_endpoint_min_size_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    await check(
        client,
        meta_world,
        {"min_width": "1920", "min_height": "1200"},
        lambda r: min_size(r, 1920, 1200),
        rovers=(rover,),
        path=rover_url(rover),
    )


async def test_ac17_rover_endpoint_total_and_page_slice(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    expected = meta_world.select(rovers=("perseverance",), where=lambda r: is_golden(r))
    response = await client.get(
        rover_url("perseverance"), params={"golden_hour": "1", "page": 2, "per_page": 5}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == len(expected) == 12
    assert [i["id"] for i in body["items"]] == ids(expected)[5:10]
    assert body["nearest_sols"] is None


# --- AC-18: golden hour ------------------------------------------------------------------


async def test_ac18_golden_hour_true_boundaries_are_0530_0700_1700_1830(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, total = await walk(client, {"golden_hour": "true"})

    assert {r.local_time for r in by_id(meta_world, got)} == {
        "05:30:00",  # in: lower bound of the morning window
        "06:59:59",
        "17:00:00",  # in: lower bound of the evening window
        "17:59:59",
        "18:00:00",
        "18:29:59",
    }
    # Out: 05:29:59, 07:00:00 (upper bound, exclusive), 16:59:59, 18:30:00 (exclusive).
    assert total == 5 * 2 * 6


@pytest.mark.parametrize("value", ["true", "1"])
async def test_ac18_golden_hour_true_spellings_match_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, value: str
) -> None:
    await check(client, meta_world, {"golden_hour": value}, is_golden)


@pytest.mark.parametrize("value", ["false", "0"])
async def test_ac18_golden_hour_false_spellings_do_not_filter(
    client: httpx.AsyncClient, meta_world: MetaWorld, value: str
) -> None:
    expected = await check(client, meta_world, {"golden_hour": value}, lambda r: True)

    assert any(r.local_time is None for r in expected)  # photos without time stay


async def test_ac18_golden_hour_absent_equals_false(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    default = await walk(client, {})
    explicit = await walk(client, {"golden_hour": "false"})

    assert default == explicit


@pytest.mark.parametrize("value", ["yes", "on", "2", "maybe", "TRUE1", "-1"])
async def test_ac18_golden_hour_other_values_are_422(
    client: httpx.AsyncClient, meta_world: MetaWorld, value: str
) -> None:
    response = await client.get(URL, params={"golden_hour": value})

    assert_validation_error(response, "golden_hour")


@pytest.mark.parametrize(
    "extra",
    [
        {"local_time_min": "17:00"},
        {"local_time_max": "18:00"},
        {"local_time_min": "17:00", "local_time_max": "18:00"},
    ],
    ids=["min", "max", "both"],
)
@pytest.mark.parametrize("path", [URL, rover_url("perseverance")], ids=["photos", "rover-photos"])
async def test_ac18_golden_hour_true_with_local_time_is_422(
    client: httpx.AsyncClient, meta_world: MetaWorld, path: str, extra: dict[str, str]
) -> None:
    response = await client.get(path, params={"golden_hour": "true", **extra})

    assert_validation_error(response, "golden_hour", "local_time_min", "local_time_max")


@pytest.mark.parametrize("value", ["false", "0"])
@pytest.mark.parametrize("path", [URL, rover_url("perseverance")], ids=["photos", "rover-photos"])
async def test_ac18_golden_hour_false_with_local_time_is_a_no_op(
    client: httpx.AsyncClient, meta_world: MetaWorld, path: str, value: str
) -> None:
    params = {"local_time_min": "17:00", "local_time_max": "18:30"}
    rovers = ("perseverance",) if path != URL else None

    await check(
        client,
        meta_world,
        {**params, "golden_hour": value},
        lambda r: in_window(r, "17:00", "18:30"),
        rovers=rovers,
        path=path,
    )


@pytest.mark.parametrize("sort", SORTS)
async def test_ac18_golden_hour_with_every_sort_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, sort: str
) -> None:
    await check(client, meta_world, {"golden_hour": "true"}, is_golden, sort=sort)


# --- AC-19: frame size -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "where"),
    [
        ({"min_width": "1920"}, lambda r: min_size(r, 1920)),
        ({"min_height": "1200"}, lambda r: min_size(r, None, 1200)),
        ({"min_width": "1920", "min_height": "1200"}, lambda r: min_size(r, 1920, 1200)),
        ({"min_width": "1"}, lambda r: min_size(r, 1)),
        ({"min_height": "1"}, lambda r: min_size(r, None, 1)),
        ({"min_width": "1919"}, lambda r: min_size(r, 1919)),
        ({"min_width": "1921"}, lambda r: min_size(r, 1921)),
        ({"min_width": "32767"}, lambda r: min_size(r, 32767)),
        ({"min_width": "32767", "min_height": "32767"}, lambda r: min_size(r, 32767, 32767)),
        ({"min_width": "640", "min_height": "1200"}, lambda r: min_size(r, 640, 1200)),
    ],
    ids=[
        "width-1920",
        "height-1200",
        "both",
        "width-1",
        "height-1",
        "width-1919",
        "width-1921",
        "width-max",
        "both-max",
        "width-640-height-1200",
    ],
)
async def test_ac19_min_size_matches_the_reference_model(
    client: httpx.AsyncClient, meta_world: MetaWorld, params: dict[str, str], where: Where
) -> None:
    expected = await check(client, meta_world, params, where)

    assert expected
    assert all(r.rover != "curiosity" for r in expected)


async def test_ac19_min_width_is_a_lower_bound_on_the_returned_widths(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    """Independent of the model: every returned body has width >= the bound."""
    response = await client.get(URL, params={"min_width": "1920", "per_page": "100"})

    items = response.json()["items"]
    assert items
    assert all(i["width"] is not None and i["width"] >= 1920 for i in items)
    assert all(i["rover"] != "curiosity" for i in items)


async def test_ac19_min_height_is_a_lower_bound_on_the_returned_heights(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    response = await client.get(URL, params={"min_height": "1200", "per_page": "100"})

    items = response.json()["items"]
    assert items
    assert all(i["height"] is not None and i["height"] >= 1200 for i in items)


async def test_ac19_curiosity_is_excluded_even_when_extra_has_a_dimension(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    response = await client.get(rover_url("curiosity"), params={"min_width": "1"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_ac19_size_filter_with_curiosity_in_rovers_drops_only_curiosity(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    await check(
        client,
        meta_world,
        {"rovers": "curiosity,perseverance", "min_width": "1"},
        lambda r: min_size(r, 1),
        rovers=("curiosity", "perseverance"),
    )


async def test_ac19_size_above_32767_in_extra_is_not_found_by_any_bound(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    """(32768, 100) and (100, 32768): the column stays NULL, so min_width=1 skips both."""
    got, _ = await walk(client, {"min_width": "1", "rovers": "perseverance"})

    traps = {r.id for r in meta_world.rows if r.sol == 102}
    assert len(traps) == 2
    assert not traps & set(got)
    got_h, _ = await walk(client, {"min_height": "1", "rovers": "perseverance"})
    assert not traps & set(got_h)


@pytest.mark.parametrize("param", ["min_width", "min_height"])
@pytest.mark.parametrize(
    "value",
    ["0", "-1", "32768", "99999999999", "abc", "1.5", "1e3", "0x10"],
    ids=["zero", "negative", "over-max", "huge", "letters", "decimal", "exponent", "hex"],
)
async def test_ac19_min_size_out_of_range_or_not_an_integer_is_422(
    client: httpx.AsyncClient, meta_world: MetaWorld, param: str, value: str
) -> None:
    response = await client.get(URL, params={param: value})

    assert_validation_error(response, param)


@pytest.mark.parametrize("param", ["min_width", "min_height"])
async def test_ac19_min_size_422_on_the_rover_endpoint_too(
    client: httpx.AsyncClient, meta_world: MetaWorld, param: str
) -> None:
    response = await client.get(rover_url("perseverance"), params={param: "32768"})

    assert_validation_error(response, param)


@pytest.mark.parametrize("param", ["min_width", "min_height"])
@pytest.mark.parametrize("value", ["1", "32767"])
async def test_ac19_bounds_1_and_32767_are_accepted(
    client: httpx.AsyncClient, meta_world: MetaWorld, param: str, value: str
) -> None:
    response = await client.get(URL, params={param: value})

    assert response.status_code == 200


# --- combinations ------------------------------------------------------------------------

COMBOS: list[tuple[dict[str, str], tuple[str, ...] | None, Where]] = [
    (
        {"golden_hour": "true", "min_width": "1920"},
        None,
        lambda r: is_golden(r) and min_size(r, 1920),
    ),
    (
        {"local_time_min": "17:00", "local_time_max": "18:30", "min_height": "1200"},
        None,
        lambda r: in_window(r, "17:00", "18:30") and min_size(r, None, 1200),
    ),
    (
        {"rovers": "perseverance,spirit", "golden_hour": "1", "sol_min": "101"},
        ("perseverance", "spirit"),
        lambda r: is_golden(r) and r.sol >= 101,
    ),
    (
        {"sol_min": "100", "sol_max": "100", "local_time_min": "23:00", "local_time_max": "01:00"},
        None,
        lambda r: r.sol == 100 and in_window(r, "23:00", "01:00"),
    ),
    (
        {
            "rovers": "curiosity,perseverance",
            "camera": "NAV_LEFT_B,MCZ_LEFT",
            "golden_hour": "true",
        },
        ("curiosity", "perseverance"),
        lambda r: is_golden(r) and r.camera in ("NAV_LEFT_B", "MCZ_LEFT"),
    ),
    (
        {"date_min": "2021-03-02", "date_max": "2021-03-02", "min_width": "640"},
        None,
        lambda r: r.earth_date.isoformat() == "2021-03-02" and min_size(r, 640),
    ),
    (
        {"rovers": "ingenuity", "local_time_max": "06:00", "min_width": "1"},
        ("ingenuity",),
        lambda r: in_window(r, None, "06:00") and min_size(r, 1),
    ),
    (
        {"golden_hour": "true", "min_width": "1920", "min_height": "1200", "sol": "101"},
        None,
        lambda r: is_golden(r) and min_size(r, 1920, 1200) and r.sol == 101,
    ),
]
COMBO_IDS = [
    "golden-and-width",
    "window-and-height",
    "two-rovers-golden-sol-min",
    "sol-range-and-wrap",
    "cameras-list-and-golden",
    "date-and-width",
    "ingenuity-open-window-and-size",
    "golden-both-sizes-sol",
]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize(("params", "rovers", "where"), COMBOS, ids=COMBO_IDS)
async def test_ac17_filters_combine_with_rovers_sols_dates_camera_and_sort(
    client: httpx.AsyncClient,
    meta_world: MetaWorld,
    params: dict[str, str],
    rovers: tuple[str, ...] | None,
    where: Where,
    sort: str,
) -> None:
    expected = await check(client, meta_world, params, where, rovers=rovers, sort=sort)

    assert expected, "a combination with an empty result proves nothing"


@pytest.mark.parametrize("sort", SORTS)
async def test_ac17_paging_across_pages_has_no_gaps_or_duplicates(
    client: httpx.AsyncClient, meta_world: MetaWorld, sort: str
) -> None:
    """Small pages over several rovers (cross-rover ties are broken by id)."""
    expected = await check(
        client,
        meta_world,
        {"local_time_min": "12:00", "local_time_max": "18:30"},
        lambda r: in_window(r, "12:00", "18:30"),
        sort=sort,
        per_page=7,
    )

    assert len(expected) > 7 * 3
    assert len(set(ids(expected))) == len(expected)


async def test_ac17_total_is_the_same_on_every_page_and_page_past_the_end_is_empty(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    expected = meta_world.select(where=is_golden)
    last_page = len(expected) // 10 + 1

    body = (
        await client.get(URL, params={"golden_hour": "true", "page": last_page + 1, "per_page": 10})
    ).json()

    assert body["items"] == []
    assert body["total"] == len(expected)


# --- AC-20: bodies keep reading width / height / local_time from `extra` -----------------


async def test_ac20_width_and_height_in_the_body_come_from_extra(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    got, _ = await walk(client, {})
    bodies: dict[int, tuple[Any, Any]] = {}
    for page in (1, 2):
        items = (await client.get(URL, params={"page": page, "per_page": 100})).json()["items"]
        bodies.update({i["id"]: (i["width"], i["height"]) for i in items})

    assert set(bodies) == set(got)
    for row in meta_world.rows:
        want = row.declared if row.declared is not None else (None, None)
        assert bodies[row.id] == want, row.source_id


async def test_ac20_size_over_32767_is_still_shown_in_the_body(
    client: httpx.AsyncClient, meta_world: MetaWorld
) -> None:
    response = await client.get(rover_url("perseverance"), params={"sol": "102"})

    sizes = sorted((i["width"], i["height"]) for i in response.json()["items"])
    assert sizes == [(100, 32768), (32768, 100)]


@pytest.mark.parametrize("rover", ALL_ROVERS)
async def test_ac20_detail_local_time_comes_from_extra(
    client: httpx.AsyncClient, meta_world: MetaWorld, rover: str
) -> None:
    kind = "ltst" if rover in MER else "lmst"
    for row in (r for r in meta_world.rows if r.rover == rover and r.sol == 100):
        body = (await client.get(f"{V2}/photos/{row.id}")).json()
        want = None if row.local_time is None else {"time": row.local_time, "kind": kind}
        assert body["local_time"] == want, row.source_id
        assert (body["width"], body["height"]) == (
            row.declared if row.declared is not None else (None, None)
        )
