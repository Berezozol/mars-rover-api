"""Malformed input on every v2 endpoint: always a 4xx in the v2 error format, never a 500."""

import httpx
import pytest

from tests.api.v2.conftest import HUGE, V2, World, assert_error, assert_validation_error

pytestmark = pytest.mark.api

NUL = "\x00"


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("camera", NUL),
        ("camera", f"FHAZ{NUL}"),
        ("sample_type", NUL),
        ("sample_type", f"full{NUL}"),
        ("earth_date", NUL),
        ("earth_date", f"2012-08-07{NUL}"),
        ("sol", NUL),
        ("page", NUL),
        ("per_page", NUL),
    ],
    ids=repr,
)
async def test_photos_nul_in_query_value_returns_422(
    client: httpx.AsyncClient, world: World, param: str, value: str
) -> None:
    response = await client.get(f"{V2}/rovers/curiosity/photos", params={param: value})

    assert_validation_error(response, param)


@pytest.mark.parametrize("param", ["from_sol", "to_sol"])
async def test_manifest_nul_in_bound_returns_422(
    client: httpx.AsyncClient, world: World, param: str
) -> None:
    response = await client.get(f"{V2}/rovers/curiosity/manifest", params={param: NUL})

    assert_validation_error(response, param)


@pytest.mark.parametrize(
    "path",
    [
        "/rovers/%00",
        "/rovers/%00/cameras",
        "/rovers/%00/photos",
        "/rovers/%00/photos/latest",
        "/rovers/%00/manifest",
    ],
    ids=["rover", "cameras", "photos", "latest", "manifest"],
)
async def test_nul_rover_slug_returns_404(
    client: httpx.AsyncClient, world: World, path: str
) -> None:
    response = await client.get(f"{V2}{path}")

    assert_error(response, 404, "rover_not_found")


@pytest.mark.parametrize(
    ("url", "field"),
    [
        ("/rovers/curiosity/photos?sol=" + HUGE, "sol"),
        ("/rovers/curiosity/photos?page=" + HUGE, "page"),
        ("/rovers/curiosity/photos?per_page=" + HUGE, "per_page"),
        ("/rovers/curiosity/photos?sol=-" + HUGE, "sol"),
        ("/rovers/curiosity/photos?page=1000001", "page"),
        ("/rovers/curiosity/photos/latest?page=" + HUGE, "page"),
        ("/rovers/curiosity/manifest?from_sol=" + HUGE, "from_sol"),
        ("/rovers/curiosity/manifest?to_sol=" + HUGE, "to_sol"),
    ],
    ids=[
        "photos-sol",
        "photos-page",
        "photos-per-page",
        "photos-negative-sol",
        "photos-page-over-max",
        "latest-page",
        "manifest-from",
        "manifest-to",
    ],
)
async def test_huge_numbers_return_422(
    client: httpx.AsyncClient, world: World, url: str, field: str
) -> None:
    response = await client.get(f"{V2}{url}")

    assert_validation_error(response, field)


@pytest.mark.parametrize(
    "photo_id",
    ["0", "-1", "9" * 20, "-" + "9" * 20],
    ids=["zero", "negative", "20-digits", "negative-20-digits"],
)
async def test_photo_id_out_of_range_returns_404(
    client: httpx.AsyncClient, world: World, photo_id: str
) -> None:
    response = await client.get(f"{V2}/photos/{photo_id}")

    assert_error(response, 404, "photo_not_found")


async def test_photo_id_not_integer_returns_422(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"{V2}/photos/abc")

    assert_validation_error(response, "id", "photo_id")


@pytest.mark.parametrize(
    "url",
    [
        "/rovers?foo=bar",
        "/rovers/curiosity?foo=%00",
        "/rovers/curiosity/cameras?page=abc",
        "/rovers/curiosity/photos?unknown=" + HUGE,
        "/rovers/curiosity/photos/latest?sol=%00",
        "/rovers/curiosity/manifest?page=-1",
    ],
    ids=["rovers", "rover-nul", "cameras-page", "photos-huge", "latest-sol-nul", "manifest-page"],
)
async def test_unknown_params_are_ignored(
    client: httpx.AsyncClient, world: World, url: str
) -> None:
    response = await client.get(f"{V2}{url}")

    assert response.status_code == 200, response.text


async def test_unknown_v2_path_returns_404_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{V2}/no-such-endpoint")

    assert_error(response, 404, "not_found")
