"""GET /api/v2/rovers/{rover}/photos/latest (docs/API.md)."""

import httpx
import pytest

from tests.api.v2.conftest import (
    HUGE,
    LATEST_KEYS,
    OPPORTUNITY_COUNT,
    OPPORTUNITY_ORDER,
    OPPORTUNITY_SOL,
    PERSEVERANCE_SOL_2031,
    V2,
    World,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api


def latest_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/photos/latest"


async def test_latest_returns_exact_json_for_max_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(latest_url("perseverance"))

    assert response.status_code == 200
    body = response.json()
    assert list(body) == LATEST_KEYS
    expected = {
        "sol": 2031,
        "items": world.photos(PERSEVERANCE_SOL_2031),
        "page": 1,
        "per_page": 25,
        "total": len(PERSEVERANCE_SOL_2031),
    }
    assert_json(body, expected)


async def test_latest_sol_is_max_over_all_sample_types(
    client: httpx.AsyncClient, world: World
) -> None:
    """Curiosity's max sol 20 has only a subframe (the max full-frame sol is 10)."""
    response = await client.get(latest_url("curiosity"))

    assert_json(
        response.json(),
        {"sol": 20, "items": world.photos(["c20-sub"]), "page": 1, "per_page": 25, "total": 1},
    )


async def test_latest_rover_without_data_returns_null_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(latest_url("spirit"))

    assert response.status_code == 200
    assert_json(response.json(), {"sol": None, "items": [], "page": 1, "per_page": 25, "total": 0})


@pytest.mark.parametrize("camera", ["NAVCAM_LEFT", "navcam_left"], ids=["upper", "lower"])
async def test_latest_filter_camera(client: httpx.AsyncClient, world: World, camera: str) -> None:
    response = await client.get(latest_url("perseverance"), params={"camera": camera})

    body = response.json()
    assert body["sol"] == 2031
    assert_json(body["items"], world.photos(["p2031-next-day", "p2031-a"]))
    assert body["total"] == 2


async def test_latest_camera_without_photos_on_max_sol_keeps_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    """SUPERCAM_RMI has a photo on sol 2030 only: the latest sol is still the rover's."""
    response = await client.get(latest_url("perseverance"), params={"camera": "SUPERCAM_RMI"})

    assert response.status_code == 200
    assert_json(response.json(), {"sol": 2031, "items": [], "page": 1, "per_page": 25, "total": 0})


async def test_latest_pagination(client: httpx.AsyncClient, world: World) -> None:
    params = {"page": "2", "per_page": "2"}
    body = (await client.get(latest_url("perseverance"), params=params)).json()

    assert (body["sol"], body["page"], body["per_page"], body["total"]) == (2031, 2, 2, 5)
    assert_json(body["items"], world.photos(["p2031-tie2", "p2031-tie1"]))


async def test_latest_pagination_default_per_page_is_25(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(latest_url("opportunity"))).json()

    assert body["sol"] == OPPORTUNITY_SOL
    assert (body["page"], body["per_page"], body["total"]) == (1, 25, OPPORTUNITY_COUNT)
    assert [p["id"] for p in body["items"]] == world.ids(OPPORTUNITY_ORDER[:25])


async def test_latest_pagination_beyond_end_is_empty(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(latest_url("opportunity"), params={"page": "3"})).json()

    assert_json(
        body,
        {"sol": OPPORTUNITY_SOL, "items": [], "page": 3, "per_page": 25, "total": 30},
    )


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("page", "0"),
        ("page", "1000001"),
        ("page", "abc"),
        ("page", HUGE),
        ("per_page", "0"),
        ("per_page", "101"),
        ("per_page", HUGE),
        ("camera", "FHAZ"),  # not a Perseverance camera
        ("camera", "\x00"),
    ],
    ids=lambda value: repr(value) if len(value) < 20 else f"{len(value)}-digits",
)
async def test_latest_invalid_param_returns_422(
    client: httpx.AsyncClient, world: World, param: str, value: str
) -> None:
    response = await client.get(latest_url("perseverance"), params={param: value})

    assert_validation_error(response, param)


async def test_latest_ignores_sol_and_sample_type(client: httpx.AsyncClient, world: World) -> None:
    """Only camera/page/per_page are parameters of latest; others are unknown -> ignored."""
    params = {"sol": "1", "sample_type": "full"}
    body = (await client.get(latest_url("perseverance"), params=params)).json()

    assert body["sol"] == 2031
    assert body["total"] == len(PERSEVERANCE_SOL_2031)
