"""GZip of v1/v2 responses (stage 9) and its interplay with HTTP caching.

- With `Accept-Encoding: gzip`, a response of about 1 KB or more is gzip-encoded and carries
  `Vary: Accept-Encoding`; without the header the body is plain JSON.
- ETag stays; `If-None-Match` with the ETag gives 304 for both encodings; HEAD works.
- Errors and admin responses stay `no-store`; the image 302 is unaffected.

httpx adds `Accept-Encoding` to every request and decodes gzip transparently, so these
tests set or remove the header explicitly and read the raw (still encoded) bytes.
The module commits its own dataset via `clean_db`.
"""

import gzip
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

pytestmark = pytest.mark.api

Factory = async_sessionmaker[AsyncSession]

GZIP = {"Accept-Encoding": "gzip"}
NO_STORE = "no-store"
BIG_SOL = 3
BIG_COUNT = 40
ADMIN_TOKEN = "test-admin-token-compression"

# Large (several KB) responses of v1 and v2.
LARGE_URLS = [
    f"/api/v1/rovers/curiosity/photos?sol={BIG_SOL}",
    f"/mars-photos/api/v1/rovers/curiosity/photos?sol={BIG_SOL}",
    "/api/v1/rovers/curiosity/latest_photos",
    "/api/v1/rovers",
    f"/api/v2/rovers/curiosity/photos?sol={BIG_SOL}&per_page=40",
    "/api/v2/rovers/curiosity/photos/latest?per_page=40",
]
# Well below 1 KB.
SMALL_URL = "/api/v2/rovers/spirit/manifest"


@pytest.fixture
async def dataset(clean_db: Factory) -> dict[str, int]:
    records = []
    for n in range(BIG_COUNT):
        taken_at = datetime(2012, 8, 9, 0, 0, tzinfo=UTC) + timedelta(seconds=n)
        records.append(
            build_record(
                source_id=f"gz-{n:03d}",
                sol=BIG_SOL,
                camera_code="FHAZ",
                sample_type="full",
                taken_at=taken_at,
                earth_date=taken_at.date(),
                img_full=f"https://images.example.test/gz-{n:03d}.JPG",
                credit="NASA/JPL-Caltech/MSSS",
            )
        )
    async with clean_db() as session:
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(session, "curiosity", {BIG_SOL})
        await refresh_rover_stats(session, "curiosity")
        await session.commit()
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        return {source_id: photo_id for source_id, photo_id in rows}


async def raw_request(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    encoding: str | None,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.Response, bytes]:
    """Send with exactly the given Accept-Encoding (None: header absent); raw body bytes."""
    request = client.build_request(method, url, headers=headers or {})
    if encoding is None:
        del request.headers["accept-encoding"]
    else:
        request.headers["accept-encoding"] = encoding
    response = await client.send(request, stream=True)
    try:
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
    finally:
        await response.aclose()
    return response, raw


def vary_tokens(response: httpx.Response) -> set[str]:
    return {
        token.strip().lower()
        for value in response.headers.get_list("vary")
        for token in value.split(",")
    }


# --- encoding -------------------------------------------------------------------------------


@pytest.mark.parametrize("url", LARGE_URLS)
async def test_large_response_with_accept_gzip_is_gzip_encoded(
    client: httpx.AsyncClient, dataset: dict[str, int], url: str
) -> None:
    plain, plain_raw = await raw_request(client, url, encoding=None)
    zipped, zipped_raw = await raw_request(client, url, encoding="gzip")

    assert plain.status_code == zipped.status_code == 200
    assert len(plain_raw) >= 1024, (url, len(plain_raw))
    assert zipped.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in vary_tokens(zipped)
    assert gzip.decompress(zipped_raw) == plain_raw
    assert len(zipped_raw) < len(plain_raw)
    assert int(zipped.headers["content-length"]) == len(zipped_raw)
    assert zipped.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("url", LARGE_URLS)
async def test_large_response_without_accept_encoding_is_plain_json(
    client: httpx.AsyncClient, dataset: dict[str, int], url: str
) -> None:
    response, raw = await raw_request(client, url, encoding=None)

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(raw)
    json.loads(raw)


async def test_identity_only_is_plain(client: httpx.AsyncClient, dataset: dict[str, int]) -> None:
    response, raw = await raw_request(client, LARGE_URLS[0], encoding="identity")

    assert "content-encoding" not in response.headers
    json.loads(raw)


async def test_small_response_is_not_compressed(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response, raw = await raw_request(client, SMALL_URL, encoding="gzip")

    assert response.status_code == 200
    assert len(raw) < 500, len(raw)
    assert "content-encoding" not in response.headers
    json.loads(raw)


async def test_decoded_gzip_body_equals_plain_body_through_httpx(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    url = LARGE_URLS[0]
    zipped = await client.get(url, headers=GZIP)
    _, plain_raw = await raw_request(client, url, encoding=None)

    assert zipped.headers.get("content-encoding") == "gzip"
    assert zipped.content == plain_raw


# --- ETag / 304 / HEAD ----------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["gzip", None], ids=["gzip", "plain"])
@pytest.mark.parametrize("url", LARGE_URLS)
async def test_etag_present_and_if_none_match_gives_304(
    client: httpx.AsyncClient, dataset: dict[str, int], url: str, encoding: str | None
) -> None:
    first, _ = await raw_request(client, url, encoding=encoding)
    etag = first.headers.get("etag")
    assert etag, url

    again, raw = await raw_request(client, url, encoding=encoding, headers={"If-None-Match": etag})

    assert again.status_code == 304, (url, again.status_code)
    assert raw == b""
    assert again.headers["etag"] == etag
    assert again.headers["cache-control"] == first.headers["cache-control"]


async def test_etag_stable_across_requests_with_gzip(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    a, _ = await raw_request(client, LARGE_URLS[0], encoding="gzip")
    b, _ = await raw_request(client, LARGE_URLS[0], encoding="gzip")

    assert a.headers["etag"] == b.headers["etag"]


@pytest.mark.parametrize("encoding", ["gzip", None], ids=["gzip", "plain"])
async def test_head_large_response_no_body_same_headers(
    client: httpx.AsyncClient, dataset: dict[str, int], encoding: str | None
) -> None:
    url = LARGE_URLS[0]
    get, _ = await raw_request(client, url, encoding=encoding)
    head, raw = await raw_request(client, url, method="HEAD", encoding=encoding)

    assert head.status_code == 200
    assert raw == b""
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["cache-control"] == get.headers["cache-control"]

    not_modified, raw = await raw_request(
        client,
        url,
        method="HEAD",
        encoding=encoding,
        headers={"If-None-Match": get.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert raw == b""


# --- what must not change -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/api/v2/rovers/pluto", 404),
        ("/api/v2/rovers/curiosity/photos?sol=-1", 422),
        ("/api/v1/rovers/pluto", 400),
        ("/api/v1/rovers/curiosity/photos?sol=abc", 400),
    ],
    ids=["v2-404", "v2-422", "v1-400-rover", "v1-400-sol"],
)
async def test_errors_with_gzip_stay_no_store_without_etag(
    client: httpx.AsyncClient, dataset: dict[str, int], url: str, status: int
) -> None:
    response, raw = await raw_request(client, url, encoding="gzip")

    assert response.status_code == status
    assert response.headers["cache-control"] == NO_STORE
    assert "etag" not in response.headers
    body = gzip.decompress(raw) if response.headers.get("content-encoding") == "gzip" else raw
    json.loads(body)


async def test_image_redirect_unaffected_by_gzip(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    photo_id = dataset["gz-000"]
    url = f"/api/v2/photos/{photo_id}/image"
    plain, _ = await raw_request(client, url, encoding=None)
    zipped, _ = await raw_request(client, url, encoding="gzip")

    for response in (plain, zipped):
        assert response.status_code == 302
        assert response.headers["location"] == "https://images.example.test/gz-000.JPG"
        assert response.headers["cache-control"] == "public, max-age=86400"
    assert "content-encoding" not in zipped.headers


@pytest.fixture
async def admin_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(settings.model_copy(update={"admin_token": SecretStr(ADMIN_TOKEN)}))
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        await app.state.engine.dispose()


async def _insert_runs(factory: Factory, count: int) -> None:
    async with factory() as session:
        for n in range(count):
            await session.execute(
                text(
                    "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, "
                    "finished_at, failed_sols) VALUES ('curiosity', 'backfill', :a, :b, "
                    "'success', now(), '{}')"
                ),
                {"a": n * 10, "b": n * 10 + 9},
            )
        await session.commit()


async def test_admin_large_response_with_gzip_stays_no_store(
    admin_client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await _insert_runs(clean_db, 50)
    headers: dict[str, Any] = {"X-Admin-Token": ADMIN_TOKEN}

    response, raw = await raw_request(
        admin_client, "/api/v2/admin/sync/runs?per_page=50", encoding="gzip", headers=headers
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == NO_STORE
    assert "etag" not in response.headers
    body = gzip.decompress(raw) if response.headers.get("content-encoding") == "gzip" else raw
    assert len(json.loads(body)["items"]) >= 1
