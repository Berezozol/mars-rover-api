"""recompute_sol_summaries / list_sol_summaries (app/repositories/photos.py)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import photos as photos_repo

pytestmark = pytest.mark.integration

MakeRecord = Callable[..., Any]


async def summary_rows(session: AsyncSession, rover_id: str) -> dict[int, Any]:
    """Raw rows (bypasses the ORM identity map, so values are always fresh)."""
    result = await session.execute(
        text("SELECT * FROM sol_summaries WHERE rover_id = :rover ORDER BY sol"),
        {"rover": rover_id},
    )
    return {row["sol"]: row for row in result.mappings()}


@pytest.fixture
async def sol20(db_session: AsyncSession, make_record: MakeRecord) -> None:
    """Curiosity sol 20: 3 full (FHAZ x2, NAVCAM) + RHAZ subframe; sol 21: one MAST subframe."""
    records = [
        make_record(source_id="a", sol=20, camera_code="NAVCAM", earth_date=date(2020, 5, 2),
                    taken_at=datetime(2020, 5, 2, 1, tzinfo=UTC)),
        make_record(source_id="b", sol=20, camera_code="FHAZ", earth_date=date(2020, 5, 1),
                    taken_at=datetime(2020, 5, 1, 23, tzinfo=UTC)),
        make_record(source_id="c", sol=20, camera_code="FHAZ", earth_date=date(2020, 5, 2),
                    taken_at=datetime(2020, 5, 2, 2, tzinfo=UTC)),
        make_record(source_id="d", sol=20, camera_code="RHAZ", earth_date=date(2020, 5, 2),
                    taken_at=datetime(2020, 5, 2, 3, tzinfo=UTC), sample_type="subframe"),
        make_record(source_id="e", sol=21, camera_code="MAST", earth_date=date(2020, 5, 3),
                    sample_type="subframe"),
    ]  # fmt: skip
    await photos_repo.upsert_photos(db_session, "curiosity", records)


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_counts_and_cameras(db_session: AsyncSession) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20])

    rows = await summary_rows(db_session, "curiosity")
    assert set(rows) == {20}
    row = rows[20]
    assert row["total_photos"] == 4
    assert row["full_photos"] == 3
    # Alphabetical, unique; seed sort order would be FHAZ, RHAZ, NAVCAM.
    assert row["cameras"] == ["FHAZ", "NAVCAM", "RHAZ"]
    assert row["full_cameras"] == ["FHAZ", "NAVCAM"]
    assert row["earth_date"] == date(2020, 5, 1)


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_sol_without_full_photos(db_session: AsyncSession) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21])

    row = (await summary_rows(db_session, "curiosity"))[21]
    assert (row["total_photos"], row["full_photos"]) == (1, 0)
    assert row["cameras"] == ["MAST"]
    assert row["full_cameras"] == []
    assert row["earth_date"] == date(2020, 5, 3)


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_only_given_sols_touched(db_session: AsyncSession) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21])
    assert set(await summary_rows(db_session, "curiosity")) == {21}

    # A stale row for sol 20 is not refreshed when only sol 21 is recomputed.
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20])
    await db_session.execute(
        text(
            "UPDATE sol_summaries SET total_photos = 999 WHERE rover_id = 'curiosity' AND sol = 20"
        )
    )
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21])
    assert (await summary_rows(db_session, "curiosity"))[20]["total_photos"] == 999


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_is_idempotent(db_session: AsyncSession) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20, 21])
    first = await summary_rows(db_session, "curiosity")
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21, 20, 20])
    second = await summary_rows(db_session, "curiosity")
    assert first == second


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_after_photo_added_updates_row(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20])
    await photos_repo.upsert_photos(
        db_session,
        "curiosity",
        [make_record(sol=20, camera_code="CHEMCAM", earth_date=date(2020, 4, 30))],
    )
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20])

    row = (await summary_rows(db_session, "curiosity"))[20]
    assert (row["total_photos"], row["full_photos"]) == (5, 4)
    assert row["cameras"] == ["CHEMCAM", "FHAZ", "NAVCAM", "RHAZ"]
    assert row["full_cameras"] == ["CHEMCAM", "FHAZ", "NAVCAM"]
    assert row["earth_date"] == date(2020, 4, 30)


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_after_photos_deleted_removes_row(
    db_session: AsyncSession,
) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20, 21])
    await db_session.execute(text("DELETE FROM photos WHERE rover_id = 'curiosity' AND sol = 21"))

    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21])

    assert set(await summary_rows(db_session, "curiosity")) == {20}


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_after_photo_moved_updates_both_sols(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20, 21])
    # "e" (the only photo of sol 21) moves to sol 22 and becomes full.
    await photos_repo.upsert_photos(
        db_session,
        "curiosity",
        [make_record(source_id="e", sol=22, camera_code="MAST", earth_date=date(2020, 5, 4))],
    )

    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [21, 22])

    rows = await summary_rows(db_session, "curiosity")
    assert set(rows) == {20, 22}
    assert (rows[22]["total_photos"], rows[22]["full_photos"]) == (1, 1)
    assert rows[22]["full_cameras"] == ["MAST"]
    assert rows[20]["total_photos"] == 4


async def test_recompute_sol_summaries_sol_without_photos_creates_no_row(
    db_session: AsyncSession,
) -> None:
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [500])
    assert await summary_rows(db_session, "curiosity") == {}


@pytest.mark.usefixtures("sol20")
async def test_recompute_sol_summaries_other_rover_not_mixed_in(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await photos_repo.upsert_photos(
        db_session, "perseverance", [make_record(sol=20, camera_code="NAVCAM_LEFT")]
    )
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", [20])
    await photos_repo.recompute_sol_summaries(db_session, "perseverance", [20])

    assert (await summary_rows(db_session, "curiosity"))[20]["total_photos"] == 4
    m20 = (await summary_rows(db_session, "perseverance"))[20]
    assert (m20["total_photos"], m20["cameras"]) == (1, ["NAVCAM_LEFT"])


# ---------------------------------------------------------------- list_sol_summaries


@pytest.fixture
async def four_sols(db_session: AsyncSession, make_record: MakeRecord) -> None:
    sols = [33, 30, 32, 31]
    await photos_repo.upsert_photos(
        db_session, "curiosity", [make_record(sol=s, camera_code="FHAZ") for s in sols]
    )
    await photos_repo.upsert_photos(
        db_session, "spirit", [make_record(sol=31, camera_code="PANCAM")]
    )
    await photos_repo.recompute_sol_summaries(db_session, "curiosity", sols)
    await photos_repo.recompute_sol_summaries(db_session, "spirit", [31])


@pytest.mark.usefixtures("four_sols")
@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ({}, [30, 31, 32, 33]),
        ({"from_sol": 31, "to_sol": 32}, [31, 32]),
        ({"from_sol": 31}, [31, 32, 33]),
        ({"to_sol": 31}, [30, 31]),
        ({"from_sol": 32, "to_sol": 32}, [32]),
        ({"from_sol": 34}, []),
        ({"from_sol": 33, "to_sol": 30}, []),
    ],
    ids=["all", "both-bounds", "from", "to", "single", "above-max", "inverted"],
)
async def test_list_sol_summaries_ordered_with_inclusive_bounds(
    db_session: AsyncSession, bounds: dict[str, int], expected: list[int]
) -> None:
    summaries = await photos_repo.list_sol_summaries(db_session, "curiosity", **bounds)
    assert [s.sol for s in summaries] == expected
    assert all(s.rover_id == "curiosity" for s in summaries)


@pytest.mark.usefixtures("four_sols")
async def test_list_sol_summaries_returns_fields(db_session: AsyncSession) -> None:
    (summary,) = await photos_repo.list_sol_summaries(db_session, "spirit")
    assert summary.sol == 31
    assert summary.total_photos == 1
    assert summary.full_photos == 1
    assert summary.cameras == ["PANCAM"]
    assert summary.full_cameras == ["PANCAM"]
    assert summary.earth_date == date(2020, 1, 1)


async def test_list_sol_summaries_no_rows_returns_empty_list(db_session: AsyncSession) -> None:
    assert await photos_repo.list_sol_summaries(db_session, "opportunity") == []
