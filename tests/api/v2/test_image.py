"""GET /api/v2/photos/{id}/image (docs/API.md; stage 6 contract, section 1).

302 to the stored URL of the requested size, falling back to `full` when that size is null
(every Curiosity photo); `Cache-Control: public, max-age=86400`; the Location comes only from
the database, never from the request. Uses the shared read-only `v2_world` seed.
"""

import httpx
import pytest

from tests.api.v2.conftest import V2, World, assert_error, assert_validation_error

pytestmark = pytest.mark.api

IMAGE_CACHE_CONTROL = "public, max-age=86400"
SIZES = ["full", "large", "medium", "small"]


def image_url(photo_id: int | str, query: str = "") -> str:
    return f"{V2}/photos/{photo_id}/image{query}"


def stored(world: World, source_id: str, size: str) -> str | None:
    _, record = world.records[source_id]
    return getattr(record, f"img_{size}")


def assert_redirect(response: httpx.Response, location: str) -> None:
    assert response.status_code == 302, response.text
    assert response.headers["location"] == location
    assert response.headers["cache-control"] == IMAGE_CACHE_CONTROL


@pytest.mark.parametrize("size", SIZES)
async def test_image_size_given_redirects_to_stored_url_of_that_size(
    client: httpx.AsyncClient, world: World, size: str
) -> None:
    expected = stored(world, "p2031-a", size)
    assert expected is not None

    response = await client.get(image_url(world.photo_ids["p2031-a"], f"?size={size}"))

    assert_redirect(response, expected)


async def test_image_sizes_are_distinct_urls(client: httpx.AsyncClient, world: World) -> None:
    photo_id = world.photo_ids["p2031-a"]

    locations = [
        (await client.get(image_url(photo_id, f"?size={size}"))).headers["location"]
        for size in SIZES
    ]

    assert len(set(locations)) == len(SIZES)


async def test_image_without_size_redirects_to_full(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(image_url(world.photo_ids["p2031-a"]))

    assert_redirect(response, stored(world, "p2031-a", "full"))


@pytest.mark.parametrize("size", ["FULL", "Large", "mEdIuM", "SMALL"])
async def test_image_size_is_case_insensitive(
    client: httpx.AsyncClient, world: World, size: str
) -> None:
    response = await client.get(image_url(world.photo_ids["p2031-a"], f"?size={size}"))

    assert_redirect(response, stored(world, "p2031-a", size.lower()))


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(
    "source_id", ["c1-fhaz", "c1-mixed", "o-00"], ids=["curiosity", "mixed", "opportunity"]
)
async def test_image_missing_size_falls_back_to_full(
    client: httpx.AsyncClient, world: World, source_id: str, size: str
) -> None:
    full = stored(world, source_id, "full")
    if size != "full":
        assert stored(world, source_id, size) is None  # the seed has only img_full

    response = await client.get(image_url(world.photo_ids[source_id], f"?size={size}"))

    assert_redirect(response, full)


@pytest.mark.parametrize(
    "source_id",
    ["p2030-sub", "p2031-early", "c1-chem", "c20-sub"],
    ids=["subframe", "downsampled", "chemcam-prc", "curiosity-subframe"],
)
async def test_image_any_sample_type_redirects(
    client: httpx.AsyncClient, world: World, source_id: str
) -> None:
    response = await client.get(image_url(world.photo_ids[source_id], "?size=large"))

    expected = stored(world, source_id, "large") or stored(world, source_id, "full")
    assert_redirect(response, expected)


async def test_image_redirect_with_if_none_match_stays_302_with_own_cache_control(
    client: httpx.AsyncClient, world: World
) -> None:
    """The caching middleware must not turn the redirect into a 304 or override its header."""
    url = image_url(world.photo_ids["p2031-a"])
    first = await client.get(url)
    etag = first.headers.get("etag")

    again = await client.get(url, headers={"If-None-Match": etag or "*"})

    assert_redirect(again, stored(world, "p2031-a", "full"))


@pytest.mark.parametrize(
    "query",
    [
        "?size=full&url=https://evil.example.test/x.png",
        "?size=full&location=https://evil.example.test/x.png",
        "?size=full&redirect=https://evil.example.test/x.png",
        "?size=https://evil.example.test/x.png",
        "?size=full&size=https://evil.example.test/x.png",
    ],
    ids=["url-param", "location-param", "redirect-param", "size-is-url", "repeated-size"],
)
async def test_image_location_never_comes_from_query(
    client: httpx.AsyncClient, world: World, query: str
) -> None:
    response = await client.get(image_url(world.photo_ids["p2031-a"], query))

    assert "evil.example.test" not in response.headers.get("location", "")
    if response.status_code == 302:
        assert response.headers["location"] in {stored(world, "p2031-a", size) for size in SIZES}
    else:
        assert_validation_error(response, "size")


@pytest.mark.parametrize(
    "photo_id",
    ["0", "-1", "999999999", str(2**63 - 1), str(2**63), "9" * 5000],
    ids=["zero", "negative", "missing", "bigint-max", "bigint-max-plus-1", "huge"],
)
async def test_image_missing_or_out_of_range_id_returns_404(
    client: httpx.AsyncClient, world: World, photo_id: str
) -> None:
    response = await client.get(image_url(photo_id))

    error = assert_error(response, 404, "photo_not_found")
    assert error["details"] is None
    assert "location" not in response.headers
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("photo_id", ["abc", "1.5", "0x10"], ids=["word", "float", "hex"])
async def test_image_non_integer_id_returns_422(
    client: httpx.AsyncClient, world: World, photo_id: str
) -> None:
    response = await client.get(image_url(photo_id))

    assert_validation_error(response, "id", "photo_id")


@pytest.mark.parametrize(
    "size",
    ["", "huge", "thumbnail", "full ", "1", "%00"],
    ids=["empty", "huge", "thumb", "space", "digit", "nul"],
)
async def test_image_invalid_size_returns_422_on_size(
    client: httpx.AsyncClient, world: World, size: str
) -> None:
    response = await client.get(image_url(world.photo_ids["p2031-a"], f"?size={size}"))

    assert_validation_error(response, "size")
    assert "location" not in response.headers
    assert response.headers["cache-control"] == "no-store"
