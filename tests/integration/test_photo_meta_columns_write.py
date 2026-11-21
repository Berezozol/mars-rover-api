"""AC-14: the write path fills `local_time_s`, `width`, `height` from `extra` (stage 18B-2).

Two layers: `upsert_photos` on hand-built records (every rover, boundaries, garbage) and a
fixture-based sync through the real adapters with the feeds mocked by respx (no network).
Expected values are computed here, from the raw feed items or the stated `extra`, never by the
application's own parser.
"""

import copy
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.repositories.photos import upsert_photos
from app.services.sync import SyncService
from app.sources import http
from app.sources.m2020 import M2020Adapter
from app.sources.msl import MslAdapter
from tests.conftest import build_record
from tests.integration._fakes import fetch_all
from tests.integration._meta_seed import null_meta_columns, stored_meta
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


async def store(factory: SessionFactory, rover: str, **overrides: Any) -> str:
    record = build_record(**overrides)
    async with factory() as session:
        await upsert_photos(session, rover, [record])
        await session.commit()
    return record.source_id


# --- upsert_photos on hand-built records -------------------------------------------------

M2020_TIME = {"date_taken_mars": "Sol-02024M13:07:38.713"}  # 13:07:38 -> 47258 s


@pytest.mark.parametrize(
    ("rover", "camera", "extra", "expected"),
    [
        (
            "curiosity",
            "FHAZ",
            {"extended": {"lmst": "Sol-05073M11:25:43.222", "dimension": "(1920,1200)"}},
            (41143, None, None),  # Curiosity never has a size, even with a dimension
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {**M2020_TIME, "extended": {"dimension": "(1648,1200)"}},
            (47258, 1648, 1200),
        ),
        (
            "ingenuity",
            "HELI_NAV",
            {"date_taken_mars": "Sol-01069M11:41:52.53212", "extended": {"dimension": "(640,480)"}},
            (42112, 640, 480),
        ),
        (
            "spirit",
            "NAVCAM",
            {"local_true_solar_time": "12:45:26", "line_samples": 1024, "lines": 800},
            (45926, 1024, 800),
        ),
        (
            "opportunity",
            "PANCAM",
            {"local_true_solar_time": "01:02:03", "line_samples": 512, "lines": 512},
            (3723, 512, 512),
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {"date_taken_mars": "Sol-00001M00:00:00.000", "extended": {"dimension": "(1,1)"}},
            (0, 1, 1),
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {
                "date_taken_mars": "Sol-00001M23:59:59.999",
                "extended": {"dimension": "(32767,32767)"},
            },
            (86399, 32767, 32767),  # truncated, never rounded to 86400
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {"date_taken_mars": "UNK", "extended": {"dimension": "UNK"}},
            (None, None, None),
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {},
            (None, None, None),
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {**M2020_TIME, "extended": {"dimension": "(32768,100)"}},
            (47258, None, None),  # over smallint: both size columns NULL, time kept
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {**M2020_TIME, "extended": {"dimension": "(100,32768)"}},
            (47258, None, None),
        ),
        (
            "perseverance",
            "NAVCAM_LEFT",
            {**M2020_TIME, "extended": {"dimension": "(0,5)"}},
            (47258, None, None),  # a non-positive size is no size
        ),
        (
            "spirit",
            "NAVCAM",
            {"local_true_solar_time": None, "line_samples": 1024, "lines": None},
            (None, None, None),  # width and height are never NULL separately
        ),
    ],
    ids=[
        "curiosity-lmst-ignores-dimension",
        "perseverance",
        "ingenuity",
        "spirit-ltst",
        "opportunity-ltst",
        "midnight-and-1x1",
        "last-second-and-max-size",
        "unk-everything",
        "empty-extra",
        "width-over-smallint",
        "height-over-smallint",
        "zero-width",
        "mer-null-lines",
    ],
)
async def test_ac14_upsert_stores_columns_from_extra(
    clean_db: SessionFactory,
    rover: str,
    camera: str,
    extra: dict[str, Any],
    expected: tuple[int | None, int | None, int | None],
) -> None:
    source_id = await store(clean_db, rover, camera_code=camera, extra=extra)

    assert (await stored_meta(clean_db))[source_id] == expected


async def test_ac14_upsert_of_a_batch_fills_every_row_independently(
    clean_db: SessionFactory,
) -> None:
    records = [
        build_record(
            source_id=f"batch-{n}",
            camera_code="NAVCAM_LEFT",
            extra={
                "date_taken_mars": f"Sol-00100M{n:02d}:00:00.000",
                "extended": {"dimension": f"({100 + n},{200 + n})"},
            },
        )
        for n in range(1, 6)
    ] + [build_record(source_id="batch-none", camera_code="NAVCAM_LEFT", extra={})]
    async with clean_db() as session:
        await upsert_photos(session, "perseverance", records)
        await session.commit()

    stored = await stored_meta(clean_db)
    assert {k: v for k, v in stored.items() if k != "batch-none"} == {
        f"batch-{n}": (n * 3600, 100 + n, 200 + n) for n in range(1, 6)
    }
    assert stored["batch-none"] == (None, None, None)


async def test_ac14_second_write_of_the_same_photo_does_not_touch_updated_at(
    clean_db: SessionFactory,
) -> None:
    record = build_record(
        source_id="same-twice",
        camera_code="NAVCAM_LEFT",
        extra={**M2020_TIME, "extended": {"dimension": "(1648,1200)"}},
    )

    async def write_and_read() -> dict[str, Any]:
        async with clean_db() as session:
            await upsert_photos(session, "perseverance", [record])
            await session.commit()
        [row] = await fetch_all(
            clean_db,
            "SELECT updated_at, local_time_s, width, height FROM photos",
        )
        return row

    first = await write_and_read()
    second = await write_and_read()

    assert (second["local_time_s"], second["width"], second["height"]) == (47258, 1648, 1200)
    assert second["updated_at"] == first["updated_at"]


async def test_ac14_rewrite_with_changed_extra_updates_the_columns(
    clean_db: SessionFactory,
) -> None:
    first = build_record(
        source_id="changes",
        camera_code="NAVCAM_LEFT",
        extra={**M2020_TIME, "extended": {"dimension": "(1648,1200)"}},
    )
    changed = build_record(
        source_id="changes",
        camera_code="NAVCAM_LEFT",
        extra={"date_taken_mars": "Sol-02024M01:00:00.000", "extended": {"dimension": "(800,600)"}},
    )
    async with clean_db() as session:
        await upsert_photos(session, "perseverance", [first])
        await session.commit()
    async with clean_db() as session:
        await upsert_photos(session, "perseverance", [changed])
        await session.commit()

    assert (await stored_meta(clean_db))["changes"] == (3600, 800, 600)


async def test_ac14_resync_of_a_photo_with_empty_columns_fills_them(
    clean_db: SessionFactory,
) -> None:
    """A photo stored before migration 0013 is completed by the next sync that sees it."""
    record = build_record(
        source_id="old-photo",
        camera_code="NAVCAM_LEFT",
        extra={**M2020_TIME, "extended": {"dimension": "(1648,1200)"}},
    )
    async with clean_db() as session:
        await upsert_photos(session, "perseverance", [record])
        await session.commit()
    await null_meta_columns(clean_db)
    assert (await stored_meta(clean_db))["old-photo"] == (None, None, None)

    async with clean_db() as session:
        await upsert_photos(session, "perseverance", [record])
        await session.commit()

    assert (await stored_meta(clean_db))["old-photo"] == (47258, 1648, 1200)


# --- fixture-based sync through the real adapters ------------------------------------------


@pytest.fixture
async def upstream_client() -> AsyncIterator[http.UpstreamClient]:
    clock = FakeClock()
    client = http.UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def service(session_factory: SessionFactory, settings: Settings) -> SyncService:
    return SyncService(session_factory, settings.model_copy(update={"sync_chunk_sols": 10}))


def raw_m2020_time_and_size(raw: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Expected columns of a raw M2020 feed item (independent of the application's parser)."""
    stamp = re.search(r"M(\d\d):(\d\d):(\d\d)", str(raw.get("date_taken_mars")))
    seconds = (
        int(stamp[1]) * 3600 + int(stamp[2]) * 60 + int(stamp[3]) if stamp is not None else None
    )
    size = re.fullmatch(r"\((\d+),(\d+)\)", str(raw.get("extended", {}).get("dimension")))
    if size is None:
        return seconds, None, None
    return seconds, int(size[1]), int(size[2])


def raw_msl_time(raw: dict[str, Any]) -> int | None:
    stamp = re.search(r"M(\d\d):(\d\d):(\d\d)", str((raw.get("extended") or {}).get("lmst")))
    return int(stamp[1]) * 3600 + int(stamp[2]) * 60 + int(stamp[3]) if stamp else None


def m2020_items() -> list[dict[str, Any]]:
    """Four real Perseverance items of sol 1000 plus the synthetic one whose dimension is UNK."""
    real = load_fixture("m2020/range_1000_page0.json")["images"]
    quirk = next(
        i
        for i in load_fixture("synthetic/m2020_quirks.json")["images"]
        if i["imageid"] == "SYN_M20_QUIRKS"
    )
    assert quirk["extended"]["dimension"] == "UNK"
    return [*copy.deepcopy(real), copy.deepcopy(quirk)]


def m2020_responder(images: list[dict[str, Any]]) -> Any:
    template = load_fixture("m2020/range_1000_page0.json")

    def respond(request: httpx.Request) -> httpx.Response:
        params = query(request)
        low = int(params["condition_2"].split(":")[0])
        high = int(params["condition_3"].split(":")[0])
        selected = [i for i in images if low <= int(i["sol"]) <= high]
        page = int(params["page"])
        body = {
            **template,
            "page": page if page == 0 else str(page),
            "total_results": len(selected),
            "images": selected if page == 0 else [],
        }
        return httpx.Response(200, json=body)

    return respond


async def test_ac14_sync_of_perseverance_fixture_stores_columns_for_every_photo(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    images = m2020_items()
    upstream_router.get(M2020_URL).mock(side_effect=m2020_responder(images))

    result = await service.run(
        M2020Adapter(upstream_client, make_settings()), from_sol=995, to_sol=1000
    )

    assert result.status == "success"
    stored = await stored_meta(session_factory)
    expected = {i["imageid"]: raw_m2020_time_and_size(i) for i in images}
    assert len(stored) >= 5
    for source_id, columns in stored.items():
        assert columns == expected[source_id], source_id
    assert stored["SYN_M20_QUIRKS"][1:] == (None, None)  # "UNK" dimension
    assert stored["SYN_M20_QUIRKS"][0] == 16 * 3600 + 39 * 60 + 47
    real = {sid: c for sid, c in stored.items() if sid != "SYN_M20_QUIRKS"}
    assert all(t is not None and w and h for t, w, h in real.values())


async def test_ac14_sync_of_curiosity_fixture_stores_time_and_never_a_size(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    pages = {k: load_fixture(f"msl/sol_4327_page{k}.json") for k in range(3)}
    raw = [i for page in pages.values() for i in page["items"] if not i["is_thumbnail"]]
    empty = {"items": [], "more": False, "total": 0, "page": 0, "per_page": 200}

    def respond(request: httpx.Request) -> httpx.Response:
        params = query(request)
        if int(params["condition_2"].split(":")[0]) != 4327:
            return httpx.Response(200, json=empty)
        return httpx.Response(200, json=pages[int(params["page"])])

    upstream_router.get(MSL_URL).mock(side_effect=respond)

    result = await service.run(
        MslAdapter(upstream_client, make_settings()), from_sol=4326, to_sol=4327
    )

    assert result.status == "success"
    stored = await stored_meta(session_factory)
    assert len(stored) == len(raw)
    for item in raw:
        assert stored[str(item["id"])] == (raw_msl_time(item), None, None), item["id"]
    assert any(t is not None for t, _, _ in stored.values())  # fixture has recorded LMST values
    assert any(t is None for t, _, _ in stored.values())  # and `lmst: null` ones


async def test_ac14_second_sync_of_the_same_range_changes_no_row(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    upstream_router.get(M2020_URL).mock(side_effect=m2020_responder(m2020_items()))
    adapter = M2020Adapter(upstream_client, make_settings())
    await service.run(adapter, from_sol=995, to_sol=1000)
    before = await fetch_all(session_factory, "SELECT id, updated_at FROM photos ORDER BY id")

    await service.run(adapter, from_sol=995, to_sol=1000)

    after = await fetch_all(session_factory, "SELECT id, updated_at FROM photos ORDER BY id")
    assert before and after == before
