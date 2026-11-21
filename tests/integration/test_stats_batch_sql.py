"""Stage 18C at the database level: AC-6, AC-9 and AC-15 (docs/proposals/stage-18c-stats-batch.md).

- AC-6: stats dates go by the sol date stored in `sol_summaries.earth_date`.
- AC-9: stats never reads `photos` and never takes a heavy-search place: with the heavy gate
  full it still answers 200, and the SQL log has no statement on `photos`.
- AC-15: batch of 100 ids is one statement on `photos` (no per-id queries), also with the gate full.

The SQL spy listens to `before_cursor_execute` of the application engine.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, text

from app.repositories.photos import upsert_photos
from tests.api.v2._stats import periods_records, photo_ids, seed_records
from tests.api.v2.conftest import V2, Factory
from tests.conftest import build_record

pytestmark = pytest.mark.integration

PHOTOS_TABLE = re.compile(
    r"\bphotos\b", re.IGNORECASE
)  # `total_photos` / `full_photos` do not match
SUMMARY_TABLES = re.compile(r"\b(sol_summaries|camera_sol_counts)\b", re.IGNORECASE)


@contextmanager
def sql_log(app: FastAPI) -> Iterator[list[str]]:
    """Collect every SQL statement the application engine executes inside the block."""
    statements: list[str] = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)


def fill_heavy_gate(app: FastAPI) -> None:
    gate = app.state.heavy_query_gate
    gate.in_use = gate.limit  # no place left for a heavy search


@pytest.fixture
async def seeded(clean_db: Factory) -> Factory:
    await seed_records(clean_db, periods_records())
    return clean_db


# --- AC-6 --------------------------------------------------------------------------------------


async def test_ac6_stats_dates_use_the_sol_date_of_the_summary_not_the_photo_date(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    async with seeded() as session:
        sol_date = await session.scalar(
            text(
                "SELECT earth_date FROM sol_summaries "
                "WHERE rover_id = 'perseverance' AND sol = 1000"
            )
        )
        late_photo = await session.scalar(
            text(
                "SELECT max(earth_date) FROM photos WHERE rover_id = 'perseverance' AND sol = 1000"
            )
        )
    # precondition: the sol began on 2023-12-31 and captured 2024-01-01
    assert sol_date == date(2023, 12, 31)
    assert late_photo == date(2024, 1, 1)

    january = {"rovers": "perseverance", "date_min": "2024-01-01", "date_max": "2024-01-31"}
    body = (await client.get(f"{V2}/photos/stats", params=january)).json()
    december = {"rovers": "perseverance", "date_min": "2023-12-01", "date_max": "2023-12-31"}
    body_december = (await client.get(f"{V2}/photos/stats", params=december)).json()

    assert (body["total_photos"], body["sols_with_photos"]) == (6, 2)
    assert [g["first_sol"] for g in body["groups"]] == [1001]
    # all three photos of sol 1000 are counted in December, including the one taken on 01-01
    assert (body_december["total_photos"], body_december["sols_with_photos"]) == (3, 1)
    assert body_december["groups"][0]["first_sol"] == 1000


# --- AC-9 --------------------------------------------------------------------------------------

STATS_QUERIES = [
    pytest.param({"group_by": "month"}, id="month"),
    pytest.param({"group_by": "year"}, id="year"),
    pytest.param({"group_by": "camera"}, id="camera"),
    pytest.param({}, id="rover"),
    pytest.param({"group_by": "month", "sample_type": "full"}, id="month-full"),
    pytest.param({"group_by": "month", "camera": "NAVCAM_LEFT,FHAZ"}, id="month-cameras"),
    pytest.param({"group_by": "camera", "sol_min": "1001", "sol_max": "4000"}, id="camera-sols"),
    pytest.param(
        {"group_by": "month", "date_min": "2024-01-01", "date_max": "2024-12-31"},
        id="month-dates",
    ),
    pytest.param({"sol_min": "999999"}, id="empty"),
]


@pytest.mark.parametrize("params", STATS_QUERIES)
async def test_ac9_stats_answers_200_with_a_full_heavy_gate_and_never_reads_photos(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory, params: dict[str, str]
) -> None:
    fill_heavy_gate(app)

    with sql_log(app) as statements:
        response = await client.get(f"{V2}/photos/stats", params=params)

    assert response.status_code == 200, response.text
    assert statements, "no SQL was captured: the spy does not see the application engine"
    assert [s for s in statements if PHOTOS_TABLE.search(s)] == []
    assert any(SUMMARY_TABLES.search(s) for s in statements)


async def test_ac9_the_gate_in_the_test_really_is_full_for_a_heavy_search(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    """Guards the setup of the AC-9 tests: a heavy list is refused while stats is served."""
    fill_heavy_gate(app)

    heavy = await client.get(f"{V2}/photos", params={"date_min": "2024-01-01"})
    stats = await client.get(f"{V2}/photos/stats", params={"group_by": "month"})

    assert heavy.status_code == 503
    assert heavy.json()["error"]["code"] == "service_busy"
    assert stats.status_code == 200


async def test_ac9_stats_does_not_hold_a_gate_place(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    gate = app.state.heavy_query_gate

    response = await client.get(f"{V2}/photos/stats", params={"group_by": "month"})

    assert response.status_code == 200
    assert gate.in_use == 0
    assert gate.by_client == {}


async def test_ac9_stats_numbers_do_not_change_when_photos_change_without_a_resync(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    """Stats come from the summaries: a photo row added behind the sync's back is not counted."""
    before = (await client.get(f"{V2}/photos/stats", params={"rovers": "curiosity"})).json()
    async with seeded() as session:
        # photos only: no recompute_sol_summaries, as after a crashed sync
        await upsert_photos(
            session, "curiosity", [build_record(sol=4000, camera_code="FHAZ", sample_type="full")]
        )
        await session.commit()

    after = (await client.get(f"{V2}/photos/stats", params={"rovers": "curiosity"})).json()

    assert after["total_photos"] == before["total_photos"] == 3


# --- AC-15 -------------------------------------------------------------------------------------


def photos_statements(statements: list[str]) -> list[str]:
    return [s for s in statements if PHOTOS_TABLE.search(s)]


async def test_ac15_batch_of_100_ids_is_one_statement_on_photos(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    existing = await photo_ids(seeded)
    assert len(existing) == 15
    raw = ",".join(str(i) for i in [*existing, *range(10**9, 10**9 + 85)])
    assert raw.count(",") == 99

    with sql_log(app) as many:
        response = await client.get(f"{V2}/photos/batch", params={"ids": raw})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == existing
    assert body["missing"] == list(range(10**9, 10**9 + 85))
    selects = photos_statements(many)
    assert len(selects) == 1, selects
    assert re.search(r"photos\.id\s*(=\s*ANY|IN)\b", selects[0], re.IGNORECASE), selects[0]


async def test_ac15_number_of_statements_does_not_grow_with_the_number_of_ids(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    existing = await photo_ids(seeded)
    one = str(existing[0])
    many = ",".join(str(i) for i in existing)

    with sql_log(app) as for_one:
        await client.get(f"{V2}/photos/batch", params={"ids": one})
    with sql_log(app) as for_many:
        await client.get(f"{V2}/photos/batch", params={"ids": many})

    assert for_one and len(for_many) == len(for_one)
    assert len(photos_statements(for_many)) == 1


async def test_ac15_batch_answers_200_with_a_full_heavy_gate(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    existing = await photo_ids(seeded)
    fill_heavy_gate(app)

    response = await client.get(f"{V2}/photos/batch", params={"ids": ",".join(map(str, existing))})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == existing
    assert app.state.heavy_query_gate.in_use == app.state.heavy_query_gate.limit


async def test_ac15_batch_with_only_missing_ids_still_answers_with_a_full_gate(
    app: FastAPI, client: httpx.AsyncClient, seeded: Factory
) -> None:
    fill_heavy_gate(app)

    response = await client.get(f"{V2}/photos/batch", params={"ids": "999999999,0"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "missing": [999999999, 0]}
