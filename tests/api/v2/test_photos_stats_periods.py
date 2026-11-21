"""/photos/stats over several months and years, and dates by SOL date (AC-5, AC-6, AC-3).

Own data through `clean_db` (tests/api/v2/_stats.py `PERIODS_SPEC`), sol dates (earliest
earth_date of the sol):
perseverance 1000 -> 2023-12-31 (3 photos, one taken on 2024-01-01), 1001 -> 2024-01-01 (3),
1002 -> 2024-01-15 (3), 1003 -> 2024-02-10 (2, subframes only), 1004 -> 2025-01-05 (1);
curiosity 4000 -> 2024-01-31 (2), 4001 -> 2024-02-01 (1).
"""

from datetime import date
from typing import Any

import httpx
import pytest

from tests.api.v2._stats import SRow, expected_stats, periods_records, periods_rows, seed_records
from tests.api.v2.conftest import V2, Factory, assert_json

pytestmark = pytest.mark.api

URL = f"{V2}/photos/stats"


@pytest.fixture
async def periods(clean_db: Factory) -> list[SRow]:
    await seed_records(clean_db, periods_records())
    return periods_rows()


@pytest.fixture
async def camera_order(client: httpx.AsyncClient, periods: list[SRow]) -> dict[str, list[str]]:
    order = {}
    for rover in ("curiosity", "ingenuity", "opportunity", "perseverance", "spirit"):
        body = (await client.get(f"{V2}/rovers/{rover}/cameras")).json()
        order[rover] = [camera["code"] for camera in body["items"]]
    return order


async def stats(client: httpx.AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def group(
    rover: str,
    period: str,
    total: int,
    sols: int,
    sol_range: tuple[int, int],
    dates: tuple[str, str],
    camera: str | None = None,
) -> dict[str, Any]:
    return {
        "rover": rover,
        "camera": camera,
        "period": period,
        "total_photos": total,
        "sols_with_photos": sols,
        "first_sol": sol_range[0],
        "last_sol": sol_range[1],
        "first_date": dates[0],
        "last_date": dates[1],
    }


# --- AC-5 --------------------------------------------------------------------------------------


async def test_ac5_group_by_month_uses_sol_dates_and_orders_periods_ascending(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    body = await stats(client, rovers="perseverance", group_by="month")

    assert body["total_photos"] == 12
    assert body["sols_with_photos"] == 5
    assert (body["first_date"], body["last_date"]) == ("2023-12-31", "2025-01-05")
    # the photo of sol 1000 taken on 2024-01-01 stays in December: the sol started on 12-31
    assert body["groups"] == [
        group("perseverance", "2023-12", 3, 1, (1000, 1000), ("2023-12-31", "2023-12-31")),
        group("perseverance", "2024-01", 6, 2, (1001, 1002), ("2024-01-01", "2024-01-15")),
        group("perseverance", "2024-02", 2, 1, (1003, 1003), ("2024-02-10", "2024-02-10")),
        group("perseverance", "2025-01", 1, 1, (1004, 1004), ("2025-01-05", "2025-01-05")),
    ]


async def test_ac5_group_by_year_formats_year_and_merges_months(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    body = await stats(client, rovers="perseverance", group_by="year")

    assert body["groups"] == [
        group("perseverance", "2023", 3, 1, (1000, 1000), ("2023-12-31", "2023-12-31")),
        group("perseverance", "2024", 8, 3, (1001, 1003), ("2024-01-01", "2024-02-10")),
        group("perseverance", "2025", 1, 1, (1004, 1004), ("2025-01-05", "2025-01-05")),
    ]


async def test_ac5_periods_of_several_rovers_are_ordered_by_rover_then_period(
    client: httpx.AsyncClient, periods: list[SRow], camera_order: dict[str, list[str]]
) -> None:
    body = await stats(client, group_by="month")

    assert [(g["rover"], g["period"]) for g in body["groups"]] == [
        ("curiosity", "2024-01"),
        ("curiosity", "2024-02"),
        ("perseverance", "2023-12"),
        ("perseverance", "2024-01"),
        ("perseverance", "2024-02"),
        ("perseverance", "2025-01"),
    ]
    assert_json(body, expected_stats(periods, camera_order, group_by="month"))
    assert body["sols_with_photos"] == 7  # 5 + 2, per rover


@pytest.mark.parametrize("group_by", ["month", "year", "camera", "rover"])
async def test_ac5_every_grouping_matches_the_model_with_a_camera_filter(
    client: httpx.AsyncClient,
    periods: list[SRow],
    camera_order: dict[str, list[str]],
    group_by: str,
) -> None:
    body = await stats(client, rovers="perseverance", camera="MCZ_LEFT", group_by=group_by)

    expected = expected_stats(
        periods, camera_order, rovers=["perseverance"], cameras=["MCZ_LEFT"], group_by=group_by
    )
    assert_json(body, expected)
    assert body["total_photos"] == 4  # 1 + 2 + 1


# --- AC-3 with periods: sols without full photos are not counted -------------------------------


async def test_ac3_sample_type_full_skips_sols_that_only_have_other_types(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    body = await stats(client, rovers="perseverance", group_by="month", sample_type="full")

    assert body["total_photos"] == 9
    assert body["sols_with_photos"] == 4  # sol 1003 has subframes only
    assert body["groups"] == [
        group("perseverance", "2023-12", 2, 1, (1000, 1000), ("2023-12-31", "2023-12-31")),
        group("perseverance", "2024-01", 6, 2, (1001, 1002), ("2024-01-01", "2024-01-15")),
        group("perseverance", "2025-01", 1, 1, (1004, 1004), ("2025-01-05", "2025-01-05")),
    ]


async def test_ac3_stats_full_total_equals_list_total_with_sample_type_full(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    params = {"rovers": "perseverance", "sample_type": "full", "sol_min": "1000", "sol_max": "1003"}
    body = await stats(client, **params)
    listing = (await client.get(f"{V2}/photos", params=params)).json()

    assert body["total_photos"] == listing["total"] == 8


# --- AC-6: dates go by the sol date ------------------------------------------------------------

JANUARY = {"date_min": "2024-01-01", "date_max": "2024-01-31"}


async def test_ac6_sol_that_started_on_dec_31_is_not_counted_in_january(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    body = await stats(client, rovers="perseverance", **JANUARY)

    assert body["total_photos"] == 6  # sols 1001 and 1002; sol 1000 started on 2023-12-31
    assert body["sols_with_photos"] == 2
    assert [(g["first_sol"], g["last_sol"]) for g in body["groups"]] == [(1001, 1002)]
    assert (body["first_date"], body["last_date"]) == ("2024-01-01", "2024-01-15")


async def test_ac6_list_by_photo_date_counts_the_photo_that_stats_by_sol_date_does_not(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    listing = (
        await client.get(f"{V2}/photos", params={"rovers": "perseverance", **JANUARY})
    ).json()
    body = await stats(client, rovers="perseverance", **JANUARY)

    assert listing["total"] == 7  # the late photo of sol 1000 was taken on 2024-01-01
    assert body["total_photos"] == 6


async def test_ac6_date_range_is_inclusive_on_both_ends_and_spans_rovers(
    client: httpx.AsyncClient, periods: list[SRow]
) -> None:
    body = await stats(client, **JANUARY)

    # curiosity sol 4000 has the sol date 2024-01-31 (the last day of the range)
    assert [(g["rover"], g["total_photos"]) for g in body["groups"]] == [
        ("curiosity", 2),
        ("perseverance", 6),
    ]


@pytest.mark.parametrize(
    ("params", "total"),
    [
        pytest.param({"date_min": "2025-01-05"}, 1, id="date-min-is-the-last-sol-date"),
        pytest.param({"date_max": "2023-12-31"}, 3, id="date-max-is-the-first-sol-date"),
        pytest.param({"date_min": "2024-01-02", "date_max": "2024-01-14"}, 0, id="gap"),
        pytest.param({"date_min": "2024-02-01", "date_max": "2024-02-01"}, 1, id="single-day"),
    ],
)
async def test_ac6_date_boundaries(
    client: httpx.AsyncClient, periods: list[SRow], params: dict[str, str], total: int
) -> None:
    body = await stats(client, **params)

    assert body["total_photos"] == total
    if total == 0:
        assert body["groups"] == [] and body["first_date"] is None


async def test_ac6_date_filter_with_month_grouping_matches_the_model(
    client: httpx.AsyncClient, periods: list[SRow], camera_order: dict[str, list[str]]
) -> None:
    body = await stats(client, group_by="month", date_min="2024-01-01", date_max="2024-02-10")

    expected = expected_stats(
        periods,
        camera_order,
        date_min=date(2024, 1, 1),
        date_max=date(2024, 2, 10),
        group_by="month",
    )
    assert_json(body, expected)
