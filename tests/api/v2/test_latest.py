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


async def test_latest_ignores_sol(client: httpx.AsyncClient, world: World) -> None:
    """`sol` is not a parameter of latest (camera/sample_type/page/per_page are): ignored."""
    params = {"sol": "1"}
    body = (await client.get(latest_url("perseverance"), params=params)).json()

    assert body["sol"] == 2031
    assert body["total"] == len(PERSEVERANCE_SOL_2031)


# --- sample_type -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample_type", "expected"),
    [
        ("full", ["p2031-next-day", "p2031-a", "p2031-tie1"]),
        ("subframe", ["p2031-tie2"]),
        ("downsampled", ["p2031-early"]),
    ],
)
async def test_latest_filter_sample_type(
    client: httpx.AsyncClient, world: World, sample_type: str, expected: list[str]
) -> None:
    response = await client.get(latest_url("perseverance"), params={"sample_type": sample_type})

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "sol": 2031,
            "items": world.photos(expected),
            "page": 1,
            "per_page": 25,
            "total": len(expected),
        },
    )


async def test_latest_sample_type_and_camera_combine(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"sample_type": "full", "camera": "NAVCAM_RIGHT"}

    body = (await client.get(latest_url("perseverance"), params=params)).json()

    assert body["sol"] == 2031
    assert_json(body["items"], world.photos(["p2031-tie1"]))
    assert body["total"] == 1


async def test_latest_sample_type_pagination_counts_the_filtered_list(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"sample_type": "full", "page": "2", "per_page": "2"}

    body = (await client.get(latest_url("perseverance"), params=params)).json()

    assert (body["sol"], body["page"], body["per_page"], body["total"]) == (2031, 2, 2, 3)
    assert_json(body["items"], world.photos(["p2031-tie1"]))


async def test_latest_sample_type_with_no_photos_on_max_sol_keeps_max_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    """Curiosity: sol 20 has only a subframe; the latest *full* frames are on sol 10."""
    response = await client.get(latest_url("curiosity"), params={"sample_type": "full"})

    assert response.status_code == 200
    assert_json(response.json(), {"sol": 20, "items": [], "page": 1, "per_page": 25, "total": 0})


async def test_latest_sample_type_matching_max_sol_returns_it(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(latest_url("curiosity"), params={"sample_type": "subframe"})

    assert_json(
        response.json(),
        {"sol": 20, "items": world.photos(["c20-sub"]), "page": 1, "per_page": 25, "total": 1},
    )


async def test_latest_sample_type_for_rover_without_data_returns_null_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(latest_url("spirit"), params={"sample_type": "full"})

    assert response.status_code == 200
    assert_json(response.json(), {"sol": None, "items": [], "page": 1, "per_page": 25, "total": 0})


async def test_latest_sample_type_value_is_case_insensitive_like_in_photos(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(latest_url("perseverance"), params={"sample_type": "FULL"})

    assert response.status_code == 200
    assert response.json()["total"] == 3


async def test_latest_etag_differs_by_sample_type(client: httpx.AsyncClient, world: World) -> None:
    plain = await client.get(latest_url("perseverance"))
    full = await client.get(latest_url("perseverance"), params={"sample_type": "full"})

    assert plain.headers["etag"] != full.headers["etag"]
