"""Schema of migration 0017 (docs/proposals/stage-21-rad.md section 6): keys and CHECKs.

Every failing statement runs in its own test (a failure aborts the session's transaction).
"""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PATH = "DATA/SOL_04710_04843/RAD_RDR_2026_080_17_18_4843_V00.TXT"
MD5 = "53e137ba4ac493af2b26d585b5caf5d1"


async def insert_file(session: AsyncSession, **values: Any) -> None:
    row = {"path": PATH, "sol": 4843, "md5": MD5, "status": "loaded", "role": "primary", **values}
    columns = list(row)
    await session.execute(
        text(
            f"INSERT INTO rad_files ({', '.join(columns)}, processed_at) "
            f"VALUES ({', '.join(':' + c for c in columns)}, now())"
        ),
        row,
    )


async def insert_daily(session: AsyncSession, **values: Any) -> None:
    row = {
        "sol": 4843,
        "ed": date(2026, 3, 21),
        "bm": 6.8,
        "bn": 6.7,
        "bx": 6.9,
        "em": 6.5,
        "en": 6.4,
        "ex": 6.6,
        "bu": 19450,
        "live": 2790.0,
        "used": 3,
        "sp": "DATA/x.TXT",
        **values,
    }
    await session.execute(
        text(
            "INSERT INTO rad_daily (sol, earth_date, dose_b_mean, dose_b_min, dose_b_max, "
            "dose_e_mean, dose_e_min, dose_e_max, bu_count, live_s, n_used, source_path, "
            "updated_at) VALUES (:sol, :ed, :bm, :bn, :bx, :em, :en, :ex, :bu, "
            ":live, :used, :sp, now())"
        ),
        row,
    )


async def test_rad_files_accepts_a_loaded_primary_file_with_defaults(
    db_session: AsyncSession,
) -> None:
    await insert_file(db_session)

    result = await db_session.execute(
        text("SELECT status, blocks, dose_obs, timing_listed FROM rad_files")
    )
    assert dict(result.mappings().one()) == {
        "status": "loaded",
        "blocks": 0,
        "dose_obs": 0,
        "timing_listed": False,
    }


@pytest.mark.parametrize(
    "values",
    [
        {"status": "weird"},
        {"role": "boss"},
        {"dup_check": "magic"},
        {"md5": None},  # md5 is NULL only for failed / md5_mismatch
        {"status": "failed", "md5": MD5},
        {"status": "md5_mismatch", "md5": MD5},
    ],
    ids=["status", "role", "dup-check", "loaded-needs-md5", "failed-no-md5", "mismatch-no-md5"],
)
async def test_rad_files_check_constraints_reject_bad_rows(
    db_session: AsyncSession, values: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await insert_file(db_session, **values)


@pytest.mark.parametrize("status", ["failed", "md5_mismatch"])
async def test_rad_files_failed_rows_may_have_no_md5(db_session: AsyncSession, status: str) -> None:
    await insert_file(db_session, status=status, md5=None, role=None, error="boom")


async def test_rad_files_path_is_unique(db_session: AsyncSession) -> None:
    await insert_file(db_session)
    with pytest.raises(IntegrityError):
        await insert_file(db_session)


async def test_rad_daily_accepts_a_valid_row_with_defaults(db_session: AsyncSession) -> None:
    await insert_daily(db_session)

    result = await db_session.execute(
        text(
            "SELECT n_dropped_qc, n_dropped_low_rate, n_dropped_invalid, qc_bits, "
            "timing_caveat FROM rad_daily"
        )
    )
    assert dict(result.mappings().one()) == {
        "n_dropped_qc": 0,
        "n_dropped_low_rate": 0,
        "n_dropped_invalid": 0,
        "qc_bits": 0,
        "timing_caveat": None,
    }


@pytest.mark.parametrize(
    "values",
    [{"live": 0.0}, {"live": -1.0}, {"used": 0}],
    ids=["live-zero", "live-negative", "used-zero"],
)
async def test_rad_daily_check_constraints_reject_bad_rows(
    db_session: AsyncSession, values: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await insert_daily(db_session, **values)


async def test_rad_daily_sol_is_the_primary_key(db_session: AsyncSession) -> None:
    await insert_daily(db_session)
    with pytest.raises(IntegrityError):
        await insert_daily(db_session)


async def test_rad_dataset_holds_one_row_only(db_session: AsyncSession) -> None:
    await db_session.execute(text("INSERT INTO rad_dataset (id) VALUES (1)"))
    with pytest.raises(DBAPIError):
        await db_session.execute(text("INSERT INTO rad_dataset (id) VALUES (2)"))
