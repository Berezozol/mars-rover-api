"""Stage 12 seed and schema (migrations 0008 and 0009) at head.

Contract: docs/proposals/stage-12-ingenuity.md sections 2 and 5. The expected flight log is
tests/_ingenuity.py, generated from data/ingenuity/flights.csv (flights 1 and 72 are also
spelled out below, so the check does not depend on the generator).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests._ingenuity import FLIGHTS

pytestmark = pytest.mark.integration

COLUMNS = (
    "flight, sol, date, distance_m, max_altitude_m, max_groundspeed_mps, duration_s, "
    "route_from, route_to"
)


async def _rows(session: AsyncSession) -> list[tuple]:
    result = await session.execute(text(f"SELECT {COLUMNS} FROM ingenuity_flights ORDER BY flight"))
    return [tuple(row) for row in result]


# --- 0008: the rover and its cameras -----------------------------------------------------------


async def test_seed_ingenuity_rover_row_matches_contract(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT id, legacy_id, name, mission, launch_date, landing_date, end_date, "
                "status, max_sol, max_date, total_photos, full_total_photos, last_synced_at "
                "FROM rovers WHERE id = 'ingenuity'"
            )
        )
    ).one()

    assert tuple(row) == (
        "ingenuity",
        9,
        "Ingenuity",
        "mars2020",
        date(2020, 7, 30),
        date(2021, 2, 18),
        date(2024, 1, 25),
        "complete",
        None,
        None,
        0,
        0,
        None,
    )


async def test_seed_ingenuity_cameras_in_order_with_names(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT code, name, sort_order FROM cameras WHERE rover_id = 'ingenuity' "
                "ORDER BY sort_order"
            )
        )
    ).all()

    assert [tuple(r) for r in rows] == [
        ("HELI_NAV", "Navigation Camera", 1),
        ("HELI_RTE", "Color Camera", 2),
    ]


async def test_seed_legacy_ids_stay_unique_and_old_ones_unchanged(db_session: AsyncSession) -> None:
    rows = (await db_session.execute(text("SELECT id, legacy_id FROM rovers"))).all()

    assert dict(rows) == {
        "curiosity": 5,
        "opportunity": 6,
        "spirit": 7,
        "perseverance": 8,
        "ingenuity": 9,
    }


# --- 0008: the flight log ------------------------------------------------------------------------


async def test_seed_flight_log_has_72_flights_numbered_1_to_72(db_session: AsyncSession) -> None:
    flights = await db_session.execute(text("SELECT flight FROM ingenuity_flights ORDER BY 1"))

    assert flights.scalars().all() == list(range(1, 73))


async def test_seed_flight_1_matches_the_log(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(text(f"SELECT {COLUMNS} FROM ingenuity_flights WHERE flight = 1"))
    ).one()

    assert tuple(row) == (
        1,
        58,
        date(2021, 4, 19),
        0,
        3,
        Decimal("0"),
        Decimal("39.1"),
        "Wright Brothers Field",
        None,
    )


async def test_seed_flight_72_matches_the_log(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(text(f"SELECT {COLUMNS} FROM ingenuity_flights WHERE flight = 72"))
    ).one()

    assert tuple(row) == (
        72,
        1035,
        date(2024, 1, 18),
        0,
        12,
        Decimal("0"),
        Decimal("32.0"),
        "Airfield Chi",
        None,
    )


async def test_seed_flight_9_has_both_airfields_and_decimal_values(
    db_session: AsyncSession,
) -> None:
    row = (
        await db_session.execute(text(f"SELECT {COLUMNS} FROM ingenuity_flights WHERE flight = 9"))
    ).one()

    assert tuple(row) == (
        9, 133, date(2021, 7, 5), 625, 10, Decimal("5"), Decimal("166.4"),
        "Airfield E", "Airfield F",
    )  # fmt: skip


async def test_seed_flight_log_equals_the_nasa_log_row_by_row(db_session: AsyncSession) -> None:
    expected = [
        (n, sol, date.fromisoformat(day), dist, alt, speed, dur, origin, dest)
        for n, sol, day, dist, alt, speed, dur, origin, dest in FLIGHTS
    ]

    assert await _rows(db_session) == expected


async def test_seed_flight_log_has_27_flights_without_destination(
    db_session: AsyncSession,
) -> None:
    count = await db_session.scalar(
        text("SELECT count(*) FROM ingenuity_flights WHERE route_to IS NULL")
    )

    assert count == 27


async def test_seed_flight_log_sols_are_unique_and_increasing_with_flight(
    db_session: AsyncSession,
) -> None:
    sols = (
        (await db_session.execute(text("SELECT sol FROM ingenuity_flights ORDER BY flight")))
        .scalars()
        .all()
    )

    assert sols == sorted(set(sols))
    assert (sols[0], sols[-1]) == (58, 1035)


# --- 0008: constraints --------------------------------------------------------------------------


def _insert(flight: int, sol: int) -> object:
    return text(
        "INSERT INTO ingenuity_flights (flight, sol, date, distance_m, max_altitude_m, "
        "max_groundspeed_mps, duration_s, route_from) "
        f"VALUES ({flight}, {sol}, DATE '2024-02-01', 1, 1, 1.5, 10.5, 'A')"
    )


async def test_flight_sol_is_unique(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await db_session.execute(_insert(500, 58))  # sol 58 is flight 1


async def test_flight_number_is_the_primary_key(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await db_session.execute(_insert(1, 5000))


@pytest.mark.parametrize("flight", [0, -1], ids=["zero", "negative"])
async def test_flight_number_must_be_positive(db_session: AsyncSession, flight: int) -> None:
    with pytest.raises(IntegrityError):
        await db_session.execute(_insert(flight, 6000))


async def test_flight_number_is_smallint_and_numeric_columns_have_contract_types(
    db_session: AsyncSession,
) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'ingenuity_flights'"
            )
        )
    ).all()
    info = {r.column_name: r for r in rows}

    assert info["flight"].data_type == "smallint"
    assert info["sol"].data_type == "integer"
    assert info["date"].data_type == "date"
    assert (
        info["max_groundspeed_mps"].numeric_precision,
        info["max_groundspeed_mps"].numeric_scale,
    ) == (5, 2)
    assert (info["duration_s"].numeric_precision, info["duration_s"].numeric_scale) == (6, 1)
    assert {name for name, r in info.items() if r.is_nullable == "YES"} == {"route_to"}


# --- 0009 / 0014: the (rover_id, site, drive) index ----------------------------------------------


async def test_photos_site_drive_index_is_covering_valid_and_partial(
    db_session: AsyncSession,
) -> None:
    """0014 replaced `ix_photos_rover_id_site_drive` with a covering index on the same key."""
    rows = (
        await db_session.execute(
            text(
                "SELECT i.indisvalid, i.indisready, pg_get_indexdef(i.indexrelid) AS indexdef "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'ix_photos_near_cover'"
            )
        )
    ).all()

    assert len(rows) == 1
    assert rows[0].indisvalid and rows[0].indisready
    definition = rows[0].indexdef
    assert "(rover_id, site, drive) INCLUDE (sol, taken_at, id)" in definition
    assert "WHERE (site IS NOT NULL)" in definition
    assert "ON public.photos" in definition


async def test_photos_old_site_drive_index_is_gone(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text("SELECT 1 FROM pg_class WHERE relname = 'ix_photos_rover_id_site_drive'")
        )
    ).all()
    assert rows == []


async def test_rover_unique_legacy_id_blocks_a_duplicate(db_session: AsyncSession) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(
            text(
                "INSERT INTO rovers (id, legacy_id, name, mission, launch_date, landing_date, "
                "status) VALUES ('dup', 9, 'Dup', 'x', DATE '2020-01-01', DATE '2020-01-02', 'x')"
            )
        )
