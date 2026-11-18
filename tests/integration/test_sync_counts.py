"""Stage 7: the sync maintains camera_sol_counts and sol_source_counts (migration 0004).

Contract (stage-7 brief, docs/ARCHITECTURE.md "Модель данных"):
- camera_sol_counts (rover_id, sol, camera_id) -> total_photos, full_photos
  (sample_type = 'full'); rows with 0 photos are not stored; recomputed by the sync for the
  touched sols together with sol_summaries.
- sol_source_counts (rover_id, sol) -> received, dropped (received but not stored after
  normalization), source_total (per-sol total declared by the source, nullable), fetched_at;
  upserted by the sync for every delivered sol with the latest fetch.

The service commits, so every test uses `clean_db`. Feeds are mocked with respx on records
trimmed from tests/fixtures/upstream/ (no network).
"""

import copy
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.sync import SyncService
from app.sources import http
from app.sources.m2020 import M2020Adapter
from app.sources.msl import MslAdapter
from tests.integration._fakes import FakeAdapter, fetch_all, photo_sols, sol_records
from tests.unit._upstream import (
    M2020_URL,
    MSL_URL,
    FakeClock,
    load_fixture,
    make_settings,
    query,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def service(session_factory: SessionFactory, settings: Settings) -> SyncService:
    return SyncService(session_factory, settings.model_copy(update={"sync_chunk_sols": 10}))


async def camera_counts(
    factory: SessionFactory, rover_id: str
) -> dict[tuple[int, str], tuple[int, int]]:
    """(sol, camera code) -> (total_photos, full_photos) from camera_sol_counts."""
    rows = await fetch_all(
        factory,
        "SELECT s.sol, c.code, s.total_photos, s.full_photos FROM camera_sol_counts s "
        "JOIN cameras c ON c.id = s.camera_id WHERE s.rover_id = :rover",
        rover=rover_id,
    )
    return {(r["sol"], r["code"]): (r["total_photos"], r["full_photos"]) for r in rows}


async def group_by_photos(
    factory: SessionFactory, rover_id: str
) -> dict[tuple[int, str], tuple[int, int]]:
    """The same numbers computed straight from photos (the reference)."""
    rows = await fetch_all(
        factory,
        "SELECT p.sol, c.code, count(*) AS total, "
        "count(*) FILTER (WHERE p.sample_type = 'full') AS full "
        "FROM photos p JOIN cameras c ON c.id = p.camera_id WHERE p.rover_id = :rover "
        "GROUP BY p.sol, c.code",
        rover=rover_id,
    )
    return {(r["sol"], r["code"]): (r["total"], r["full"]) for r in rows}


async def source_counts(factory: SessionFactory, rover_id: str) -> dict[int, dict[str, Any]]:
    rows = await fetch_all(
        factory, "SELECT * FROM sol_source_counts WHERE rover_id = :rover", rover=rover_id
    )
    return {row["sol"]: row for row in rows}


# --- camera_sol_counts -------------------------------------------------------------------


def curiosity_mix() -> FakeAdapter:
    """Sol 100: FHAZ 2 full + 1 subframe, NAVCAM 1 full; sol 101: MAST 1 downsampled."""
    adapter = FakeAdapter("curiosity")
    adapter.add(sol_records("curiosity", 100, 2, camera_code="FHAZ"))
    adapter.add(sol_records("curiosity", 100, 1, camera_code="FHAZ", sample_type="subframe"))
    adapter.add([*sol_records("curiosity", 100, 3, camera_code="NAVCAM")][2:])
    adapter.add(sol_records("curiosity", 101, 1, camera_code="MAST", sample_type="downsampled"))
    return adapter


async def test_sync_writes_camera_sol_counts_per_sol_and_camera(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(curiosity_mix(), from_sol=95, to_sol=105)

    counts = await camera_counts(session_factory, "curiosity")
    assert counts == {
        (100, "FHAZ"): (3, 2),
        (100, "NAVCAM"): (1, 1),
        (101, "MAST"): (1, 0),
    }
    assert counts == await group_by_photos(session_factory, "curiosity")


async def test_sync_camera_sol_counts_has_no_zero_rows(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(curiosity_mix(), from_sol=95, to_sol=105)

    rows = await fetch_all(
        session_factory, "SELECT * FROM camera_sol_counts WHERE total_photos <= 0"
    )
    assert rows == []
    sols = {sol for sol, _ in await camera_counts(session_factory, "curiosity")}
    assert sols == {100, 101}  # sols 95..99, 102..105 have no photos -> no rows


async def test_sync_resync_recomputes_camera_counts_photo_added_and_camera_moved(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = FakeAdapter("curiosity")
    first.add(sol_records("curiosity", 100, 2, camera_code="FHAZ"))  # curiosity-100-0, -1
    first.add([*sol_records("curiosity", 100, 3, camera_code="NAVCAM")][2:])  # curiosity-100-2
    await service.run(first, from_sol=100, to_sol=100)
    assert await camera_counts(session_factory, "curiosity") == {
        (100, "FHAZ"): (2, 2),
        (100, "NAVCAM"): (1, 1),
    }

    second = FakeAdapter("curiosity")
    second.add(sol_records("curiosity", 100, 2, camera_code="FHAZ"))
    second.add([*sol_records("curiosity", 100, 4, camera_code="FHAZ")][3:])  # new: -3
    second.add([*sol_records("curiosity", 100, 3, camera_code="MAST")][2:])  # -2 moved
    await service.run(second, from_sol=100, to_sol=100)

    counts = await camera_counts(session_factory, "curiosity")
    assert counts == {(100, "FHAZ"): (3, 3), (100, "MAST"): (1, 1)}  # NAVCAM row is gone
    assert counts == await group_by_photos(session_factory, "curiosity")


async def test_sync_untouched_sols_keep_their_camera_counts(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(curiosity_mix(), from_sol=95, to_sol=105)
    before = await camera_counts(session_factory, "curiosity")

    await service.run(
        FakeAdapter("curiosity").add(sol_records("curiosity", 200, 2, camera_code="RHAZ")),
        from_sol=200,
        to_sol=200,
    )

    after = await camera_counts(session_factory, "curiosity")
    assert after == {**before, (200, "RHAZ"): (2, 2)}


async def test_sync_camera_counts_new_camera_created_by_sync(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance").add(
        sol_records("perseverance", 7, 2, camera_code="BRAND_NEW_CAM")
    )

    await service.run(adapter, from_sol=7, to_sol=7)

    assert await camera_counts(session_factory, "perseverance") == {(7, "BRAND_NEW_CAM"): (2, 2)}


# --- sol_source_counts through the real adapters ------------------------------------------


@pytest.fixture
async def upstream_client() -> AsyncIterator[http.UpstreamClient]:
    clock = FakeClock()
    client = http.UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock)
    try:
        yield client
    finally:
        await client.aclose()


def m2020_images() -> list[dict[str, Any]]:
    """Sol 1000: 4 real images + 1 thumbnail + 1 without full_res; sol 999: 2 images."""
    base = load_fixture("m2020/range_1000_page0.json")["images"]
    assert len(base) == 4
    images = copy.deepcopy(base)
    thumb = copy.deepcopy(base[0])
    thumb["imageid"] += "_THUMB"
    thumb["sample_type"] = "Thumbnail"
    broken = copy.deepcopy(base[1])
    broken["imageid"] += "_NOFULL"
    broken["image_files"]["full_res"] = None
    images += [thumb, broken]
    for n in range(2):
        clone = copy.deepcopy(base[n])
        clone["imageid"] += f"_SOL999_{n}"
        clone["sol"] = 999
        images.append(clone)
    return images


def m2020_responder(images: list[dict[str, Any]]) -> Any:
    template = load_fixture("m2020/range_1000_page0.json")

    def respond(request: httpx.Request) -> httpx.Response:
        params = query(request)
        low = int(params["condition_2"].split(":")[0])
        high = int(params["condition_3"].split(":")[0])
        selected = [img for img in images if low <= int(img["sol"]) <= high]
        page = int(params["page"])
        body = {
            **template,
            "page": page if page == 0 else str(page),
            "total_results": len(selected),
            "images": selected if page == 0 else [],
        }
        return httpx.Response(200, json=body)

    return respond


async def test_sync_m2020_writes_sol_source_counts_with_dropped_records(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    upstream_router.get(M2020_URL).mock(side_effect=m2020_responder(m2020_images()))
    adapter = M2020Adapter(upstream_client, make_settings())

    result = await service.run(adapter, from_sol=995, to_sol=1000)

    assert result.status == "success"
    assert await photo_sols(session_factory, "perseverance") == {1000: 4, 999: 2}
    counts = await source_counts(session_factory, "perseverance")
    assert {999, 1000} <= set(counts) <= set(range(995, 1001))
    # A fetched sol without images may be recorded, but only as "received nothing".
    for sol in set(counts) - {999, 1000}:
        assert (counts[sol]["received"], counts[sol]["dropped"]) == (0, 0)
    assert (counts[1000]["received"], counts[1000]["dropped"]) == (6, 2)
    assert (counts[999]["received"], counts[999]["dropped"]) == (2, 0)
    for row in counts.values():
        assert row["fetched_at"] is not None
        assert row["received"] - row["dropped"] >= 0


def msl_pages(*, drop_page2_thumbnails: bool = False) -> dict[int, dict[str, Any]]:
    pages = {k: load_fixture(f"msl/sol_4327_page{k}.json") for k in range(3)}
    if drop_page2_thumbnails:
        pages[2]["items"] = [item for item in pages[2]["items"] if not item["is_thumbnail"]]
    return pages


def msl_responder(pages_by_sol: dict[int, dict[int, dict[str, Any]]]) -> Any:
    empty = {"items": [], "more": False, "total": 0, "page": 0, "per_page": 200}

    def respond(request: httpx.Request) -> httpx.Response:
        params = query(request)
        sol = int(params["condition_2"].split(":")[0])
        pages = pages_by_sol.get(sol)
        if pages is None:
            return httpx.Response(200, json=empty)
        return httpx.Response(200, json=pages[int(params["page"])])

    return respond


def raw_items(pages: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for page in pages.values() for item in page["items"]]


async def test_sync_msl_writes_sol_source_counts_received_dropped_and_source_total(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    pages = msl_pages()
    route = upstream_router.get(MSL_URL).mock(side_effect=msl_responder({4327: pages}))
    adapter = MslAdapter(upstream_client, make_settings())

    result = await service.run(adapter, from_sol=4326, to_sol=4327)

    assert result.status == "success"
    assert route.call_count == 4  # sol 4327: 3 pages, sol 4326: 1 empty page
    items = raw_items(pages)
    thumbnails = sum(1 for item in items if item["is_thumbnail"])
    stored = (await photo_sols(session_factory, "curiosity"))[4327]
    assert stored == len(items) - thumbnails
    counts = await source_counts(session_factory, "curiosity")
    assert 4327 in counts
    if 4326 in counts:  # fetched, nothing there: may be recorded as received 0
        assert (counts[4326]["received"], counts[4326]["dropped"]) == (0, 0)
    row = counts[4327]
    assert (row["received"], row["dropped"]) == (len(items), thumbnails)
    assert row["source_total"] == 508  # `total` of the per-sol MSL response
    assert row["fetched_at"] is not None


async def test_sync_refetch_overwrites_sol_source_counts(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    route = upstream_router.get(MSL_URL).mock(side_effect=msl_responder({4327: msl_pages()}))
    adapter = MslAdapter(upstream_client, make_settings())
    await service.run(adapter, from_sol=4327, to_sol=4327)
    first = (await source_counts(session_factory, "curiosity"))[4327]

    refetched = msl_pages(drop_page2_thumbnails=True)
    for page in refetched.values():
        page["total"] = 400
    route.mock(side_effect=msl_responder({4327: refetched}))
    await service.run(adapter, from_sol=4327, to_sol=4327)

    counts = await source_counts(session_factory, "curiosity")
    assert set(counts) == {4327}  # upsert, not a second row
    row = counts[4327]
    items = raw_items(refetched)
    thumbnails = sum(1 for item in items if item["is_thumbnail"])
    assert (row["received"], row["dropped"], row["source_total"]) == (
        len(items),
        thumbnails,
        400,
    )
    assert row["received"] < first["received"]
    assert row["fetched_at"] > first["fetched_at"]


async def test_sync_m2020_refetch_overwrites_received_and_dropped(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    images = m2020_images()
    route = upstream_router.get(M2020_URL).mock(side_effect=m2020_responder(images))
    adapter = M2020Adapter(upstream_client, make_settings())
    await service.run(adapter, from_sol=1000, to_sol=1000)

    # The thumbnail is gone upstream and one more real image arrived.
    later = [img for img in images if img["sample_type"] != "Thumbnail"]
    extra = copy.deepcopy(later[0])
    extra["imageid"] += "_LATE"
    later.append(extra)
    route.mock(side_effect=m2020_responder(later))
    await service.run(adapter, from_sol=1000, to_sol=1000)

    row = (await source_counts(session_factory, "perseverance"))[1000]
    assert (row["received"], row["dropped"]) == (6, 1)
    assert (await photo_sols(session_factory, "perseverance"))[1000] == 5


# --- review round: photo moved to another sol, M20 out-of-order duplicates ----------------


async def summaries_by_sol(factory: SessionFactory, rover_id: str) -> dict[int, dict[str, Any]]:
    rows = await fetch_all(
        factory, "SELECT * FROM sol_summaries WHERE rover_id = :rover", rover=rover_id
    )
    return {row["sol"]: row for row in rows}


async def test_sync_photo_moved_to_other_sol_recomputes_old_sol(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = FakeAdapter("curiosity")
    first.add(sol_records("curiosity", 100, 2, camera_code="FHAZ"))  # curiosity-100-0, -1
    await service.run(first, from_sol=100, to_sol=100)

    # The source now files curiosity-100-1 under sol 101 (same source_id, new sol).
    moved = sol_records("curiosity", 100, 2, camera_code="FHAZ")[1]
    second = FakeAdapter("curiosity").add([dataclasses.replace(moved, sol=101)])
    await service.run(second, from_sol=101, to_sol=101)  # sol 100 is outside the range

    assert await photo_sols(session_factory, "curiosity") == {100: 1, 101: 1}
    counts = await camera_counts(session_factory, "curiosity")
    assert counts == {(100, "FHAZ"): (1, 1), (101, "FHAZ"): (1, 1)}
    assert counts == await group_by_photos(session_factory, "curiosity")
    summary = await summaries_by_sol(session_factory, "curiosity")
    assert (summary[100]["total_photos"], summary[100]["full_photos"]) == (1, 1)
    assert summary[101]["total_photos"] == 1


async def test_sync_last_photo_moved_away_removes_old_sol_rows(
    service: SyncService, session_factory: SessionFactory
) -> None:
    [only] = sol_records("curiosity", 100, 1, camera_code="NAVCAM")
    await service.run(FakeAdapter("curiosity").add([only]), from_sol=100, to_sol=100)

    moved = dataclasses.replace(only, sol=102, camera_code="FHAZ")
    await service.run(FakeAdapter("curiosity").add([moved]), from_sol=102, to_sol=102)

    assert await photo_sols(session_factory, "curiosity") == {102: 1}
    assert await camera_counts(session_factory, "curiosity") == {(102, "FHAZ"): (1, 1)}
    assert set(await summaries_by_sol(session_factory, "curiosity")) == {102}


async def test_sync_m2020_sol_reappearing_with_only_duplicates_keeps_counts_consistent(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    base = load_fixture("m2020/range_1000_page0.json")["images"]
    sol_999 = []
    for n in range(2):
        clone = copy.deepcopy(base[n])
        clone["imageid"] += f"_SOL999_{n}"
        clone["sol"] = 999
        sol_999.append(clone)
    # Out of order: sol 1000 (4), sol 999 (2), sol 1000 again with 2 duplicates only.
    images = [*copy.deepcopy(base), *sol_999, copy.deepcopy(base[0]), copy.deepcopy(base[2])]
    upstream_router.get(M2020_URL).mock(side_effect=m2020_responder(images))
    adapter = M2020Adapter(upstream_client, make_settings())

    result = await service.run(adapter, from_sol=999, to_sol=1000)

    assert result.status == "success"
    assert await photo_sols(session_factory, "perseverance") == {1000: 4, 999: 2}
    counts = await source_counts(session_factory, "perseverance")
    row = counts[1000]
    assert row["received"] == 6  # every raw image of the sol, both passes
    assert row["received"] - row["dropped"] == 4  # the duplicates are dropped
    assert (counts[999]["received"], counts[999]["dropped"]) == (2, 0)
