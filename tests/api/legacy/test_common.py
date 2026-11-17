"""Cross-endpoint v1 rules: unknown rover -> 400, rover name case, api_key, CORS."""

import json

import httpx
import pytest

pytestmark = pytest.mark.api

# Endpoint templates that take a rover name, with params that would otherwise succeed.
ROVER_ENDPOINTS = [
    ("/rovers/{rover}", {}),
    ("/rovers/{rover}/photos", {"sol": "1"}),
    ("/rovers/{rover}/latest_photos", {}),
    ("/manifests/{rover}", {}),
]
ROVER_ENDPOINT_IDS = ["rover", "photos", "latest_photos", "manifest"]

ALL_ENDPOINTS = [
    ("/rovers", {}),
    ("/rovers/curiosity", {}),
    ("/rovers/curiosity/photos", {"sol": "1"}),
    ("/rovers/curiosity/latest_photos", {}),
    ("/manifests/curiosity", {}),
]
ALL_ENDPOINT_IDS = ["rovers", "rover", "photos", "latest_photos", "manifest"]


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(("template", "params"), ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize("rover", ["foo", "curiositi", "5", "Mars"])
async def test_unknown_rover_returns_400_invalid_rover_name(
    client: httpx.AsyncClient, prefix: str, template: str, params: dict[str, str], rover: str
) -> None:
    response = await client.get(prefix + template.format(rover=rover), params=params)

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid Rover Name"}


# Raw (already percent-encoded) path segments; httpx sends them as is.
NUL_ROVER_NAMES = ["%00", "cur%00iosity", "curiosity%00"]


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(("template", "params"), ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize("rover", NUL_ROVER_NAMES, ids=["nul", "nul-inside", "nul-suffix"])
async def test_rover_name_with_nul_returns_400_invalid_rover_name(
    client: httpx.AsyncClient, prefix: str, template: str, params: dict[str, str], rover: str
) -> None:
    # Review round 1, MAJOR 1: was 500 (NUL reached PostgreSQL).
    response = await client.get(prefix + template.format(rover=rover), params=params)

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid Rover Name"}


@pytest.mark.usefixtures("clean_db")
async def test_unknown_rover_photos_without_sol_still_400(
    client: httpx.AsyncClient, prefix: str
) -> None:
    response = await client.get(f"{prefix}/rovers/foo/photos")

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid Rover Name"}


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(("template", "params"), ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize("rover", ["spirit", "Spirit", "SPIRIT"])
async def test_rover_name_case_insensitive_on_every_endpoint(
    client: httpx.AsyncClient, prefix: str, template: str, params: dict[str, str], rover: str
) -> None:
    response = await client.get(prefix + template.format(rover=rover), params=params)

    assert response.status_code == 200, response.text


@pytest.mark.usefixtures("world")
@pytest.mark.parametrize(("path", "params"), ALL_ENDPOINTS, ids=ALL_ENDPOINT_IDS)
async def test_api_key_is_accepted_and_ignored(
    client: httpx.AsyncClient, prefix: str, path: str, params: dict[str, str]
) -> None:
    plain = await client.get(prefix + path, params=params)
    with_key = await client.get(prefix + path, params={**params, "api_key": "DEMO_KEY"})

    assert plain.status_code == 200
    assert with_key.status_code == 200
    # Every body carries seeded Curiosity data (its full max_date), so the comparison
    # below is not between two empty answers.
    assert "2014-12-05" in json.dumps(plain.json())
    assert with_key.json() == plain.json()


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(("path", "params"), ALL_ENDPOINTS, ids=ALL_ENDPOINT_IDS)
async def test_cors_get_allows_any_origin(
    client: httpx.AsyncClient, prefix: str, path: str, params: dict[str, str]
) -> None:
    response = await client.get(
        prefix + path, params=params, headers={"Origin": "https://example.org"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.usefixtures("clean_db")
async def test_cors_error_response_also_has_header(client: httpx.AsyncClient, prefix: str) -> None:
    response = await client.get(f"{prefix}/rovers/foo", headers={"Origin": "https://example.org"})

    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == "*"
