"""Repositories app/repositories/photos.py and rovers.py on the test database."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories.photos import PhotoFilter

pytestmark = pytest.mark.integration

MakeRecord = Callable[..., Any]

CURIOSITY_CODES = ["FHAZ", "RHAZ", "MAST", "CHEMCAM", "MAHLI", "MARDI", "NAVCAM"]


def ts(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2020, 1, day, hour, minute, tzinfo=UTC)


async def photo_rows(session: AsyncSession, rover_id: str) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT p.*, c.code AS camera_code, c.sort_order AS camera_sort_order "
            "FROM photos p JOIN cameras c ON c.id = p.camera_id "
            "WHERE p.rover_id = :rover ORDER BY p.id"
        ),
        {"rover": rover_id},
    )
    return list(result.mappings())


async def camera_rows(session: AsyncSession, rover_id: str) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT id, code, name, sort_order FROM cameras "
            "WHERE rover_id = :rover ORDER BY sort_order"
        ),
        {"rover": rover_id},
    )
    return list(result.mappings())


# --------------------------------------------------------------------------- upsert


async def test_upsert_photos_new_records_inserts_rows_with_all_fields(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    full = make_record(
        source_id="rec-full",
        sol=42,
        camera_code="NAVCAM",
        taken_at=ts(3, 4, 5),
        earth_date=date(2020, 1, 3),
        received_at=ts(4, 1),
        sample_type="full",
        img_full="https://images.example.test/full.png",
        img_small="https://images.example.test/small.jpg",
        img_medium="https://images.example.test/medium.jpg",
        img_large="https://images.example.test/large.jpg",
        title="A title",
        caption="A caption",
        credit="NASA/JPL-Caltech",
        source_link="https://source.example.test/rec-full",
        site=7,
        drive=1234,
        extra={"filter_name": "MXY_L0", "attitude": [1, 2, 3]},
    )
    minimal = make_record(source_id="rec-min", sol=43, camera_code="FHAZ")

    processed = await photos_repo.upsert_photos(db_session, "curiosity", [full, minimal])

    assert processed == 2
    rows = {r["source_id"]: r for r in await photo_rows(db_session, "curiosity")}
    assert set(rows) == {"rec-full", "rec-min"}
    row = rows["rec-full"]
    assert row["rover_id"] == "curiosity"
    assert row["camera_code"] == "NAVCAM"
    assert row["sol"] == 42
    assert row["earth_date"] == date(2020, 1, 3)
    assert row["taken_at"] == ts(3, 4, 5)
    assert row["received_at"] == ts(4, 1)
    assert row["sample_type"] == "full"
    assert row["img_full"] == "https://images.example.test/full.png"
    assert row["img_small"] == "https://images.example.test/small.jpg"
    assert row["img_medium"] == "https://images.example.test/medium.jpg"
    assert row["img_large"] == "https://images.example.test/large.jpg"
    assert row["title"] == "A title"
    assert row["caption"] == "A caption"
    assert row["credit"] == "NASA/JPL-Caltech"
    assert row["source_link"] == "https://source.example.test/rec-full"
    assert (row["site"], row["drive"]) == (7, 1234)
    assert row["extra"] == {"filter_name": "MXY_L0", "attitude": [1, 2, 3]}
    assert row["created_at"] is not None
    assert row["updated_at"] is not None
    minimal_row = rows["rec-min"]
    assert minimal_row["received_at"] is None
    assert minimal_row["img_small"] is None
    assert minimal_row["extra"] == {}


async def test_upsert_photos_empty_input_returns_zero(db_session: AsyncSession) -> None:
    assert await photos_repo.upsert_photos(db_session, "curiosity", []) == 0
    assert await photo_rows(db_session, "curiosity") == []


async def test_upsert_photos_repeated_same_records_no_duplicates_same_ids(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    records = [make_record(sol=10 + i, camera_code=code) for i, code in enumerate(CURIOSITY_CODES)]

    first = await photos_repo.upsert_photos(db_session, "curiosity", records)
    ids_before = {r["source_id"]: r["id"] for r in await photo_rows(db_session, "curiosity")}
    second = await photos_repo.upsert_photos(db_session, "curiosity", records)
    third = await photos_repo.upsert_photos(db_session, "curiosity", list(reversed(records)))
    rows_after = await photo_rows(db_session, "curiosity")

    assert first == second == third == len(records)
    assert len(rows_after) == len(records)
    assert {r["source_id"]: r["id"] for r in rows_after} == ids_before
    assert len(await camera_rows(db_session, "curiosity")) == len(CURIOSITY_CODES)


OLD_TS = datetime(2000, 1, 1, tzinfo=UTC)


async def backdate_timestamps(session: AsyncSession, source_id: str) -> None:
    """now() is frozen inside the test transaction, so move timestamps into the past."""
    await session.execute(
        text("UPDATE photos SET created_at = :ts, updated_at = :ts WHERE source_id = :sid"),
        {"ts": OLD_TS, "sid": source_id},
    )


async def test_upsert_photos_changed_fields_updates_row_keeps_id_and_created_at(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    original = make_record(
        source_id="rec-change", sol=5, camera_code="FHAZ", sample_type="subframe", caption="old"
    )
    await photos_repo.upsert_photos(db_session, "curiosity", [original])
    (before,) = await photo_rows(db_session, "curiosity")
    await backdate_timestamps(db_session, "rec-change")

    changed = make_record(
        source_id="rec-change",
        sol=6,
        camera_code="RHAZ",
        taken_at=ts(2, 0),
        earth_date=date(2020, 1, 2),
        sample_type="full",
        img_full="https://images.example.test/new.png",
        img_large="https://images.example.test/new-large.jpg",
        caption="new",
        site=3,
        extra={"k": "v"},
    )
    processed = await photos_repo.upsert_photos(db_session, "curiosity", [changed])
    (after,) = await photo_rows(db_session, "curiosity")

    assert processed == 1
    assert after["id"] == before["id"]
    assert after["created_at"] == OLD_TS
    assert after["updated_at"] > OLD_TS
    assert after["sol"] == 6
    assert after["camera_code"] == "RHAZ"
    assert after["taken_at"] == ts(2, 0)
    assert after["earth_date"] == date(2020, 1, 2)
    assert after["sample_type"] == "full"
    assert after["img_full"] == "https://images.example.test/new.png"
    assert after["img_large"] == "https://images.example.test/new-large.jpg"
    assert after["caption"] == "new"
    assert after["site"] == 3
    assert after["extra"] == {"k": "v"}


async def test_upsert_photos_identical_data_does_not_rewrite_row(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    record = make_record(source_id="rec-same", extra={"k": "v"}, received_at=ts(2, 3))
    await photos_repo.upsert_photos(db_session, "curiosity", [record])
    await backdate_timestamps(db_session, "rec-same")

    processed = await photos_repo.upsert_photos(db_session, "curiosity", [record])
    (row,) = await photo_rows(db_session, "curiosity")

    assert processed == 1
    assert row["created_at"] == OLD_TS
    assert row["updated_at"] == OLD_TS


async def test_upsert_photos_img_small_none_keeps_the_stored_thumbnail(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    thumb = "https://images.example.test/thumb.jpg"
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-thumb", img_small=thumb)]
    )

    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-thumb", img_small=None)]
    )

    (row,) = await photo_rows(db_session, "curiosity")
    assert row["img_small"] == thumb


async def test_upsert_photos_same_img_small_does_not_rewrite_row(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    thumb = "https://images.example.test/thumb.jpg"
    record = make_record(source_id="rec-thumb-same", img_small=thumb)
    await photos_repo.upsert_photos(db_session, "curiosity", [record])
    await backdate_timestamps(db_session, "rec-thumb-same")

    await photos_repo.upsert_photos(db_session, "curiosity", [record])
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-thumb-same", img_small=None)]
    )

    (row,) = await photo_rows(db_session, "curiosity")
    assert row["img_small"] == thumb
    assert row["updated_at"] == OLD_TS


async def test_upsert_photos_new_img_small_replaces_the_stored_thumbnail(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.upsert_photos(
        db_session,
        "curiosity",
        [make_record(source_id="rec-thumb-new", img_small="https://images.example.test/old.jpg")],
    )
    await backdate_timestamps(db_session, "rec-thumb-new")
    new = "https://images.example.test/new.jpg"

    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-thumb-new", img_small=new)]
    )

    (row,) = await photo_rows(db_session, "curiosity")
    assert row["img_small"] == new
    assert row["updated_at"] != OLD_TS


async def test_upsert_photos_field_cleared_to_none_is_updated(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-clear", caption="has caption")]
    )
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="rec-clear", caption=None)]
    )
    (row,) = await photo_rows(db_session, "curiosity")
    assert row["caption"] is None


async def test_upsert_photos_duplicate_source_id_in_batch_last_one_wins(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    records = [
        make_record(source_id="dup", caption="first"),
        make_record(source_id="other"),
        make_record(source_id="dup", caption="last"),
    ]

    processed = await photos_repo.upsert_photos(db_session, "curiosity", records)

    assert processed == 2
    rows = {r["source_id"]: r for r in await photo_rows(db_session, "curiosity")}
    assert set(rows) == {"dup", "other"}
    assert rows["dup"]["caption"] == "last"


async def test_upsert_photos_same_source_id_on_different_rovers_both_stored(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(source_id="shared", camera_code="FHAZ")]
    )
    await photos_repo.upsert_photos(
        db_session, "perseverance", [make_record(source_id="shared", camera_code="NAVCAM_LEFT")]
    )

    curiosity = await photo_rows(db_session, "curiosity")
    perseverance = await photo_rows(db_session, "perseverance")
    assert [r["source_id"] for r in curiosity] == ["shared"]
    assert [r["source_id"] for r in perseverance] == ["shared"]
    assert curiosity[0]["id"] != perseverance[0]["id"]
    assert perseverance[0]["camera_code"] == "NAVCAM_LEFT"


async def test_upsert_photos_unknown_camera_created_at_end_of_order(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    records = [
        make_record(camera_code="ZZ_TEST_B"),
        make_record(camera_code="zz_test_a"),
        make_record(camera_code="ZZ_TEST_B"),
        make_record(camera_code="FHAZ"),
    ]

    await photos_repo.upsert_photos(db_session, "curiosity", records)

    cameras = await camera_rows(db_session, "curiosity")
    assert [c["code"] for c in cameras] == [*CURIOSITY_CODES, "ZZ_TEST_B", "ZZ_TEST_A"]
    assert [c["sort_order"] for c in cameras] == list(range(1, len(CURIOSITY_CODES) + 3))
    new = {c["code"]: c["name"] for c in cameras[len(CURIOSITY_CODES) :]}
    assert new == {"ZZ_TEST_B": "ZZ_TEST_B", "ZZ_TEST_A": "ZZ_TEST_A"}
    rows = await photo_rows(db_session, "curiosity")
    assert sorted(r["camera_code"] for r in rows) == ["FHAZ", "ZZ_TEST_A", "ZZ_TEST_B", "ZZ_TEST_B"]


# ------------------------------------------------------------------- cameras repo


async def test_get_or_create_cameras_existing_codes_case_insensitive_no_new_rows(
    db_session: AsyncSession,
) -> None:
    before = await camera_rows(db_session, "curiosity")
    ids = {c["code"]: c["id"] for c in before}

    cameras = await rovers_repo.get_or_create_cameras(db_session, "curiosity", ["fhaz", "Navcam"])

    assert set(cameras) == {"FHAZ", "NAVCAM"}
    assert cameras["FHAZ"].id == ids["FHAZ"]
    assert cameras["NAVCAM"].id == ids["NAVCAM"]
    assert cameras["FHAZ"].code == "FHAZ"
    assert len(await camera_rows(db_session, "curiosity")) == len(before)


async def test_get_or_create_cameras_unknown_codes_appended_in_input_order(
    db_session: AsyncSession,
) -> None:
    cameras = await rovers_repo.get_or_create_cameras(
        db_session, "spirit", ["new_two", "FHAZ", "NEW_ONE", "New_Two"]
    )

    assert set(cameras) == {"NEW_TWO", "FHAZ", "NEW_ONE"}
    assert cameras["NEW_TWO"].sort_order == 7
    assert cameras["NEW_ONE"].sort_order == 8
    assert cameras["NEW_ONE"].name == "NEW_ONE"
    assert cameras["NEW_TWO"].rover_id == "spirit"
    assert [c["code"] for c in await camera_rows(db_session, "spirit")][-2:] == [
        "NEW_TWO",
        "NEW_ONE",
    ]

    again = await rovers_repo.get_or_create_cameras(db_session, "spirit", ["new_one"])
    assert again["NEW_ONE"].id == cameras["NEW_ONE"].id
    assert len(await camera_rows(db_session, "spirit")) == 8


# ------------------------------------------------------------------- rovers repo


async def test_list_rovers_ordered_by_legacy_id(db_session: AsyncSession) -> None:
    rovers = await rovers_repo.list_rovers(db_session)
    assert [(r.id, r.legacy_id) for r in rovers] == [
        ("curiosity", 5),
        ("opportunity", 6),
        ("spirit", 7),
        ("perseverance", 8),
        ("ingenuity", 9),  # stage 12: the repository sees it; v1 hides it in the service
    ]


@pytest.mark.parametrize("rover_id", ["curiosity", "CURIOSITY", "Curiosity"])
async def test_get_rover_any_case_returns_rover(db_session: AsyncSession, rover_id: str) -> None:
    rover = await rovers_repo.get_rover(db_session, rover_id)
    assert rover is not None
    assert (rover.id, rover.name, rover.landing_date) == (
        "curiosity",
        "Curiosity",
        date(2012, 8, 6),
    )


@pytest.mark.parametrize("rover_id", ["sojourner", "", "curiosity-2"])
async def test_get_rover_unknown_returns_none(db_session: AsyncSession, rover_id: str) -> None:
    assert await rovers_repo.get_rover(db_session, rover_id) is None


async def test_list_cameras_ordered_by_sort_order(db_session: AsyncSession) -> None:
    cameras = await rovers_repo.list_cameras(db_session, "curiosity")
    assert [c.code for c in cameras] == CURIOSITY_CODES
    assert [c.sort_order for c in cameras] == list(range(1, 8))


# --------------------------------------------------------------- queries dataset


@pytest.fixture
async def dataset(db_session: AsyncSession, make_record: MakeRecord) -> dict[str, Any]:
    """Curiosity photos over sols 10..13 plus one Perseverance photo on sol 10.

    Returns photo rows keyed by source_id (with camera code and sort order).
    """
    records = [
        make_record(source_id="s12-navcam", sol=12, camera_code="NAVCAM", taken_at=ts(3, 1),
                    earth_date=date(2020, 1, 3)),
        make_record(source_id="s10-fhaz-full", sol=10, camera_code="FHAZ", taken_at=ts(1, 10)),
        make_record(source_id="s10-navcam", sol=10, camera_code="NAVCAM", taken_at=ts(1, 9)),
        make_record(source_id="s10-fhaz-sub", sol=10, camera_code="fhaz", taken_at=ts(1, 11),
                    sample_type="subframe"),
        make_record(source_id="s11-mast", sol=11, camera_code="MAST", taken_at=ts(2, 8),
                    earth_date=date(2020, 1, 2)),
        make_record(source_id="s11-rhaz-down", sol=11, camera_code="RHAZ", taken_at=ts(2, 12),
                    earth_date=date(2020, 1, 2), sample_type="downsampled"),
        make_record(source_id="s12-fhaz", sol=12, camera_code="FHAZ", taken_at=ts(3, 1),
                    earth_date=date(2020, 1, 3)),
        make_record(source_id="s13-chemcam-sub", sol=13, camera_code="CHEMCAM", taken_at=ts(4, 1),
                    earth_date=date(2020, 1, 4), sample_type="subframe"),
    ]  # fmt: skip
    await photos_repo.upsert_photos(db_session, "curiosity", records)
    await photos_repo.upsert_photos(
        db_session,
        "perseverance",
        [make_record(source_id="m20-s10", sol=10, camera_code="NAVCAM_LEFT", taken_at=ts(1, 10))],
    )
    return {r["source_id"]: r for r in await photo_rows(db_session, "curiosity")}


FILTER_CASES = [
    pytest.param({}, {"s10-fhaz-full", "s10-navcam", "s10-fhaz-sub", "s11-mast",
                      "s11-rhaz-down", "s12-fhaz", "s12-navcam", "s13-chemcam-sub"},
                 id="rover-only"),
    pytest.param({"sol": 10}, {"s10-fhaz-full", "s10-navcam", "s10-fhaz-sub"}, id="sol"),
    pytest.param({"sol": 99}, set(), id="sol-without-photos"),
    pytest.param({"earth_date": date(2020, 1, 2)}, {"s11-mast", "s11-rhaz-down"}, id="earth-date"),
    pytest.param({"camera_codes": ["fhaz"]}, {"s10-fhaz-full", "s10-fhaz-sub", "s12-fhaz"},
                 id="camera-lower-case"),
    pytest.param({"camera_codes": ["FHAZ", "Navcam"]},
                 {"s10-fhaz-full", "s10-fhaz-sub", "s12-fhaz", "s10-navcam", "s12-navcam"},
                 id="several-cameras"),
    pytest.param({"camera_codes": ["FHA"]}, set(), id="camera-prefix-is-not-a-match"),
    pytest.param({"camera_codes": ["PANCAM"]}, set(), id="camera-of-other-rover"),
    pytest.param({"camera_codes": []}, set(), id="empty-camera-list"),
    pytest.param({"sample_type": "full"},
                 {"s10-fhaz-full", "s10-navcam", "s11-mast", "s12-fhaz", "s12-navcam"},
                 id="full-only"),
    pytest.param({"sol": 10, "camera_codes": ["FHAZ"], "sample_type": "full"}, {"s10-fhaz-full"},
                 id="combined"),
]  # fmt: skip


@pytest.mark.parametrize(("kwargs", "expected"), FILTER_CASES)
async def test_list_photos_filter_returns_matching_photos(
    db_session: AsyncSession, dataset: dict[str, Any], kwargs: dict[str, Any], expected: set[str]
) -> None:
    photos = await photos_repo.list_photos(db_session, PhotoFilter(rover_id="curiosity", **kwargs))
    assert {p.source_id for p in photos} == expected
    assert all(p.rover_id == "curiosity" for p in photos)


@pytest.mark.parametrize(("kwargs", "expected"), FILTER_CASES)
async def test_count_photos_filter_matches_list(
    db_session: AsyncSession, dataset: dict[str, Any], kwargs: dict[str, Any], expected: set[str]
) -> None:
    count = await photos_repo.count_photos(db_session, PhotoFilter(rover_id="curiosity", **kwargs))
    assert count == len(expected)


async def test_list_photos_legacy_order_by_camera_sort_order_then_id(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    expected = [
        r["id"] for r in sorted(dataset.values(), key=lambda r: (r["camera_sort_order"], r["id"]))
    ]

    photos = await photos_repo.list_photos(
        db_session, PhotoFilter(rover_id="curiosity"), order="legacy"
    )

    assert [p.id for p in photos] == expected


async def test_list_photos_newest_order_by_sol_taken_at_id_desc(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    expected = [
        r["id"]
        for r in sorted(
            dataset.values(), key=lambda r: (r["sol"], r["taken_at"], r["id"]), reverse=True
        )
    ]

    photos = await photos_repo.list_photos(
        db_session, PhotoFilter(rover_id="curiosity"), order="newest"
    )
    default = await photos_repo.list_photos(db_session, PhotoFilter(rover_id="curiosity"))

    assert [p.id for p in photos] == expected
    assert [p.id for p in default] == expected
    # s12-fhaz and s12-navcam share sol and taken_at: the tie is broken by id DESC.
    tie = [p.source_id for p in photos if p.sol == 12]
    assert tie == sorted(tie, key=lambda s: dataset[s]["id"], reverse=True)


@pytest.mark.parametrize("order", ["legacy", "newest"])
@pytest.mark.parametrize(
    ("limit", "offset"),
    [(2, 0), (3, 2), (None, 5), (100, 0), (5, 8), (1, 7)],
    ids=lambda v: str(v),
)
async def test_list_photos_limit_offset_returns_slice_of_full_order(
    db_session: AsyncSession, dataset: dict[str, Any], order: str, limit: int | None, offset: int
) -> None:
    flt = PhotoFilter(rover_id="curiosity")
    everything = [p.id for p in await photos_repo.list_photos(db_session, flt, order=order)]

    page = await photos_repo.list_photos(db_session, flt, order=order, limit=limit, offset=offset)

    end = None if limit is None else offset + limit
    assert [p.id for p in page] == everything[offset:end]


async def test_list_photos_camera_is_loaded_without_lazy_load(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    photos = await photos_repo.list_photos(db_session, PhotoFilter(rover_id="curiosity", sol=11))
    db_session.expunge_all()  # any lazy load would now fail
    assert {(p.source_id, p.camera.code) for p in photos} == {
        ("s11-mast", "MAST"),
        ("s11-rhaz-down", "RHAZ"),
    }


async def test_get_photo_existing_returns_photo_with_camera(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    row = dataset["s11-mast"]

    photo = await photos_repo.get_photo(db_session, row["id"])
    assert photo is not None
    db_session.expunge_all()

    assert (photo.id, photo.source_id, photo.sol) == (row["id"], "s11-mast", 11)
    assert photo.camera.code == "MAST"
    assert photo.camera.sort_order == 3


async def test_get_photo_missing_returns_none(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    missing_id = max(r["id"] for r in dataset.values()) + 1_000_000
    assert await photos_repo.get_photo(db_session, missing_id) is None


@pytest.mark.parametrize(("full_only", "expected"), [(False, 13), (True, 12)])
async def test_max_sol_with_photos(
    db_session: AsyncSession, dataset: dict[str, Any], full_only: bool, expected: int
) -> None:
    assert await photos_repo.max_sol(db_session, "curiosity", full_only=full_only) == expected


async def test_max_sol_default_counts_all_sample_types(
    db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    assert await photos_repo.max_sol(db_session, "curiosity") == 13


@pytest.mark.parametrize("full_only", [False, True])
async def test_max_sol_without_photos_returns_none(
    db_session: AsyncSession, dataset: dict[str, Any], full_only: bool
) -> None:
    assert await photos_repo.max_sol(db_session, "spirit", full_only=full_only) is None


async def test_max_sol_full_only_without_full_photos_returns_none(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.upsert_photos(
        db_session, "opportunity", [make_record(sol=3, sample_type="subframe")]
    )
    assert await photos_repo.max_sol(db_session, "opportunity") == 3
    assert await photos_repo.max_sol(db_session, "opportunity", full_only=True) is None
