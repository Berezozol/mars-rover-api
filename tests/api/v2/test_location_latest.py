"""GET /api/v2/rovers/{rover}/location?latest=true (docs/API.md; stage 12, B3).

`latest` is a boolean (`true`/`false`, also `1`/`0`, default false); anything else -> 422.
With `true` `items` holds only the last stop (largest sol, then site, then drive) or `[]`; the
other members of the response stay as without the parameter; `latest` together with `sol`,
`from_sol` or `to_sol` -> 422. Rovers without a source (Spirit, Opportunity, Ingenuity) answer
`available: false` as before.
"""

from typing import Any

import httpx
import pytest

from tests._locations import Factory, insert_dataset, insert_waypoints, waypoint
from tests.api.v2._locations_data import (
    CURIOSITY_UPDATED,
    CURIOSITY_WAYPOINTS,
    PERSEVERANCE_ORDER,
    PERSEVERANCE_UPDATED,
    PERSEVERANCE_WAYPOINTS,
    expected_waypoints,
    location_response,
    seed_locations,
)
from tests.api.v2.conftest import V2, assert_json, assert_validation_error, utc

pytestmark = pytest.mark.api

P = f"{V2}/rovers/perseverance/location"
LATEST_PERSEVERANCE = [(30, 5, 100)]
LATEST_CURIOSITY = [(8, 1, 70)]


@pytest.fixture
async def seeded(clean_db: Factory) -> Factory:
    await seed_locations(clean_db)
    return clean_db


# --- true -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "1"])
async def test_latest_true_returns_only_the_last_stop_with_the_usual_envelope(
    client: httpx.AsyncClient, seeded: Factory, value: str
) -> None:
    response = await client.get(P, params={"latest": value})

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["rover", "available", "updated_at", "source_max_sol", "items"]
    assert_json(
        body,
        location_response(
            "perseverance",
            expected_waypoints(PERSEVERANCE_WAYPOINTS, LATEST_PERSEVERANCE),
            updated_at=PERSEVERANCE_UPDATED,
            source_max_sol=30,
        ),
    )


@pytest.mark.parametrize("value", ["true", "1"])
async def test_latest_true_item_equals_the_last_item_of_the_full_list(
    client: httpx.AsyncClient, seeded: Factory, value: str
) -> None:
    full = (await client.get(P)).json()
    latest = (await client.get(P, params={"latest": value})).json()

    assert latest["items"] == [full["items"][-1]]
    assert {k: v for k, v in latest.items() if k != "items"} == {
        k: v for k, v in full.items() if k != "items"
    }


async def test_latest_true_is_scoped_to_the_rover(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/location", params={"latest": "true"})).json()

    assert_json(
        body,
        location_response(
            "curiosity",
            expected_waypoints(CURIOSITY_WAYPOINTS, LATEST_CURIOSITY),
            updated_at=CURIOSITY_UPDATED,
            source_max_sol=8,
        ),
    )


async def test_latest_true_slug_is_case_insensitive(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (
        await client.get(f"{V2}/rovers/PERSEVERANCE/location", params={"latest": "true"})
    ).json()

    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == LATEST_PERSEVERANCE


async def test_latest_true_breaks_ties_by_site_then_drive(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_waypoints(
        clean_db,
        "perseverance",
        [
            waypoint(40, 5, 100, 18.0, 77.0),
            waypoint(40, 6, 0, 18.1, 77.1),
            waypoint(40, 6, 20, 18.2, 77.2),  # same sol, largest site, then largest drive
            waypoint(39, 9, 900, 18.3, 77.3),  # larger site and drive, but an earlier sol
            waypoint(40, 6, 10, 18.4, 77.4),
        ],
    )

    body = (await client.get(P, params={"latest": "true"})).json()

    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == [(40, 6, 20)]
    assert body["items"][0]["lat"] == 18.2


async def test_latest_true_with_a_single_stop_returns_it(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_waypoints(clean_db, "perseverance", [waypoint(1, 0, 0, 18.0, 77.0)])

    body = (await client.get(P, params={"latest": "true"})).json()

    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == [(1, 0, 0)]


async def test_latest_true_without_stops_returns_empty_items_and_keeps_the_state(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_dataset(
        clean_db,
        "perseverance",
        "waypoints",
        checked_at=utc(2026, 11, 1),
        succeeded_at=utc(2026, 11, 1),
        changed_at=utc(2026, 11, 1),
        items=0,
        max_sol=None,
    )

    body = (await client.get(P, params={"latest": "true"})).json()

    assert (body["available"], body["items"]) == (True, [])
    assert body["updated_at"] == "2026-11-01T00:00:00Z"


async def test_latest_true_before_the_first_load_is_available_with_empty_items(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    body = (await client.get(P, params={"latest": "true"})).json()

    assert_json(
        body,
        {
            "rover": "perseverance",
            "available": True,
            "updated_at": None,
            "source_max_sol": None,
            "items": [],
        },
    )


# --- false and default ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["false", "0"])
async def test_latest_false_returns_the_full_list_like_without_the_parameter(
    client: httpx.AsyncClient, seeded: Factory, value: str
) -> None:
    plain = (await client.get(P)).json()
    explicit = (await client.get(P, params={"latest": value})).json()

    assert explicit == plain
    assert [(i["sol"], i["site"], i["drive"]) for i in explicit["items"]] == PERSEVERANCE_ORDER


@pytest.mark.parametrize("value", ["false", "0"])
async def test_latest_false_combines_with_sol_filters(
    client: httpx.AsyncClient, seeded: Factory, value: str
) -> None:
    body = (await client.get(P, params={"latest": value, "sol": 10})).json()

    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == [
        (10, 2, 900),
        (10, 3, 0),
        (10, 3, 50),
        (10, 4, 10),
    ]


# --- validation ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["yes", "no", "2", "-1", "tru", "null", "latest", "1.0", "true,false"]
)
async def test_latest_with_another_value_returns_422(
    client: httpx.AsyncClient, seeded: Factory, value: str
) -> None:
    response = await client.get(P, params={"latest": value})

    assert_validation_error(response, "latest")


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"sol": 10}, id="sol"),
        pytest.param({"from_sol": 1}, id="from-sol"),
        pytest.param({"to_sol": 100}, id="to-sol"),
        pytest.param({"from_sol": 1, "to_sol": 100}, id="range"),
        pytest.param({"sol": 10, "latest": "1"}, id="sol-with-numeric-flag"),
    ],
)
async def test_latest_true_together_with_a_sol_filter_returns_422(
    client: httpx.AsyncClient, seeded: Factory, params: dict[str, Any]
) -> None:
    response = await client.get(P, params={"latest": "true", **params})

    assert_validation_error(response, "latest", "sol", "from_sol", "to_sol")


async def test_latest_true_with_an_invalid_sol_is_422_too(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(P, params={"latest": "true", "sol": "abc"})

    assert_validation_error(response, "latest", "sol")


# --- rovers without a source ---------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "ingenuity"])
@pytest.mark.parametrize("latest", ["true", "false"])
async def test_latest_for_rovers_without_a_source_is_available_false_and_empty(
    client: httpx.AsyncClient, seeded: Factory, rover: str, latest: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/location", params={"latest": latest})

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "rover": rover,
            "available": False,
            "updated_at": None,
            "source_max_sol": None,
            "items": [],
        },
    )


# --- caching -------------------------------------------------------------------------------------


async def test_latest_is_part_of_the_cache_key(client: httpx.AsyncClient, seeded: Factory) -> None:
    full = await client.get(P)
    latest = await client.get(P, params={"latest": "true"})

    assert full.headers["etag"] != latest.headers["etag"]
    again = await client.get(
        P, params={"latest": "true"}, headers={"If-None-Match": latest.headers["etag"]}
    )
    assert again.status_code == 304
