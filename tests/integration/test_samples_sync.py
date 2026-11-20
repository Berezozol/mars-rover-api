"""Samples sync (app/services/sample_sync.py; stage 13, ADR-1/3/4).

Real Postgres (`clean_db`); the NASA page is mocked with respx on the trimmed fixture. Covered:
first load, the once-a-day rule (`refresh_due`) and `refresh(force=...)`, change detection by
the parsed rows (not by the page body: it carries a changing `nonce`), conditional headers when
the server ever sends validators, acceptance rules (all or nothing, a stored number must not
disappear unless forced), atomic replacement, failures recorded in `last_error`, the advisory
lock. Scheduler and alert tests live in test_samples_scheduler.py / test_samples_alert.py.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.sample_sync import SAMPLES_LOCK_NAMESPACE, SampleSyncService
from app.sources.http import UpstreamClient
from tests._samples import (
    COL_DATE,
    COL_NAME,
    COL_SOL,
    EXPECTED,
    NUMBERS,
    SAMPLES_URL,
    SOURCE_MODIFIED,
    Factory,
    as_plain,
    count,
    dataset,
    insert_expected,
    mock_page,
    page_with_cell,
    page_with_row,
    page_without_number,
    replace_in_page,
    sample_rows,
    set_page,
    trimmed_bytes,
)
from tests.integration._fakes import legacy_id

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

H = timedelta(hours=1)


# --- fixtures and helpers ----------------------------------------------------------------------


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sample_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "samples_url": SAMPLES_URL,
            "samples_refresh_hours": 24,
            "samples_sync_enabled": True,
            "upstream_max_rps": 0,
            "upstream_max_retries": 0,
            "upstream_retry_base_seconds": 0.0,
        }
    )


@pytest.fixture
async def make_service(
    engine: AsyncEngine, sample_settings: Settings
) -> AsyncIterator[Callable[..., SampleSyncService]]:
    clients: list[UpstreamClient] = []

    def build(**overrides: Any) -> SampleSyncService:
        values = sample_settings.model_copy(update=overrides)
        client = UpstreamClient(values)
        clients.append(client)
        return SampleSyncService(engine, client, values)

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()


@pytest.fixture
def service(make_service: Callable[..., SampleSyncService]) -> SampleSyncService:
    return make_service()


@pytest.fixture
def page(upstream_router: respx.MockRouter) -> respx.Route:
    return mock_page(upstream_router)


async def first_load(service: SampleSyncService) -> None:
    result = await service.refresh_due(datetime.now(UTC))
    assert result.status == "changed"


async def stored(clean_db: Factory) -> list[dict[str, Any]]:
    return [as_plain(row) for row in await sample_rows(clean_db)]


def expected_rows(numbers: list[int] = NUMBERS) -> list[dict[str, Any]]:
    return [EXPECTED[n] for n in numbers]


# --- first load --------------------------------------------------------------------------------


async def test_refresh_due_first_load_stores_the_rows_and_reports_changed(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    result = await service.refresh_due(datetime.now(UTC))

    assert result.status == "changed"
    assert page.call_count == 1
    assert await stored(clean_db) == expected_rows()


async def test_first_load_stores_the_dataset_state(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)

    state = await dataset(clean_db)
    assert state is not None
    assert state["items"] == 7
    assert state["source_modified_at"] == SOURCE_MODIFIED
    assert state["last_error"] is None
    for column in ("created_at", "checked_at", "succeeded_at", "changed_at"):
        assert state[column] is not None, column
    assert isinstance(state["content_sha256"], str) and len(state["content_sha256"]) == 64


async def test_first_load_sends_no_conditional_headers(
    service: SampleSyncService, page: respx.Route
) -> None:
    await first_load(service)

    headers = page.calls.last.request.headers
    assert "if-none-match" not in headers and "if-modified-since" not in headers


async def test_first_load_stores_rows_only_for_perseverance(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)

    assert await count(clean_db, "rover_samples") == 7
    assert await count(clean_db, "sample_datasets") == 1
    assert [r["rover_id"] for r in await sample_rows(clean_db)] == ["perseverance"] * 7


# --- once a day --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "status", "calls"),
    [
        pytest.param(H, "skipped", 1, id="1h-skipped"),
        pytest.param(23 * H, "skipped", 1, id="23h-skipped"),
        pytest.param(25 * H, "unchanged", 2, id="25h-checked"),
        pytest.param(timedelta(days=10), "unchanged", 2, id="10d-checked"),
    ],
)
async def test_refresh_due_follows_the_daily_schedule(
    service: SampleSyncService,
    page: respx.Route,
    clean_db: Factory,
    offset: timedelta,
    status: str,
    calls: int,
) -> None:
    await first_load(service)
    before = await dataset(clean_db)

    result = await service.refresh_due(datetime.now(UTC) + offset)

    assert result.status == status
    assert page.call_count == calls
    after = await dataset(clean_db)
    if status == "skipped":
        assert after == before  # nothing is written when it is not due
    else:
        assert after["succeeded_at"] > before["succeeded_at"]
        assert after["changed_at"] == before["changed_at"]


async def test_refresh_due_uses_the_refresh_hours_setting(
    make_service: Callable[..., SampleSyncService], page: respx.Route
) -> None:
    service = make_service(samples_refresh_hours=6)
    await first_load(service)

    assert (await service.refresh_due(datetime.now(UTC) + 5 * H)).status == "skipped"
    assert (await service.refresh_due(datetime.now(UTC) + 7 * H)).status == "unchanged"
    assert page.call_count == 2


async def test_refresh_ignores_the_schedule(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)

    result = await service.refresh()

    assert result.status == "unchanged"
    assert page.call_count == 2


async def test_refresh_after_a_failure_is_retried_at_the_next_cycle(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    """A failed check does not move `succeeded_at`: the next `refresh_due` asks again."""
    await first_load(service)
    page.mock(return_value=httpx.Response(500))
    failed = await service.refresh_due(datetime.now(UTC) + 25 * H)
    assert failed.status == "failed"
    set_page(page, trimmed_bytes())

    result = await service.refresh_due(datetime.now(UTC) + 25 * H + 1 * H)

    assert result.status == "unchanged"
    assert (await dataset(clean_db))["last_error"] is None


# --- change detection --------------------------------------------------------------------------


async def test_unchanged_rows_in_a_page_with_new_markup_are_unchanged(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    """The page body differs on every request (`nonce`): only the parsed rows decide."""
    await first_load(service)
    before = await dataset(clean_db)
    rows_before = await sample_rows(clean_db)
    set_page(
        page,
        replace_in_page("<main>", '<main><script nonce="zzz-new-nonce">1</script>'),
    )

    result = await service.refresh()

    assert result.status == "unchanged"
    after = await dataset(clean_db)
    assert after["content_sha256"] == before["content_sha256"]
    assert after["changed_at"] == before["changed_at"]
    assert after["succeeded_at"] > before["succeeded_at"]
    assert after["checked_at"] > before["checked_at"]
    assert await sample_rows(clean_db) == rows_before


async def test_content_hash_is_not_the_hash_of_the_page_body(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)

    state = await dataset(clean_db)
    assert state["content_sha256"] != hashlib.sha256(trimmed_bytes()).hexdigest()


async def test_changed_row_replaces_the_data_and_moves_changed_at(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    before = await dataset(clean_db)
    set_page(page, page_with_cell(25, COL_SOL, "1216"))

    result = await service.refresh()

    assert result.status == "changed"
    expected = [dict(row) for row in expected_rows()]
    next(row for row in expected if row["number"] == 25)["sol"] = 1216
    assert await stored(clean_db) == expected
    after = await dataset(clean_db)
    assert after["changed_at"] > before["changed_at"]
    assert after["content_sha256"] != before["content_sha256"]
    assert after["items"] == 7


async def test_tbd_filled_in_on_the_page_is_a_change(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    body = page_with_cell(29, COL_SOL, "1500")
    set_page(page, body)

    assert (await service.refresh()).status == "changed"

    row = next(r for r in await stored(clean_db) if r["number"] == 29)
    assert row["sol"] == 1500


async def test_new_sample_on_the_page_is_added(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    set_page(page, page_with_row(31, name="Thirty-One", sol="1600", date="Oct. 3, 2026"))

    assert (await service.refresh()).status == "changed"

    rows = await stored(clean_db)
    assert [r["number"] for r in rows] == [*NUMBERS, 31]
    assert rows[-1]["name"] == "Thirty-One"
    assert rows[-1]["sealed_on"] == "2026-10-03"
    assert (await dataset(clean_db))["items"] == 8


async def test_changed_page_modified_time_without_row_changes_is_unchanged(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    set_page(page, replace_in_page("2026-06-20T12:45:46-04:00", "2026-11-01T00:00:00-04:00"))

    result = await service.refresh()

    assert result.status == "unchanged"
    assert await stored(clean_db) == expected_rows()


# --- conditional headers (if the server ever sends validators) ---------------------------------


async def test_validators_from_the_server_are_sent_back_and_304_is_unchanged(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    page.mock(
        return_value=httpx.Response(
            200,
            content=trimmed_bytes(),
            headers={
                "Content-Type": "text/html; charset=UTF-8",
                "ETag": '"v1"',
                "Last-Modified": "Sat, 20 Jun 2026 16:45:46 GMT",
            },
        )
    )
    await first_load(service)
    before = await dataset(clean_db)
    page.mock(return_value=httpx.Response(304))

    result = await service.refresh()

    assert result.status == "unchanged"
    headers = page.calls.last.request.headers
    assert headers["if-none-match"] == '"v1"'
    assert headers["if-modified-since"] == "Sat, 20 Jun 2026 16:45:46 GMT"
    after = await dataset(clean_db)
    assert after["succeeded_at"] > before["succeeded_at"]
    assert after["changed_at"] == before["changed_at"]
    assert await stored(clean_db) == expected_rows()


async def test_force_drops_the_conditional_headers(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    page.mock(
        return_value=httpx.Response(
            200, content=trimmed_bytes(), headers={"ETag": '"v1"', "Content-Type": "text/html"}
        )
    )
    await first_load(service)

    result = await service.refresh(force=True)

    assert result.status in {"changed", "unchanged"}
    assert "if-none-match" not in page.calls.last.request.headers


async def test_force_rewrites_the_rows_even_when_the_parsed_page_is_unchanged(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    async with clean_db() as session:
        await session.execute(text("UPDATE rover_samples SET rock_type = 'TAMPERED'"))
        await session.commit()

    assert (await service.refresh()).status == "unchanged"
    assert {r["rock_type"] for r in await sample_rows(clean_db)} == {"TAMPERED"}  # not touched

    result = await service.refresh(force=True)

    assert result.status in {"changed", "unchanged"}
    assert await stored(clean_db) == expected_rows()  # restored from the page
    assert (await dataset(clean_db))["last_error"] is None


# --- acceptance: all or nothing ----------------------------------------------------------------

BAD_PAGES: list[Any] = [
    pytest.param(replace_in_page("<th>Sol Sealed</th>", "<th>Sol</th>"), id="header-renamed"),
    pytest.param(b"<html><body>Maintenance</body></html>", id="no-table"),
    pytest.param(page_with_row(8), id="duplicate-number"),
    pytest.param(page_with_cell(25, COL_SOL, "abc"), id="sol-abc"),
    pytest.param(page_with_cell(25, COL_DATE, "Foo 1, 2021"), id="date-foo"),
    pytest.param(page_with_cell(25, COL_NAME, ""), id="empty-name"),
    pytest.param(page_without_number(8), id="stored-number-disappeared"),
]


@pytest.mark.parametrize("body", BAD_PAGES)
async def test_rejected_page_keeps_the_data_and_records_the_reason(
    service: SampleSyncService, page: respx.Route, clean_db: Factory, body: bytes
) -> None:
    await first_load(service)
    before = await dataset(clean_db)
    rows_before = await sample_rows(clean_db)
    set_page(page, body)

    result = await service.refresh()

    assert result.status == "rejected"
    assert await sample_rows(clean_db) == rows_before
    after = await dataset(clean_db)
    assert after["last_error"]
    assert after["items"] == before["items"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["changed_at"] == before["changed_at"]
    assert after["succeeded_at"] == before["succeeded_at"]  # alerts count from the last success
    assert after["checked_at"] > before["checked_at"]


async def test_page_with_a_changed_layout_on_the_first_load_stores_nothing(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    set_page(page, replace_in_page("<th>Sol Sealed</th>", "<th>Sol</th>"))

    result = await service.refresh_due(datetime.now(UTC))

    assert result.status == "rejected"
    assert await count(clean_db, "rover_samples") == 0
    state = await dataset(clean_db)
    assert state is None or (state["last_error"] and state["succeeded_at"] is None)


async def test_disappeared_number_is_accepted_with_force(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    set_page(page, page_without_number(8))
    assert (await service.refresh()).status == "rejected"

    result = await service.refresh(force=True)

    assert result.status == "changed"
    assert [r["number"] for r in await stored(clean_db)] == [1, 17, 25, 26, 29, 30]
    state = await dataset(clean_db)
    assert state["items"] == 6 and state["last_error"] is None


async def test_force_does_not_accept_a_broken_page(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    set_page(page, page_with_cell(25, COL_SOL, "abc"))

    result = await service.refresh(force=True)

    assert result.status == "rejected"
    assert await stored(clean_db) == expected_rows()


async def test_good_page_after_a_rejection_clears_the_error(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)
    set_page(page, page_with_cell(25, COL_SOL, "abc"))
    assert (await service.refresh()).status == "rejected"
    assert (await dataset(clean_db))["last_error"]
    set_page(page, trimmed_bytes())

    result = await service.refresh()

    assert result.status == "unchanged"
    state = await dataset(clean_db)
    assert state["last_error"] is None
    assert await stored(clean_db) == expected_rows()


# --- failures ----------------------------------------------------------------------------------

FAILURES: list[Any] = [
    pytest.param(httpx.Response(500), id="http-500"),
    pytest.param(httpx.Response(503), id="http-503"),
    pytest.param(httpx.Response(404), id="http-404"),
    pytest.param(httpx.ConnectError("boom"), id="connect-error"),
    pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
]


@pytest.mark.parametrize("outcome", FAILURES)
async def test_unreachable_source_fails_keeps_the_data_and_records_the_error(
    service: SampleSyncService,
    page: respx.Route,
    clean_db: Factory,
    outcome: Any,
) -> None:
    await first_load(service)
    before = await dataset(clean_db)
    rows_before = await sample_rows(clean_db)
    if isinstance(outcome, Exception):
        page.mock(side_effect=outcome)
    else:
        page.mock(return_value=outcome)

    result = await service.refresh()

    assert result.status == "failed"
    assert await sample_rows(clean_db) == rows_before
    after = await dataset(clean_db)
    assert after["last_error"]
    assert after["succeeded_at"] == before["succeeded_at"]
    assert after["changed_at"] == before["changed_at"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["checked_at"] > before["checked_at"]


async def test_page_over_the_size_limit_is_not_stored(
    make_service: Callable[..., SampleSyncService], page: respx.Route, clean_db: Factory
) -> None:
    service = make_service(samples_max_bytes=2048)  # the trimmed page is about 4 KB

    result = await service.refresh_due(datetime.now(UTC))

    assert result.status in {"failed", "rejected"}
    assert await count(clean_db, "rover_samples") == 0


# --- atomic replacement ------------------------------------------------------------------------


@pytest.fixture
async def reject_sol_9999(engine: AsyncEngine) -> AsyncIterator[None]:
    """A trigger that fails any write of a row with sol 9999 (to break a replacement midway)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION qa_reject_sol() RETURNS trigger AS $$ BEGIN "
                "IF NEW.sol = 9999 THEN RAISE EXCEPTION 'qa: row refused'; END IF; "
                "RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS qa_reject_sol ON rover_samples"))
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER IF EXISTS qa_reject_sol ON rover_samples"))
            await conn.execute(text("DROP FUNCTION IF EXISTS qa_reject_sol()"))


async def test_failure_in_the_middle_of_a_replacement_leaves_the_old_list(
    service: SampleSyncService,
    page: respx.Route,
    clean_db: Factory,
    engine: AsyncEngine,
    reject_sol_9999: None,
) -> None:
    await first_load(service)
    rows_before = await sample_rows(clean_db)
    before = await dataset(clean_db)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TRIGGER qa_reject_sol BEFORE INSERT OR UPDATE ON rover_samples "
                "FOR EACH ROW EXECUTE FUNCTION qa_reject_sol()"
            )
        )
    set_page(page, page_with_cell(25, COL_SOL, "9999"))

    result = await service.refresh()

    assert result.status == "failed"
    assert await sample_rows(clean_db) == rows_before  # all 7 old rows, none half-replaced
    after = await dataset(clean_db)
    assert after["last_error"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["changed_at"] == before["changed_at"]


# --- lock --------------------------------------------------------------------------------------


def test_lock_namespace_is_the_agreed_constant() -> None:
    from app.services.location_sync import LOCATION_LOCK_NAMESPACE
    from app.services.sync import SYNC_LOCK_NAMESPACE

    assert SAMPLES_LOCK_NAMESPACE == 7_250_003
    assert SAMPLES_LOCK_NAMESPACE not in {LOCATION_LOCK_NAMESPACE, SYNC_LOCK_NAMESPACE}


async def test_busy_advisory_lock_skips_the_run_without_writes(
    service: SampleSyncService,
    page: respx.Route,
    clean_db: Factory,
    engine: AsyncEngine,
) -> None:
    key = await legacy_id(clean_db, "perseverance")
    async with engine.connect() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": SAMPLES_LOCK_NAMESPACE, "key": key},
        )

        result = await service.refresh()

    assert result.status == "skipped"
    assert await count(clean_db, "rover_samples") == 0


async def test_lock_is_released_after_the_run(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await first_load(service)

    holders = await dataset_locks(clean_db)
    assert holders == 0
    assert (await service.refresh()).status == "unchanged"


async def dataset_locks(factory: Factory) -> int:
    from tests._samples import rows

    found = await rows(
        factory,
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = :ns "
        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
        ns=SAMPLES_LOCK_NAMESPACE,
    )
    return found[0]["n"]


async def test_samples_lock_does_not_block_photo_sync_locks(
    service: SampleSyncService, page: respx.Route, clean_db: Factory, engine: AsyncEngine
) -> None:
    from app.services.sync import SYNC_LOCK_NAMESPACE

    async with engine.connect() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_LOCK_NAMESPACE, "key": await legacy_id(clean_db, "perseverance")},
        )

        assert (await service.refresh_due(datetime.now(UTC))).status == "changed"


# --- rows without state ------------------------------------------------------------------------


async def test_refresh_with_rows_but_no_dataset_state_stores_the_page(
    service: SampleSyncService, page: respx.Route, clean_db: Factory
) -> None:
    await insert_expected(clean_db, [1])  # a pre-existing row of the same rover is replaced
    set_page(page, trimmed_bytes())

    result = await service.refresh()

    assert result.status == "changed"
    assert await stored(clean_db) == expected_rows()
    assert (await dataset(clean_db))["items"] == 7
