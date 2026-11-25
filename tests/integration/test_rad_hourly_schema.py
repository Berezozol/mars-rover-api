"""Schema of migration 0021: keys and CHECKs of `rad_hourly` and the new `rad_daily` columns."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_rad_schema import insert_daily

pytestmark = pytest.mark.integration


async def insert_hour(session: AsyncSession, **values: Any) -> None:
    row = {
        "sol": 4843,
        "hour": 0,
        "n_obs": 2,
        "live_s": 1860.0,
        "bm": 6.8,
        "em": 6.5,
        "bu": 12941,
        **values,
    }
    await session.execute(
        text(
            "INSERT INTO rad_hourly (sol, hour, n_obs, live_s, dose_b_mean, dose_e_mean, "
            "bu_count) VALUES (:sol, :hour, :n_obs, :live_s, :bm, :em, :bu)"
        ),
        row,
    )


async def test_rad_daily_hourly_columns_have_defaults(db_session: AsyncSession) -> None:
    await insert_daily(db_session)
    await insert_hour(db_session)

    daily = await db_session.execute(
        text("SELECT hourly_revision, hourly_reject, hourly_dropped FROM rad_daily")
    )
    assert dict(daily.mappings().one()) == {
        "hourly_revision": 0,
        "hourly_reject": None,
        "hourly_dropped": 0,
    }


@pytest.mark.parametrize("hour", [0, 23])
async def test_rad_hourly_accepts_the_hour_limits(db_session: AsyncSession, hour: int) -> None:
    await insert_daily(db_session)
    await insert_hour(db_session, hour=hour)


@pytest.mark.parametrize(
    "values",
    [
        {"hour": -1},
        {"hour": 24},
        {"n_obs": 0},
        {"live_s": 0.0},
        {"live_s": -1.0},
        {"sol": 9999},
        {"bm": None},
        {"em": None},
        {"bu": None},
    ],
    ids=[
        "hour-low",
        "hour-high",
        "no-obs",
        "live-zero",
        "live-negative",
        "no-sol",
        "no-b",
        "no-e",
        "no-bu",
    ],
)
async def test_rad_hourly_rejects_bad_rows(
    db_session: AsyncSession, values: dict[str, Any]
) -> None:
    await insert_daily(db_session)
    with pytest.raises(DBAPIError):
        await insert_hour(db_session, **values)


async def test_rad_hourly_primary_key_is_sol_and_hour(db_session: AsyncSession) -> None:
    await insert_daily(db_session)
    await insert_hour(db_session)
    with pytest.raises(IntegrityError):
        await insert_hour(db_session)


async def test_rad_hourly_rows_follow_their_sol_on_delete(db_session: AsyncSession) -> None:
    await insert_daily(db_session)
    await insert_hour(db_session)
    await insert_hour(db_session, hour=5)

    await db_session.execute(text("DELETE FROM rad_daily WHERE sol = 4843"))

    assert await db_session.scalar(text("SELECT count(*) FROM rad_hourly")) == 0


@pytest.mark.parametrize("reject", ["overlap", "off_sol", None])
async def test_rad_daily_accepts_the_known_reject_reasons(
    db_session: AsyncSession, reject: str | None
) -> None:
    await insert_daily(db_session)
    await db_session.execute(
        text("UPDATE rad_daily SET hourly_reject = :r, hourly_revision = 1"), {"r": reject}
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE rad_daily SET hourly_reject = 'weird'",
        "UPDATE rad_daily SET hourly_dropped = -1",
    ],
    ids=["reject-reason", "dropped-negative"],
)
async def test_rad_daily_new_columns_reject_bad_values(db_session: AsyncSession, sql: str) -> None:
    await insert_daily(db_session)
    with pytest.raises(DBAPIError):
        await db_session.execute(text(sql))


def test_orm_models_know_the_hourly_columns() -> None:
    from app.db.models import RadDaily, RadHourly

    assert {c.name for c in RadHourly.__table__.columns} == {
        "sol",
        "hour",
        "n_obs",
        "live_s",
        "dose_b_mean",
        "dose_e_mean",
        "bu_count",
    }
    for name in ("hourly_revision", "hourly_reject", "hourly_dropped"):
        assert hasattr(RadDaily, name)
