"""GET /api/v2/rovers/{rover}/photos?site=&drive_from=&drive_to= (docs/API.md; stage 12, B1).

One dataset is committed once per module (like `v2_world`, so this module must not use
`clean_db`). Photos sit on stops of two sites over four sols; some have no site/drive.

Contract points covered: `site` alone; `site` with an inclusive `drive_from`/`drive_to` range
(either bound may be omitted); `drive_*` without `site` -> 422; `drive_from > drive_to` -> 422;
bounds 0..2147483647; AND with `sol`, `camera`, `sample_type`; photos without `site`/`drive`
never match; the sort order is unchanged; `total` and pagination; `nearest_sols` ignores the
filters; the filters are part of the cache key (ETag); other rovers keep their own stops.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.api.v2.conftest import (
    INT32_MAX,
    V2,
    assert_error,
    assert_validation_error,
)
from tests.conftest import _clean_database, build_record

pytestmark = pytest.mark.api

P = f"{V2}/rovers/perseverance/photos"
BASE = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)

# source_id, sol, site, drive, camera, sample_type; taken_at grows with the list position.
PERSEVERANCE = [
    ("a1", 100, 10, 0, "NAVCAM_LEFT", "full"),
    ("a2", 100, 10, 5, "NAVCAM_RIGHT", "full"),
    ("a3", 101, 10, 5, "NAVCAM_RIGHT", "full"),
    ("a4", 101, 10, 12, "NAVCAM_RIGHT", "subframe"),
    ("a5", 102, 11, 0, "NAVCAM_LEFT", "full"),
    ("a6", 102, 11, 7, "NAVCAM_LEFT", "full"),
    ("a7", 102, 10, 20, "NAVCAM_LEFT", "full"),
    ("z0", 103, 0, 0, "NAVCAM_LEFT", "full"),
    ("n1", 103, None, None, "NAVCAM_LEFT", "full"),
    ("n2", 103, None, None, "NAVCAM_RIGHT", "full"),
]
CURIOSITY = [
    ("c1", 50, 10, 5, "FHAZ", "full"),
    ("c2", 50, 99, 1, "FHAZ", "full"),
]


def newest_first(ids: list[str]) -> list[str]:
    """Expected list order: sol DESC, taken_at DESC (list position), id DESC (same position)."""
    sols = {row[0]: row[1] for row in PERSEVERANCE}
    position = {row[0]: n for n, row in enumerate(PERSEVERANCE)}
    return sorted(ids, key=lambda i: (sols[i], position[i]), reverse=True)


def _records(rows: list[tuple[Any, ...]]) -> list[Any]:
    return [
        build_record(
            source_id=source_id,
            sol=sol,
            site=site,
            drive=drive,
            camera_code=camera,
            sample_type=sample_type,
            taken_at=BASE + timedelta(days=sol, minutes=n),
            earth_date=(BASE + timedelta(days=sol)).date(),
        )
        for n, (source_id, sol, site, drive, camera, sample_type) in enumerate(rows)
    ]


async def _seed(factory: async_sessionmaker[AsyncSession], rover: str, rows: list) -> None:
    records = _records(rows)
    async with factory() as session:
        for record in records:  # one by one: ids follow the list order
            await upsert_photos(session, rover, [record])
        await recompute_sol_summaries(session, rover, {r.sol for r in records})
        await refresh_rover_stats(session, rover)
        await session.commit()


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def site_world(migrated_db: str) -> AsyncIterator[None]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean_database(factory)
        await _seed(factory, "perseverance", PERSEVERANCE)
        await _seed(factory, "curiosity", CURIOSITY)
        yield
    finally:
        try:
            await _clean_database(factory)
        finally:
            await engine.dispose()


async def source_ids(client: httpx.AsyncClient, url: str = P, **params: Any) -> list[str]:
    response = await client.get(url, params={"per_page": 100, **params})
    assert response.status_code == 200, response.text
    return [item["source"]["id"] for item in response.json()["items"]]


# --- site ----------------------------------------------------------------------------------------


async def test_site_only_returns_every_photo_of_that_site_in_list_order(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(P, params={"site": 10})

    body = response.json()
    assert [i["source"]["id"] for i in body["items"]] == newest_first(
        ["a1", "a2", "a3", "a4", "a7"]
    )
    assert body["total"] == 5
    assert body["nearest_sols"] is None


async def test_site_of_another_stop_group_returns_only_its_photos(
    client: httpx.AsyncClient,
) -> None:
    assert await source_ids(client, site=11) == newest_first(["a5", "a6"])


async def test_site_zero_is_a_valid_site(client: httpx.AsyncClient) -> None:
    assert await source_ids(client, site=0) == ["z0"]


async def test_unknown_site_returns_empty_page_with_total_zero(client: httpx.AsyncClient) -> None:
    body = (await client.get(P, params={"site": 999})).json()

    assert (body["items"], body["total"], body["nearest_sols"]) == ([], 0, None)


# --- drive range ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"drive_from": 5, "drive_to": 5}, ["a2", "a3"], id="exact-stop-two-sols"),
        pytest.param({"drive_from": 0, "drive_to": 0}, ["a1"], id="exact-zero"),
        pytest.param({"drive_from": 5}, ["a2", "a3", "a4", "a7"], id="half-open-from"),
        pytest.param({"drive_to": 5}, ["a1", "a2", "a3"], id="half-open-to"),
        pytest.param({"drive_from": 6, "drive_to": 19}, ["a4"], id="range-inside"),
        pytest.param({"drive_from": 0, "drive_to": 20}, ["a1", "a2", "a3", "a4", "a7"], id="both"),
        pytest.param({"drive_from": 12, "drive_to": 20}, ["a4", "a7"], id="inclusive-bounds"),
        pytest.param({"drive_from": 21}, [], id="beyond-last-stop"),
        pytest.param({"drive_from": 6, "drive_to": 11}, [], id="gap-between-stops"),
        pytest.param({"drive_from": INT32_MAX}, [], id="int32-max-from"),
        pytest.param({"drive_to": INT32_MAX}, ["a1", "a2", "a3", "a4", "a7"], id="int32-max-to"),
    ],
)
async def test_site_with_drive_range_filters_inclusively_on_site_10(
    client: httpx.AsyncClient, params: dict[str, int], expected: list[str]
) -> None:
    got = await source_ids(client, site=10, **params)

    assert got == newest_first(expected)


async def test_drive_range_total_counts_the_whole_filtered_set(client: httpx.AsyncClient) -> None:
    body = (await client.get(P, params={"site": 10, "drive_from": 5, "drive_to": 5})).json()

    assert body["total"] == 2
    assert [i["sol"] for i in body["items"]] == [101, 100]  # one stop, several sols


async def test_photos_without_site_or_drive_never_match(client: httpx.AsyncClient) -> None:
    every = await source_ids(client, site=0, drive_from=0)
    wide = []
    for site in (0, 10, 11):
        wide += await source_ids(client, site=site, drive_from=0, drive_to=INT32_MAX)

    assert "n1" not in every + wide and "n2" not in every + wide
    assert sorted(wide) == sorted(["z0", "a1", "a2", "a3", "a4", "a7", "a5", "a6"])


async def test_filter_is_scoped_to_the_rover_of_the_url(client: httpx.AsyncClient) -> None:
    curiosity = f"{V2}/rovers/curiosity/photos"

    assert await source_ids(client, curiosity, site=10, drive_from=5, drive_to=5) == ["c1"]
    assert await source_ids(client, curiosity, site=11) == []
    assert await source_ids(client, curiosity, site=99) == ["c2"]


# --- combination with other filters --------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"sol": 101}, ["a3", "a4"], id="sol"),
        pytest.param({"sol": 100, "drive_from": 5, "drive_to": 5}, ["a2"], id="sol-and-stop"),
        pytest.param({"camera": "navcam_left"}, ["a1", "a7"], id="camera-any-case"),
        pytest.param({"sample_type": "subframe"}, ["a4"], id="sample-type"),
        pytest.param({"sample_type": "full", "drive_from": 12}, ["a7"], id="sample-type-drive"),
        pytest.param({"sol": 102, "camera": "NAVCAM_RIGHT"}, [], id="empty-intersection"),
    ],
)
async def test_site_filters_combine_with_the_other_parameters_by_and(
    client: httpx.AsyncClient, params: dict[str, Any], expected: list[str]
) -> None:
    assert await source_ids(client, site=10, **params) == newest_first(expected)


async def test_earth_date_combines_with_site(client: httpx.AsyncClient) -> None:
    day = (BASE + timedelta(days=101)).date().isoformat()

    assert await source_ids(client, site=10, earth_date=day) == newest_first(["a3", "a4"])


# --- pagination ----------------------------------------------------------------------------------


async def test_site_filter_paginates_with_total_of_the_filtered_set(
    client: httpx.AsyncClient,
) -> None:
    expected = newest_first(["a1", "a2", "a3", "a4", "a7"])
    pages = []
    for page in (1, 2, 3):
        body = (await client.get(P, params={"site": 10, "per_page": 2, "page": page})).json()
        assert (body["total"], body["page"], body["per_page"]) == (5, page, 2)
        pages.append([i["source"]["id"] for i in body["items"]])

    assert pages == [expected[:2], expected[2:4], expected[4:]]


# --- validation ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"drive_from": 1}, id="drive-from-without-site"),
        pytest.param({"drive_to": 1}, id="drive-to-without-site"),
        pytest.param({"drive_from": 1, "drive_to": 2}, id="both-without-site"),
        pytest.param({"drive_from": 0}, id="drive-from-zero-without-site"),
    ],
)
async def test_drive_without_site_returns_422(
    client: httpx.AsyncClient, params: dict[str, int]
) -> None:
    response = await client.get(P, params=params)

    assert_validation_error(response, "site", "drive_from", "drive_to")


async def test_drive_from_greater_than_drive_to_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get(P, params={"site": 10, "drive_from": 6, "drive_to": 5})

    assert_validation_error(response, "drive_from", "drive_to")


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"site": -1}, "site", id="site-negative"),
        pytest.param({"site": INT32_MAX + 1}, "site", id="site-over-int32"),
        pytest.param({"site": "abc"}, "site", id="site-text"),
        pytest.param({"site": "1.5"}, "site", id="site-fraction"),
        pytest.param({"site": "9" * 5000}, "site", id="site-huge"),
        pytest.param({"site": 10, "drive_from": -1}, "drive_from", id="drive-from-negative"),
        pytest.param({"site": 10, "drive_to": -1}, "drive_to", id="drive-to-negative"),
        pytest.param({"site": 10, "drive_from": INT32_MAX + 1}, "drive_from", id="from-over-int32"),
        pytest.param({"site": 10, "drive_to": INT32_MAX + 1}, "drive_to", id="to-over-int32"),
        pytest.param({"site": 10, "drive_from": "abc"}, "drive_from", id="drive-from-text"),
        pytest.param({"site": 10, "drive_to": "x1"}, "drive_to", id="drive-to-text"),
    ],
)
async def test_malformed_site_or_drive_returns_422_naming_the_field(
    client: httpx.AsyncClient, params: dict[str, Any], field: str
) -> None:
    response = await client.get(P, params=params)

    details = assert_validation_error(response, field, "site", "drive_from", "drive_to")
    assert any(item["field"] == field for item in details), details


async def test_unknown_rover_with_site_filter_is_still_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{V2}/rovers/pluto/photos", params={"site": 1})

    assert_error(response, 404, "rover_not_found")


# --- nearest_sols and caching --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "nearest"),
    [
        pytest.param({"sol": 102, "site": 999}, {"before": 101, "after": 103}, id="site-has-none"),
        pytest.param({"sol": 50, "site": 10}, {"before": None, "after": 100}, id="before-all"),
        pytest.param(
            {"sol": 999, "site": 10, "drive_from": 0},
            {"before": 103, "after": None},
            id="after-all",
        ),
        pytest.param(
            {"sol": 100, "site": 11},
            {"before": None, "after": 101},
            id="sol-with-photos-of-other-site",
        ),
        pytest.param(
            {"sol": 101, "site": 10, "camera": "NAVCAM_LEFT"},
            {"before": 100, "after": 102},
            id="camera-ignored",
        ),
    ],
)
async def test_nearest_sols_ignores_site_and_drive_filters(
    client: httpx.AsyncClient, params: dict[str, Any], nearest: dict[str, int | None]
) -> None:
    body = (await client.get(P, params=params)).json()

    assert body["total"] == 0
    assert body["nearest_sols"] == nearest


async def test_nearest_sols_is_null_when_the_site_filter_matches_something(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(P, params={"sol": 101, "site": 10})).json()

    assert body["total"] == 2
    assert body["nearest_sols"] is None


async def test_etag_differs_for_different_site_and_drive_filters(
    client: httpx.AsyncClient,
) -> None:
    variants = [
        {},
        {"site": 10},
        {"site": 11},
        {"site": 10, "drive_from": 5},
        {"site": 10, "drive_from": 5, "drive_to": 5},
        {"site": 10, "drive_to": 5},
    ]
    etags = []
    for params in variants:
        response = await client.get(P, params=params)
        assert response.status_code == 200
        etags.append(response.headers["etag"])

    assert len(set(etags)) == len(variants)


async def test_conditional_get_of_a_site_filter_returns_304(client: httpx.AsyncClient) -> None:
    first = await client.get(P, params={"site": 10, "drive_from": 5, "drive_to": 5})
    second = await client.get(
        P,
        params={"site": 10, "drive_from": 5, "drive_to": 5},
        headers={"If-None-Match": first.headers["etag"]},
    )

    assert second.status_code == 304
    assert second.content == b""
