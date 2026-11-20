"""Schema of migration 0010 (docs/proposals/stage-13-samples.md section 5).

`rover_samples` (PK rover_id + number, CHECKs on number / status / sample_type / sol) and
`sample_datasets` (one state row per rover). Every failing statement runs in its own test:
the session's transaction is aborted by a failure.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _all(session: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql), params)).mappings()]


async def _insert(session: AsyncSession, **values: Any) -> None:
    row = {"rover_id": "perseverance", "number": 1, "name": "Roubion", **values}
    columns = list(row)
    await session.execute(
        text(
            f"INSERT INTO rover_samples ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + c for c in columns)})"
        ),
        row,
    )


@pytest.mark.parametrize(
    ("table", "primary_key"),
    [
        ("rover_samples", ["rover_id", "number"]),
        ("sample_datasets", ["rover_id"]),
    ],
)
async def test_sample_table_primary_key_matches_contract(
    db_session: AsyncSession, table: str, primary_key: list[str]
) -> None:
    rows = await _all(
        db_session,
        "SELECT a.attname AS name FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey::int2[], a.attnum)",
        t=f"public.{table}",
    )
    assert [row["name"] for row in rows] == primary_key


@pytest.mark.parametrize(
    ("table", "column", "data_type", "nullable"),
    [
        ("rover_samples", "rover_id", "text", "NO"),
        ("rover_samples", "number", "smallint", "NO"),
        ("rover_samples", "name", "text", "NO"),
        ("rover_samples", "feature_name", "text", "YES"),
        ("rover_samples", "sampling_location", "text", "YES"),
        ("rover_samples", "current_location", "text", "YES"),
        ("rover_samples", "status", "text", "YES"),
        ("rover_samples", "sample_type", "text", "YES"),
        ("rover_samples", "sample_type_label", "text", "YES"),
        ("rover_samples", "rock_type", "text", "YES"),
        ("rover_samples", "sealed_on", "date", "YES"),
        ("rover_samples", "sol", "integer", "YES"),
        ("rover_samples", "height_cm", "numeric", "YES"),
        ("rover_samples", "height_note", "text", "YES"),
        ("rover_samples", "dossier_url", "text", "YES"),
        ("sample_datasets", "rover_id", "text", "NO"),
        ("sample_datasets", "created_at", "timestamp with time zone", "NO"),
        ("sample_datasets", "checked_at", "timestamp with time zone", "YES"),
        ("sample_datasets", "succeeded_at", "timestamp with time zone", "YES"),
        ("sample_datasets", "changed_at", "timestamp with time zone", "YES"),
        ("sample_datasets", "source_modified_at", "timestamp with time zone", "YES"),
        ("sample_datasets", "etag", "text", "YES"),
        ("sample_datasets", "last_modified", "text", "YES"),
        ("sample_datasets", "content_sha256", "text", "YES"),
        ("sample_datasets", "items", "integer", "NO"),
        ("sample_datasets", "last_error", "text", "YES"),
    ],
    ids=lambda v: str(v),
)
async def test_sample_column_type_and_nullability(
    db_session: AsyncSession, table: str, column: str, data_type: str, nullable: str
) -> None:
    [row] = await _all(
        db_session,
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c",
        t=table,
        c=column,
    )
    assert (row["data_type"], row["is_nullable"]) == (data_type, nullable)


async def test_height_is_numeric_5_2(db_session: AsyncSession) -> None:
    [row] = await _all(
        db_session,
        "SELECT numeric_precision AS p, numeric_scale AS s FROM information_schema.columns "
        "WHERE table_name = 'rover_samples' AND column_name = 'height_cm'",
    )
    assert (row["p"], row["s"]) == (5, 2)


@pytest.mark.parametrize("table", ["rover_samples", "sample_datasets"])
async def test_sample_table_rover_id_is_a_foreign_key(db_session: AsyncSession, table: str) -> None:
    rows = await _all(
        db_session,
        "SELECT 1 FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'f' "
        "AND confrelid = 'public.rovers'::regclass",
        t=f"public.{table}",
    )
    assert rows, f"{table}.rover_id must reference rovers"


async def test_sample_datasets_items_default_to_zero(db_session: AsyncSession) -> None:
    await db_session.execute(text("INSERT INTO sample_datasets (rover_id) VALUES ('perseverance')"))

    [row] = await _all(db_session, "SELECT items, created_at FROM sample_datasets")

    assert row["items"] == 0
    assert row["created_at"] is not None


async def test_sample_row_round_trips_values_and_height_keeps_two_decimals(
    db_session: AsyncSession,
) -> None:
    await _insert(
        db_session,
        number=25,
        status="on_rover",
        sample_type="rock_core",
        sealed_on=date(2024, 7, 21),
        sol=1215,
        height_cm=6.2,
    )

    [row] = await _all(db_session, "SELECT * FROM rover_samples")

    assert (row["number"], row["sol"], row["sealed_on"]) == (25, 1215, date(2024, 7, 21))
    assert row["height_cm"] == Decimal("6.20")


async def test_sample_number_32767_is_accepted(db_session: AsyncSession) -> None:
    await _insert(db_session, number=32767)

    [row] = await _all(db_session, "SELECT number FROM rover_samples")

    assert row["number"] == 32767


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"number": 0}, id="number-zero"),
        pytest.param({"number": -1}, id="number-negative"),
        pytest.param({"number": 32768}, id="number-over-smallint"),
        pytest.param({"status": "somewhere"}, id="status-unknown"),
        pytest.param({"sample_type": "dust"}, id="sample-type-unknown"),
        pytest.param({"sol": -1}, id="sol-negative"),
        pytest.param({"name": None}, id="name-null"),
        pytest.param({"rover_id": "nobody"}, id="rover-unknown"),
    ],
)
async def test_sample_row_constraints_reject_bad_values(
    db_session: AsyncSession, values: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await _insert(db_session, **values)


async def test_sample_primary_key_rejects_a_duplicate_number(db_session: AsyncSession) -> None:
    await _insert(db_session, number=3)

    with pytest.raises(DBAPIError):
        await _insert(db_session, number=3, name="Other")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "on_rover"),
        ("status", "depot"),
        ("status", None),
        ("sample_type", "rock_core"),
        ("sample_type", "regolith"),
        ("sample_type", "atmospheric"),
        ("sample_type", None),
        ("sol", 0),
    ],
    ids=lambda v: str(v),
)
async def test_sample_row_constraints_accept_the_contract_values(
    db_session: AsyncSession, column: str, value: Any
) -> None:
    await _insert(db_session, **{column: value})

    [row] = await _all(db_session, "SELECT * FROM rover_samples")

    assert row[column] == value
