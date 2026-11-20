"""GET /api/v2/rovers/{rover}/feed.xml: parameters, errors, limits and HTTP caching
(docs/API.md `feed.xml` and "Кэширование", stage 16).

- 404 `rover_not_found` (unknown rover), 404 `not_found` (archive rovers), 422
  `validation_error` (camera, sample_type, group, limit): JSON v2 with `no-store`;
- `limit`: 1..100, default 20 (`group=sol`) / 50 (`group=photo`);
- ETag by body (stable), `Last-Modified` = newest item, `If-Modified-Since` -> 304 (a present
  `If-None-Match` decides alone), HEAD, gzip, rate limit, v1 and /photos unchanged.
"""

import gzip
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app
from tests.api.test_rate_limit import Clock, client_for, limited_settings, running
from tests.api.v2._feed_data import (
    ANCHOR,
    CACHE_CONTROL,
    HOST,
    RSS_TYPE,
    T_A,
    T_B,
    Factory,
    channel_of,
    child_text,
    day,
    get_feed,
    http_date,
    items_of,
    parse,
    photo_ids,
    record,
    seed_batch,
)
from tests.api.v2.conftest import (
    PHOTO_DETAIL_KEYS,
    PHOTO_KEYS,
    assert_error,
    assert_validation_error,
)

pytestmark = pytest.mark.api

NO_STORE = "no-store"


# --- errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "ingenuity"])
async def test_feed_archive_rover_returns_404_not_found_json(
    client: httpx.AsyncClient, world: dict[str, int], rover: str
) -> None:
    response = await get_feed(client, rover)

    assert_error(response, 404, "not_found")
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == NO_STORE
    assert "last-modified" not in response.headers


@pytest.mark.parametrize("rover", ["xyz", "curiosity2", "perseverance.xml"])
async def test_feed_unknown_rover_returns_404_rover_not_found_json(
    client: httpx.AsyncClient, world: dict[str, int], rover: str
) -> None:
    response = await get_feed(client, rover)

    assert_error(response, 404, "rover_not_found")
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize(
    ("rover", "params", "field"),
    [
        pytest.param("perseverance", {"camera": "NOPE"}, "camera", id="camera-unknown"),
        pytest.param("perseverance", {"camera": "FHAZ"}, "camera", id="camera-of-another-rover"),
        pytest.param("curiosity", {"camera": "NAVCAM_LEFT"}, "camera", id="camera-of-curiosity"),
        pytest.param("perseverance", {"sample_type": "mixed"}, "sample_type", id="type-mixed"),
        pytest.param("perseverance", {"sample_type": "thumbnail"}, "sample_type", id="type-other"),
        pytest.param("perseverance", {"group": "x"}, "group", id="group-unknown"),
        pytest.param("perseverance", {"group": "photos"}, "group", id="group-plural"),
        pytest.param("perseverance", {"group": "SOL"}, "group", id="group-uppercase"),
        pytest.param("perseverance", {"limit": "0"}, "limit", id="limit-zero"),
        pytest.param("perseverance", {"limit": "-1"}, "limit", id="limit-negative"),
        pytest.param("perseverance", {"limit": "101"}, "limit", id="limit-above-max"),
        pytest.param("perseverance", {"limit": "abc"}, "limit", id="limit-text"),
        pytest.param("perseverance", {"limit": "1.5"}, "limit", id="limit-fraction"),
        pytest.param("perseverance", {"limit": ""}, "limit", id="limit-empty"),
        pytest.param("curiosity", {"group": "photo", "limit": "101"}, "limit", id="limit-photo"),
    ],
)
async def test_feed_invalid_parameter_returns_422_json_naming_the_field(
    client: httpx.AsyncClient,
    world: dict[str, int],
    rover: str,
    params: dict[str, str],
    field: str,
) -> None:
    response = await get_feed(client, rover, **params)

    assert_validation_error(response, field)
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"limit": "1"}, id="limit-min"),
        pytest.param({"limit": "100"}, id="limit-max"),
        pytest.param({"sample_type": "full"}, id="type-full"),
        pytest.param({"sample_type": "subframe"}, id="type-subframe"),
        pytest.param({"sample_type": "downsampled"}, id="type-downsampled"),
        pytest.param({"camera": "mcz_left"}, id="camera-lowercase"),
        pytest.param({"group": "photo", "limit": "100"}, id="group-photo"),
    ],
)
async def test_feed_valid_parameters_return_200_rss(
    client: httpx.AsyncClient, world: dict[str, int], params: dict[str, str]
) -> None:
    response = await get_feed(client, **params)

    assert response.status_code == 200
    assert response.headers["content-type"] == RSS_TYPE
    parse(response)


async def test_feed_unknown_parameters_are_ignored(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    plain = await get_feed(client)
    noisy = await get_feed(client, sol="5", page="3", foo="bar", format="json")

    assert noisy.status_code == 200
    assert noisy.content == plain.content


# --- limit --------------------------------------------------------------------------------------


async def test_feed_default_limit_is_20_for_sol_and_50_for_photo(
    client: httpx.AsyncClient, many: list[str]
) -> None:
    by_sol = items_of(await get_feed(client))
    by_photo = items_of(await get_feed(client, group="photo"))

    assert len(by_sol) == 20
    assert len(by_photo) == 50
    assert child_text(by_sol[0], "title").startswith("Perseverance Sol 154: 1 new image")
    assert child_text(by_sol[1], "title").startswith("Perseverance Sol 153: 1 new image")


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"limit": "7"}, 7, id="sol-7"),
        pytest.param({"limit": "100"}, 55, id="sol-100-returns-all"),
        pytest.param({"group": "photo", "limit": "3"}, 3, id="photo-3"),
        pytest.param({"group": "photo", "limit": "100"}, 55, id="photo-100-returns-all"),
        pytest.param({"limit": "1"}, 1, id="sol-1"),
    ],
)
async def test_feed_limit_caps_the_number_of_items(
    client: httpx.AsyncClient, many: list[str], params: dict[str, str], expected: int
) -> None:
    assert len(items_of(await get_feed(client, **params))) == expected


async def test_feed_limit_keeps_the_newest_items(
    client: httpx.AsyncClient, many: list[str], clean_db: Factory
) -> None:
    ids = await photo_ids(clean_db)

    items = items_of(await get_feed(client, group="photo", limit=3))

    assert [child_text(i, "link").rsplit("/", 1)[1] for i in items] == [
        str(ids[s]) for s in many[:3]
    ]


# --- empty feed ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="no-data"),
        pytest.param({"group": "photo"}, id="no-data-photo"),
    ],
)
async def test_feed_without_new_photos_is_a_valid_empty_channel(
    client: httpx.AsyncClient, clean_db: Factory, params: dict[str, str]
) -> None:
    response = await get_feed(client, **params)

    assert response.status_code == 200
    assert response.headers["content-type"] == RSS_TYPE
    channel = channel_of(parse(response))
    assert channel.findall("item") == []
    assert channel.find("lastBuildDate") is None
    assert "last-modified" not in response.headers
    assert response.headers["etag"]
    assert child_text(channel, "title")


async def test_feed_filter_without_matches_is_an_empty_channel(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await get_feed(client, camera="MCZ_RIGHT")

    channel = channel_of(parse(response))
    assert channel.findall("item") == []
    assert channel.find("lastBuildDate") is None
    assert "last-modified" not in response.headers


async def test_feed_with_only_backfilled_photos_is_empty(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_batch(
        clean_db, "perseverance", [record("BF1", 10, "NAVCAM_LEFT", ANCHOR)], None, mark_new=False
    )

    assert items_of(await get_feed(client)) == []


# --- ETag and 304 -------------------------------------------------------------------------------


async def test_feed_etag_is_stable_and_if_none_match_returns_304(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    first = await get_feed(client)
    second = await get_feed(client)
    etag = first.headers["etag"]

    cached = await get_feed(client, headers={"If-None-Match": etag})

    assert second.headers["etag"] == etag
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == etag
    assert cached.headers["cache-control"] == CACHE_CONTROL
    assert cached.headers["last-modified"] == first.headers["last-modified"]


async def test_feed_if_none_match_mismatch_returns_200(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await get_feed(client, headers={"If-None-Match": '"other"'})

    assert response.status_code == 200
    assert response.content


async def test_feed_etag_differs_between_parameter_sets(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    sol = await get_feed(client)
    photo = await get_feed(client, group="photo")
    camera = await get_feed(client, camera="MCZ_LEFT")

    assert len({sol.headers["etag"], photo.headers["etag"], camera.headers["etag"]}) == 3


async def test_feed_etag_changes_when_a_new_batch_arrives(
    client: httpx.AsyncClient, world: dict[str, int], clean_db: Factory
) -> None:
    before = await get_feed(client)

    await seed_batch(
        clean_db,
        "perseverance",
        [record("N1", 2033, "NAVCAM_LEFT", ANCHOR)],
        ANCHOR,
    )
    after = await get_feed(client)

    assert after.headers["etag"] != before.headers["etag"]
    assert after.headers["last-modified"] == http_date(ANCHOR)
    assert len(items_of(after)) == len(items_of(before)) + 1
    old_guids = [child_text(i, "guid") for i in items_of(before)]
    assert [child_text(i, "guid") for i in items_of(after)][
        1:
    ] == old_guids  # entries are immutable


async def test_feed_batch_redelivered_later_is_a_new_item_and_the_old_one_is_unchanged(
    client: httpx.AsyncClient, world: dict[str, int], clean_db: Factory
) -> None:
    before = {
        child_text(i, "guid"): child_text(i, "description")
        for i in items_of(await get_feed(client))
    }

    await seed_batch(
        clean_db,
        "perseverance",
        [record("A5", 2032, "NAVCAM_RIGHT", day(10, 15))],
        ANCHOR,
    )
    after_items = items_of(await get_feed(client))

    after = {child_text(i, "guid"): child_text(i, "description") for i in after_items}
    assert len(after) == len(before) + 1
    for guid, description in before.items():
        assert after[guid] == description
    assert child_text(after_items[0], "title").startswith("Perseverance Sol 2032: 1 new image")


async def test_feed_head_returns_headers_without_body(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    get = await get_feed(client)

    head = await client.head("/api/v2/rovers/perseverance/feed.xml", headers={"Host": HOST})

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == RSS_TYPE
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["last-modified"] == get.headers["last-modified"]
    assert head.headers["cache-control"] == CACHE_CONTROL


async def test_feed_gzip_when_accepted(client: httpx.AsyncClient, many: list[str]) -> None:
    request = client.build_request(
        "GET",
        "/api/v2/rovers/perseverance/feed.xml",
        params={"group": "photo", "limit": 100},
        headers={"Host": HOST, "Accept-Encoding": "gzip"},
    )
    response = await client.send(request, stream=True)
    try:
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
    finally:
        await response.aclose()
    plain = await get_feed(
        client, group="photo", limit=100, headers={"Accept-Encoding": "identity"}
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert gzip.decompress(raw) == plain.content
    assert response.headers["content-type"] == RSS_TYPE


# --- If-Modified-Since --------------------------------------------------------------------------


T_A_SECOND = T_A.replace(microsecond=0)


@pytest.mark.parametrize(
    ("ims", "status"),
    [
        pytest.param(http_date(T_A_SECOND), 304, id="equal"),
        pytest.param(http_date(T_A_SECOND + timedelta(days=1)), 304, id="later"),
        pytest.param(http_date(T_A_SECOND + timedelta(days=3650)), 304, id="far-future"),
        pytest.param(http_date(T_A_SECOND - timedelta(seconds=1)), 200, id="one-second-earlier"),
        pytest.param(http_date(T_A_SECOND - timedelta(days=1)), 200, id="earlier"),
        pytest.param("not a date", 200, id="garbage"),
        pytest.param("", 200, id="empty"),
        pytest.param(T_A_SECOND.strftime("%Y-%m-%dT%H:%M:%SZ"), 200, id="iso-is-not-http-date"),
    ],
)
async def test_feed_if_modified_since_compares_with_last_modified(
    client: httpx.AsyncClient, world: dict[str, int], ims: str, status: int
) -> None:
    response = await get_feed(client, headers={"If-Modified-Since": ims})

    assert response.status_code == status
    if status == 304:
        assert response.content == b""
        assert response.headers["last-modified"] == http_date(T_A)
        assert response.headers["cache-control"] == CACHE_CONTROL
        assert response.headers["etag"]
    else:
        assert response.content
        parse(response)


async def test_feed_if_modified_since_works_for_the_group_photo_feed(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    last_modified = (await get_feed(client, group="photo")).headers["last-modified"]

    response = await get_feed(client, group="photo", headers={"If-Modified-Since": last_modified})

    assert response.status_code == 304


async def test_feed_if_none_match_takes_precedence_over_if_modified_since(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    first = await get_feed(client)
    later = http_date(T_A_SECOND + timedelta(days=1))
    earlier = http_date(T_A_SECOND - timedelta(days=1))

    mismatch_and_fresh = await get_feed(
        client, headers={"If-None-Match": '"other"', "If-Modified-Since": later}
    )
    match_and_stale = await get_feed(
        client, headers={"If-None-Match": first.headers["etag"], "If-Modified-Since": earlier}
    )
    match_and_fresh = await get_feed(
        client, headers={"If-None-Match": first.headers["etag"], "If-Modified-Since": later}
    )

    assert mismatch_and_fresh.status_code == 200
    assert match_and_stale.status_code == 304
    assert match_and_fresh.status_code == 304


async def test_feed_if_modified_since_is_ignored_when_there_is_no_last_modified(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    response = await get_feed(client, headers={"If-Modified-Since": http_date(ANCHOR)})

    assert response.status_code == 200
    assert "last-modified" not in response.headers


async def test_feed_if_modified_since_for_a_filtered_feed_uses_its_own_last_modified(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    # The MCZ_LEFT feed is last modified at T_B (older than T_A).
    fresh = await get_feed(client, camera="MCZ_LEFT", headers={"If-Modified-Since": http_date(T_B)})
    stale = await get_feed(
        client,
        camera="MCZ_LEFT",
        headers={"If-Modified-Since": http_date(T_B - timedelta(seconds=1))},
    )

    assert fresh.status_code == 304
    assert stale.status_code == 200


async def test_if_modified_since_does_not_change_other_endpoints(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(
        "/api/v2/rovers", headers={"If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"}
    )

    assert response.status_code == 200


# --- rate limit ---------------------------------------------------------------------------------


async def test_feed_requests_count_against_the_rate_limit(
    settings: Settings, world: dict[str, int]
) -> None:
    app = create_app(limited_settings(settings, per_minute=2))
    async with running(app):
        app.state.rate_limiter.clock = Clock()
        async with client_for(app) as client:
            statuses = [
                (await client.get("/api/v2/rovers/perseverance/feed.xml")).status_code
                for _ in range(3)
            ]
            limited = await client.get("/api/v2/rovers/perseverance/feed.xml")

    assert statuses == [200, 200, 429]
    assert_error(limited, 429, "rate_limited")


# --- v1 and the photo objects stay as they were ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/api/v1/rovers/perseverance/feed.xml", id="v1"),
        pytest.param("/mars-photos/api/v1/rovers/perseverance/feed.xml", id="v1-alt"),
    ],
)
async def test_feed_does_not_exist_in_v1(
    client: httpx.AsyncClient, world: dict[str, int], path: str
) -> None:
    response = await client.get(path)

    assert response.status_code == 404
    assert "xml" not in response.headers.get("content-type", "")


async def test_v2_photo_objects_do_not_expose_feed_at(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    listing = await client.get("/api/v2/rovers/perseverance/photos", params={"sol": 2032})
    detail = await client.get(f"/api/v2/photos/{world['A1']}")

    assert listing.status_code == detail.status_code == 200
    for photo in listing.json()["items"]:
        assert list(photo) == PHOTO_KEYS
    assert list(detail.json()) == PHOTO_DETAIL_KEYS
    assert "feed_at" not in listing.text and "feed_at" not in detail.text


async def test_v1_photo_responses_do_not_expose_feed_at(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    listing = await client.get("/api/v1/rovers/perseverance/photos", params={"sol": 2032})

    assert listing.status_code == 200
    assert list(listing.json()) == ["photos"]
    assert "feed_at" not in listing.text
