"""HTTP caching and CORS for v1 and v2 (docs/API.md "Кэширование (v1 и v2)"; stage 6 contract,
section 3).

- Every 200 GET of /api/v1, /mars-photos/api/v1 and /api/v2 carries an ETag; a repeated request
  with a matching If-None-Match gets 304 with no body (weak comparison, lists and "*").
- Cache-Control: lists, rovers, cameras, manifests -> public, max-age=300; the photo card
  (/photos/{id}, v1 and v2) -> public, max-age=3600; errors -> no-store.
- /health is left alone. CORS exposes ETag and allows only GET.

The module commits its own small dataset and uses `clean_db` (not the v2 shared seed).
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

pytestmark = pytest.mark.api

Factory = async_sessionmaker[AsyncSession]

SHORT = "public, max-age=300"
CARD = "public, max-age=3600"
NO_STORE = "no-store"
V1_PREFIXES = ["/api/v1", "/mars-photos/api/v1"]

# Path templates; "{id}" is the seeded Curiosity full-frame photo.
V2_SHORT = [
    "/api/v2/rovers",
    "/api/v2/rovers/curiosity",
    "/api/v2/rovers/curiosity/cameras",
    "/api/v2/rovers/curiosity/photos?sol=1",
    "/api/v2/rovers/curiosity/photos/latest",
    "/api/v2/rovers/curiosity/manifest",
]
V1_SHORT = [
    "/rovers",
    "/rovers/curiosity",
    "/rovers/curiosity/photos?sol=1",
    "/rovers/curiosity/latest_photos",
    "/manifests/curiosity",
]


async def seed(factory: Factory, rover_id: str, records: list) -> None:
    async with factory() as session:
        await upsert_photos(session, rover_id, records)
        await recompute_sol_summaries(session, rover_id, {record.sol for record in records})
        await refresh_rover_stats(session, rover_id)
        await session.commit()


def curiosity_photo(source_id: str, sol: int = 1) -> object:
    return build_record(
        source_id=source_id,
        sol=sol,
        camera_code="FHAZ",
        sample_type="full",
        taken_at=datetime(2012, 8, 7, 12, 0, tzinfo=UTC),
        earth_date=date(2012, 8, 7),
        img_full=f"https://images.example.test/{source_id}.JPG",
        credit="NASA/JPL-Caltech/MSSS",
    )


@pytest.fixture
async def photo_id(clean_db: Factory) -> AsyncIterator[int]:
    await seed(clean_db, "curiosity", [curiosity_photo("cache-c1")])
    async with clean_db() as session:
        value = await session.scalar(text("SELECT id FROM photos WHERE source_id = 'cache-c1'"))
    yield int(value)


@pytest.fixture
def cors_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def v2_urls(photo_id: int) -> list[str]:
    return [*V2_SHORT, f"/api/v2/photos/{photo_id}"]


def v1_urls(photo_id: int) -> list[str]:
    return [
        f"{prefix}{path}" for prefix in V1_PREFIXES for path in [*V1_SHORT, f"/photos/{photo_id}"]
    ]


def all_urls(photo_id: int) -> list[str]:
    return v2_urls(photo_id) + v1_urls(photo_id)


def weak(etag: str) -> str:
    return etag if etag.startswith("W/") else f"W/{etag}"


def strong(etag: str) -> str:
    return etag.removeprefix("W/")


def assert_not_modified(response: httpx.Response, etag: str, cache_control: str) -> None:
    assert response.status_code == 304, (response.status_code, response.text)
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == cache_control
    assert response.headers.get("content-length", "0") == "0"


# --- ETag and 304 --------------------------------------------------------------------------


async def test_every_200_has_etag_and_repeat_with_if_none_match_gets_304(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    for url in all_urls(photo_id):
        first = await client.get(url)
        assert first.status_code == 200, (url, first.text)
        etag = first.headers.get("etag")
        assert etag, url
        assert etag.startswith(('"', 'W/"')) and etag.endswith('"'), (url, etag)

        again = await client.get(url, headers={"If-None-Match": etag})

        assert_not_modified(again, etag, first.headers["cache-control"])


async def test_etag_is_stable_for_the_same_response(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    for url in all_urls(photo_id):
        first = await client.get(url)
        second = await client.get(url)
        assert first.headers["etag"] == second.headers["etag"], url


@pytest.mark.parametrize(
    ("url_a", "url_b"),
    [
        (
            "/api/v2/rovers/curiosity/photos?per_page=1",
            "/api/v2/rovers/curiosity/photos?per_page=2",
        ),
        ("/api/v2/rovers/curiosity", "/api/v2/rovers/perseverance"),
        ("/api/v1/rovers/curiosity/photos?sol=1", "/api/v1/rovers/curiosity/photos?sol=2"),
    ],
    ids=["v2-query", "v2-path", "v1-query"],
)
async def test_different_response_has_different_etag(
    client: httpx.AsyncClient, photo_id: int, url_a: str, url_b: str
) -> None:
    a = await client.get(url_a)
    b = await client.get(url_b)

    assert a.status_code == b.status_code == 200
    assert a.content != b.content
    assert a.headers["etag"] != b.headers["etag"]


async def test_etag_of_other_url_does_not_give_304(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    other = (await client.get("/api/v2/rovers/perseverance")).headers["etag"]

    response = await client.get("/api/v2/rovers/curiosity", headers={"If-None-Match": other})

    assert response.status_code == 200
    assert response.json()["id"] == "curiosity"


async def test_etag_changes_when_data_changes(
    client: httpx.AsyncClient, photo_id: int, clean_db: Factory
) -> None:
    url = "/api/v2/rovers/curiosity/photos?sol=1"
    old = (await client.get(url)).headers["etag"]

    await seed(clean_db, "curiosity", [curiosity_photo("cache-c1-new")])
    response = await client.get(url, headers={"If-None-Match": old})

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.headers["etag"] != old


@pytest.mark.parametrize(
    "prefix", ["/api/v2/rovers", "/api/v1/rovers", "/mars-photos/api/v1/rovers"]
)
@pytest.mark.parametrize(
    "variant",
    ["weak", "strong", "list-first", "list-last", "list-no-spaces", "star"],
)
async def test_if_none_match_variants_get_304(
    client: httpx.AsyncClient, photo_id: int, prefix: str, variant: str
) -> None:
    first = await client.get(prefix)
    etag = first.headers["etag"]
    header = {
        "weak": weak(etag),
        "strong": strong(etag),
        "list-first": f'{etag}, "other"',
        "list-last": f'"other", W/"another", {etag}',
        "list-no-spaces": f'"other",{etag}',
        "star": "*",
    }[variant]

    response = await client.get(prefix, headers={"If-None-Match": header})

    assert_not_modified(response, etag, first.headers["cache-control"])


@pytest.mark.parametrize(
    "header",
    ['"nope"', 'W/"nope"', '"nope", W/"nope2"', ""],
    ids=["strong", "weak", "list", "empty"],
)
async def test_if_none_match_mismatch_returns_full_200(
    client: httpx.AsyncClient, photo_id: int, header: str
) -> None:
    plain = await client.get("/api/v2/rovers")

    response = await client.get("/api/v2/rovers", headers={"If-None-Match": header})

    assert response.status_code == 200
    assert response.content == plain.content
    assert response.headers["etag"] == plain.headers["etag"]


async def test_etag_without_quotes_or_partial_value_does_not_match(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    etag = strong((await client.get("/api/v2/rovers")).headers["etag"])
    inner = etag.strip('"')

    for header in (inner, f'"{inner[:-1]}"'):
        response = await client.get("/api/v2/rovers", headers={"If-None-Match": header})
        assert response.status_code == 200, header


async def test_200_content_length_matches_body(client: httpx.AsyncClient, photo_id: int) -> None:
    for url in all_urls(photo_id):
        response = await client.get(url)
        # Content-Length counts the bytes on the wire (gzip-encoded when httpx asks for it).
        assert int(response.headers["content-length"]) == response.num_bytes_downloaded, url


# --- HEAD ----------------------------------------------------------------------------------


async def test_head_matches_get_headers_without_body(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    """HEAD on v2 and both v1 prefixes: 200, no body, same ETag and Cache-Control as GET."""
    for url in all_urls(photo_id):
        get = await client.get(url)
        head = await client.head(url)

        assert head.status_code == 200, (url, head.status_code)
        assert head.content == b"", url
        assert head.headers.get("etag") == get.headers["etag"], url
        assert head.headers.get("cache-control") == get.headers["cache-control"], url


async def test_head_with_matching_if_none_match_gets_304(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    for url in all_urls(photo_id):
        get = await client.get(url)
        etag = get.headers["etag"]

        head = await client.head(url, headers={"If-None-Match": etag})

        assert head.status_code == 304, (url, head.status_code)
        assert head.content == b"", url
        assert head.headers["etag"] == etag, url
        assert head.headers["cache-control"] == get.headers["cache-control"], url


async def test_head_error_is_no_store_without_etag(client: httpx.AsyncClient) -> None:
    for url in ("/api/v2/rovers/pluto", "/api/v1/rovers/pluto", "/mars-photos/api/v1/rovers/pluto"):
        response = await client.head(url, headers={"If-None-Match": "*"})

        assert response.status_code in (400, 404), (url, response.status_code)
        assert response.content == b""
        assert response.headers["cache-control"] == NO_STORE, url
        assert "etag" not in response.headers


# --- Cache-Control -------------------------------------------------------------------------


async def test_cache_control_lists_rovers_cameras_manifests_short(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    urls = V2_SHORT + [f"{prefix}{path}" for prefix in V1_PREFIXES for path in V1_SHORT]
    for url in urls:
        response = await client.get(url)
        assert response.status_code == 200, url
        assert response.headers["cache-control"] == SHORT, url


async def test_cache_control_photo_card_one_hour(client: httpx.AsyncClient, photo_id: int) -> None:
    urls = [f"/api/v2/photos/{photo_id}"] + [
        f"{prefix}/photos/{photo_id}" for prefix in V1_PREFIXES
    ]
    for url in urls:
        response = await client.get(url)
        assert response.status_code == 200, url
        assert response.headers["cache-control"] == CARD, url


async def test_cache_control_image_redirect_one_day(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    response = await client.get(f"/api/v2/photos/{photo_id}/image")

    assert response.status_code == 302
    assert response.headers["cache-control"] == "public, max-age=86400"


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/api/v2/rovers/pluto", 404),
        ("/api/v2/photos/999999999", 404),
        ("/api/v2/rovers/curiosity/photos?sol=-1", 422),
        ("/api/v2/rovers/curiosity/manifest?from_sol=5&to_sol=1", 422),
        ("/api/v2/no-such-path", 404),
        ("/api/v1/rovers/pluto", 400),
        ("/api/v1/photos/999999999", 404),
        ("/mars-photos/api/v1/rovers/curiosity/photos?sol=abc", 400),
        ("/api/v1/rovers/curiosity/photos?sol=1&page=abc", 400),
    ],
    ids=[
        "v2-rover-404",
        "v2-photo-404",
        "v2-422",
        "v2-manifest-422",
        "v2-unknown-path",
        "v1-rover-400",
        "v1-photo-404",
        "v1-alt-prefix-400",
        "v1-bad-page-400",
    ],
)
async def test_errors_are_no_store_without_etag_and_never_304(
    client: httpx.AsyncClient, photo_id: int, url: str, status: int
) -> None:
    response = await client.get(url)
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert "etag" not in response.headers

    again = await client.get(url, headers={"If-None-Match": "*"})
    assert again.status_code == status
    assert again.content == response.content


async def test_health_is_untouched(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert "etag" not in response.headers
    assert "cache-control" not in response.headers
    again = await client.get("/health", headers={"If-None-Match": "*"})
    assert again.status_code == 200
    assert again.json() == {"status": "ok", "db": "ok"}


async def test_non_get_methods_are_not_cached(client: httpx.AsyncClient, photo_id: int) -> None:
    response = await client.post("/api/v2/rovers", headers={"If-None-Match": "*"})

    assert response.status_code == 405
    assert "etag" not in response.headers


# --- CORS ----------------------------------------------------------------------------------


async def test_cors_exposes_etag_on_get(cors_client: httpx.AsyncClient, photo_id: int) -> None:
    async with cors_client as client:
        response = await client.get("/api/v2/rovers", headers={"Origin": "https://example.test"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in ("*", "https://example.test")
    exposed = [
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    ]
    assert "etag" in exposed


async def test_cors_304_still_has_cors_headers(
    cors_client: httpx.AsyncClient, photo_id: int
) -> None:
    origin = {"Origin": "https://example.test"}
    async with cors_client as client:
        etag = (await client.get("/api/v1/rovers", headers=origin)).headers["etag"]
        response = await client.get("/api/v1/rovers", headers={**origin, "If-None-Match": etag})

    assert response.status_code == 304
    assert "access-control-allow-origin" in response.headers


async def test_cors_preflight_allows_get(cors_client: httpx.AsyncClient) -> None:
    async with cors_client as client:
        response = await client.options(
            "/api/v2/rovers",
            headers={"Origin": "https://example.test", "Access-Control-Request-Method": "GET"},
        )

    assert response.status_code == 200
    assert "GET" in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_cors_preflight_rejects_other_methods(
    cors_client: httpx.AsyncClient, method: str
) -> None:
    async with cors_client as client:
        response = await client.options(
            "/api/v2/rovers",
            headers={"Origin": "https://example.test", "Access-Control-Request-Method": method},
        )

    assert response.status_code == 400
    assert method not in response.headers.get("access-control-allow-methods", "")
