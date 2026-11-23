"""Stage-17 review fixes, API side: ground flag filtering (MINOR 7) and range slicing/caching.

With `WEATHER_M20_GROUND_ENABLED=false` nothing about the ground temperature may leak for
Perseverance: no `ground_temp_c` flags, no `DER_TIRS` in `source_version` or `source.files`.
A range request answers exactly the matching slice of the full list, and a cached answer
never outlives a data change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.core.config import Settings
from tests._weather import Factory
from tests.api.v2.test_weather import C, P, insert_file, seed
from tests.api.v2.test_weather_ground import make_client

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

GROUND_FLAG = {"code": "out_of_range", "parameter": "ground_temp_c", "count": 3}
OTHER_FLAG = {"code": "humidity_over_100", "parameter": "humidity_pct", "count": 12}
TIRS_PATH = (
    "data_derived_env/sol_0090_0179/sol_0100/WE__0100___________DER_TIRS_________________P01.CSV"  # noqa: E501
)
ATS_PATH = (
    "data_calibrated_env/sol_0090_0179/sol_0100/WE__0100___________CAL_ATS_________________P02.CSV"  # noqa: E501
)
VERSIONS = {"CAL_ATS": "P02", "DER_PS": "P01", "DER_TIRS": "P01"}


async def _closed(app: Any, client: httpx.AsyncClient) -> None:
    await client.aclose()
    shared = getattr(app.state, "upstream_client", None)
    if shared is not None:
        await shared.aclose()
    await app.state.engine.dispose()


@pytest.fixture
async def off(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app, client = await make_client(settings, tmp_path, False)
    try:
        yield client
    finally:
        await _closed(app, client)


@pytest.fixture
async def on(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app, client = await make_client(settings, tmp_path, True)
    try:
        yield client
    finally:
        await _closed(app, client)


async def seed_ground_sol(factory: Factory) -> None:
    await seed(
        factory,
        "perseverance",
        100,
        flags=[OTHER_FLAG, GROUND_FLAG],
        source_version=VERSIONS,
    )
    await insert_file(factory, "perseverance", 100, ATS_PATH, "CAL_ATS", "P02")
    await insert_file(factory, "perseverance", 100, TIRS_PATH, "DER_TIRS", "P01")


# --- MINOR 7 ----------------------------------------------------------------------------------


async def test_flag_off_hides_ground_flags_and_tirs_in_the_list_ac48(
    off: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_ground_sol(clean_db)

    response = await off.get(P)

    item = response.json()["items"][0]
    assert item["flags"] == [OTHER_FLAG]
    assert item["source_version"] == {"CAL_ATS": "P02", "DER_PS": "P01"}
    assert "ground" not in response.text and "TIRS" not in response.text


async def test_flag_off_hides_ground_flags_and_tirs_in_the_detail_ac48(
    off: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_ground_sol(clean_db)

    response = await off.get(f"{P}/100")

    body = response.json()
    assert body["flags"] == [OTHER_FLAG]
    assert body["source_version"] == {"CAL_ATS": "P02", "DER_PS": "P01"}
    assert [f["product"] for f in body["source"]["files"]] == ["CAL_ATS"]
    assert "ground" not in response.text and "TIRS" not in response.text


async def test_flag_on_keeps_ground_flags_and_tirs_ac48(
    on: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_ground_sol(clean_db)

    item = (await on.get(P)).json()["items"][0]
    detail = (await on.get(f"{P}/100")).json()

    assert item["flags"] == [OTHER_FLAG, GROUND_FLAG]
    assert item["source_version"] == VERSIONS
    assert sorted(f["product"] for f in detail["source"]["files"]) == ["CAL_ATS", "DER_TIRS"]


async def test_flag_off_does_not_touch_curiosity_flags_ac48(
    on: httpx.AsyncClient, off: httpx.AsyncClient, clean_db: Factory
) -> None:
    """Curiosity measures the ground: its `ground_temp_c` flags stay whatever the switch says."""
    await seed(clean_db, "curiosity", 30, flags=[GROUND_FLAG])

    a = (await on.get(C)).json()["items"][0]["flags"]
    b = (await off.get(C)).json()["items"][0]["flags"]

    assert a == b == [GROUND_FLAG]


async def test_flag_off_keeps_the_other_flags_in_the_contract_order_ac48(
    off: httpx.AsyncClient, clean_db: Factory
) -> None:
    other = {"code": "out_of_range", "parameter": "wind", "count": 1}
    await seed(clean_db, "perseverance", 100, flags=[GROUND_FLAG, other, OTHER_FLAG, GROUND_FLAG])

    assert (await off.get(P)).json()["items"][0]["flags"] == [OTHER_FLAG, other]


# --- security MEDIUM: ranges are slices of the full answer, caches follow the data ---------------


@pytest.fixture
async def spread(clean_db: Factory) -> Factory:
    for sol in (5, 10, 15, 20, 25, 30, 35):
        await seed(clean_db, "perseverance", sol, air_mean_k=200.0 + sol)
    return clean_db


RANGES = [
    {"sol_min": 10, "sol_max": 30},
    {"sol_min": 11},
    {"sol_max": 14},
    {"sol_min": 15, "sol_max": 15},
    {"sol_min": 0},
    {"sol_min": 36},
    {"sol_min": 16, "sol_max": 19},
]


@pytest.mark.parametrize(
    "params",
    RANGES,
    ids=["both", "min-only", "max-only", "single", "zero", "above-all", "gap"],
)
async def test_range_answer_is_the_slice_of_the_full_list_ac29(
    client: httpx.AsyncClient, spread: Factory, params: dict[str, int]
) -> None:
    full = (await client.get(P)).json()

    response = await client.get(P, params=params)

    body = response.json()
    low, high = params.get("sol_min", 0), params.get("sol_max", 10**9)
    assert body["items"] == [i for i in full["items"] if low <= i["sol"] <= high]
    assert {k: v for k, v in body.items() if k != "items"} == {
        k: v for k, v in full.items() if k != "items"
    }
    assert list(body) == list(full)


async def test_many_different_ranges_in_a_row_stay_correct_and_stable_ac29(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    """More distinct ranges than any small cache holds, each asked twice with equal bytes."""
    full = (await client.get(P)).json()["items"]
    sols = [i["sol"] for i in full]
    for low in range(0, 40):
        first = await client.get(P, params={"sol_min": low})
        second = await client.get(P, params={"sol_min": low})
        assert first.content == second.content
        assert [i["sol"] for i in first.json()["items"]] == [s for s in sols if s >= low]


async def test_ranged_answer_follows_a_data_change_ac34(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    params = {"sol_min": 10, "sol_max": 20}
    warm = await client.get(P, params=params)
    assert [i["sol"] for i in warm.json()["items"]] == [10, 15, 20]
    full_warm = await client.get(P)

    async with spread() as session:
        await session.execute(text("UPDATE weather_daily SET air_mean_k = 300 WHERE sol = 15"))
        await session.execute(
            text("UPDATE weather_dataset SET updated_at = '2026-12-24T10:00:00+00:00'")
        )
        await session.commit()

    fresh = await client.get(P, params=params)
    full_fresh = await client.get(P)

    changed = {i["sol"]: i for i in fresh.json()["items"]}
    assert changed[15]["air_temp_c"]["mean"] == pytest.approx(26.9, abs=0.1)
    assert fresh.json()["updated_at"] == "2026-12-24T10:00:00Z"
    assert fresh.content != warm.content
    assert fresh.headers["etag"] != warm.headers["etag"]
    assert full_fresh.content != full_warm.content
    in_full = [i for i in full_fresh.json()["items"] if 10 <= i["sol"] <= 20]
    assert in_full == fresh.json()["items"]


async def test_ranged_answer_sees_a_new_sol_after_the_dataset_changes_ac34(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    params = {"sol_min": 20}
    warm = await client.get(P, params=params)
    assert [i["sol"] for i in warm.json()["items"]] == [20, 25, 30, 35]

    await seed(spread, "perseverance", 40)
    async with spread() as session:
        await session.execute(
            text("UPDATE weather_dataset SET updated_at = '2026-12-25T10:00:00+00:00'")
        )
        await session.commit()

    fresh = await client.get(P, params=params)

    assert [i["sol"] for i in fresh.json()["items"]] == [20, 25, 30, 35, 40]
    assert fresh.json()["source_max_sol"] == 40
