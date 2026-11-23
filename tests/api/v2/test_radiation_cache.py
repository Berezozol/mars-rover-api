"""The /radiation body cache (`app.state.rad_cache`, LRU of 8 keys (sol_min, sol_max), valid
while `RadiationService.fingerprint()` is unchanged). Stage 21 review fixes.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests._rad import Factory, insert_daily
from tests.api.v2.conftest import V2, assert_error

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

URL = f"{V2}/rovers/curiosity/radiation"


async def test_radiation_repeated_request_returns_the_identical_body(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    await insert_daily(clean_db, 20)

    first = await client.get(URL)
    second = await client.get(URL)

    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert second.headers["cache-control"] == "public, max-age=3600"


async def test_radiation_new_row_shows_up_after_a_cached_answer(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    assert [i["sol"] for i in (await client.get(URL)).json()["items"]] == [10]

    await insert_daily(clean_db, 20, updated_at="2026-12-01T00:00:00+00:00")

    body = (await client.get(URL)).json()
    assert [i["sol"] for i in body["items"]] == [10, 20]
    assert body["source_max_sol"] == 20


async def test_radiation_changed_updated_at_shows_up_after_a_cached_answer(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    first = await client.get(URL)
    async with clean_db() as session:
        await session.execute(
            text("UPDATE rad_daily SET updated_at = '2026-12-24T10:00:00+00:00' WHERE sol = 10")
        )
        await session.commit()

    second = await client.get(URL)

    assert second.json()["updated_at"] == "2026-12-24T10:00:00Z"
    assert second.headers["etag"] != first.headers["etag"]


async def test_radiation_deleted_row_disappears_after_a_cached_answer(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    await insert_daily(clean_db, 20)
    assert len((await client.get(URL)).json()["items"]) == 2
    async with clean_db() as session:
        await session.execute(text("DELETE FROM rad_daily WHERE sol = 20"))
        await session.commit()

    body = (await client.get(URL)).json()

    assert [i["sol"] for i in body["items"]] == [10]
    assert body["source_max_sol"] == 10


async def test_radiation_value_change_shows_up_after_a_cached_range_answer(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10, dose_b=(6.0, 5.0, 7.0))
    first = (await client.get(URL, params={"sol_min": 5, "sol_max": 15})).json()
    async with clean_db() as session:
        await session.execute(
            text(
                "UPDATE rad_daily SET dose_b_mean = 7.0, "
                "updated_at = '2026-12-25T00:00:00+00:00' WHERE sol = 10"
            )
        )
        await session.commit()

    second = (await client.get(URL, params={"sol_min": 5, "sol_max": 15})).json()

    assert first["items"][0]["dose_b_ugy_day"]["mean"] == 144.0
    assert second["items"][0]["dose_b_ugy_day"]["mean"] == 168.0


async def test_radiation_ranges_are_cached_apart(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for sol in (10, 20, 30):
        await insert_daily(clean_db, sol)

    whole = await client.get(URL)
    part = await client.get(URL, params={"sol_min": 20, "sol_max": 30})
    whole_again = await client.get(URL)
    part_again = await client.get(URL, params={"sol_min": 20, "sol_max": 30})

    assert [i["sol"] for i in whole.json()["items"]] == [10, 20, 30]
    assert [i["sol"] for i in part.json()["items"]] == [20, 30]
    assert whole_again.content == whole.content and part_again.content == part.content


async def test_radiation_errors_are_still_returned_when_the_body_is_cached(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    assert (await client.get(URL)).status_code == 200  # warm the cache for (None, None)

    assert_error(await client.get(f"{V2}/rovers/perseverance/radiation"), 404, "not_found")
    assert_error(await client.get(f"{V2}/rovers/pluto/radiation"), 404, "rover_not_found")
    assert_error(
        await client.get(URL, params={"sol_min": 9, "sol_max": 1}), 422, "validation_error"
    )
    assert_error(await client.get(f"{URL}?sol_min=1&sol_min=2"), 422, "validation_error")
    assert (await client.get(URL)).status_code == 200  # and the cached answer survives


async def test_radiation_cache_keeps_at_most_eight_ranges_and_stays_correct(
    client: httpx.AsyncClient, clean_db: Factory, app: Any
) -> None:
    for sol in range(1, 13):
        await insert_daily(clean_db, sol)

    for low in range(1, 12):  # 11 different keys
        body = (await client.get(URL, params={"sol_min": low})).json()
        assert [i["sol"] for i in body["items"]] == list(range(low, 13))

    assert len(app.state.rad_cache._items) <= 8
    body = (await client.get(URL, params={"sol_min": 1})).json()  # evicted key, recomputed
    assert len(body["items"]) == 12


async def test_radiation_if_none_match_works_on_a_cached_body(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10)
    first = await client.get(URL)
    await client.get(URL)

    again = await client.get(URL, headers={"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304
