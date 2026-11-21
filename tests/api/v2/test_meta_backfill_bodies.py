"""AC-20 across the backfill: response bodies do not depend on the new columns (stage 18B-2).

`width`, `height` and `local_time` in the bodies still come from `extra` (D13), so the list and
detail bodies are identical with the columns empty (the window between migration 0013 and
`backfill-meta` on a live database), after the backfill, and before it. The filters, in contrast,
see only the columns: empty columns -> no match; after the backfill -> the reference result.
"""

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from tests.api.v2._meta import MRow, ids, is_golden, min_size
from tests.api.v2.conftest import V2
from tests.integration._meta_seed import null_meta_columns, seed_meta_rows

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings):  # type: ignore[no-untyped-def]
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def snapshot(client: httpx.AsyncClient, rows: list[MRow]) -> dict[str, Any]:
    """Every list body (whole dataset, both sorts) and the detail body of each sol-100 photo."""
    out: dict[str, Any] = {}
    for sort in ("-sol", "taken_at"):
        for page in (1, 2):
            response = await client.get(
                f"{V2}/photos", params={"sort": sort, "page": page, "per_page": 100}
            )
            assert response.status_code == 200
            out[f"list:{sort}:{page}"] = response.json()
    for row in rows:
        if row.sol == 100:
            response = await client.get(f"{V2}/photos/{row.id}")
            assert response.status_code == 200
            out[f"detail:{row.id}"] = response.json()
    return out


async def run_backfill() -> int:
    return await asyncio.to_thread(cli.main, ["backfill-meta"])


async def test_ac20_bodies_are_identical_with_empty_columns_and_after_backfill(
    clean_db: SessionFactory, client: httpx.AsyncClient
) -> None:
    rows = await seed_meta_rows(clean_db)
    stored_by_write_path = await snapshot(client, rows)

    await null_meta_columns(clean_db)
    with_empty_columns = await snapshot(client, rows)
    assert await run_backfill() == 0
    after_backfill = await snapshot(client, rows)

    assert with_empty_columns == stored_by_write_path
    assert after_backfill == stored_by_write_path
    # The snapshot is not vacuous: sizes and local times are really present in it.
    detail = next(v for k, v in stored_by_write_path.items() if k.startswith("detail:"))
    assert "local_time" in detail
    items = stored_by_write_path["list:-sol:1"]["items"]
    assert any(i["width"] is not None for i in items)


async def test_ac20_filters_find_nothing_while_columns_are_empty_and_work_after_backfill(
    clean_db: SessionFactory, client: httpx.AsyncClient
) -> None:
    rows = await seed_meta_rows(clean_db)
    await null_meta_columns(clean_db)
    queries = {
        "golden": ({"golden_hour": "true"}, is_golden),
        "window": (
            {"local_time_min": "12:00", "local_time_max": "12:01"},
            lambda r: r.time_s == 43200,
        ),
        "width": ({"min_width": "1920"}, lambda r: min_size(r, 1920)),
    }

    for params, _ in queries.values():
        empty = await client.get(f"{V2}/photos", params=params)
        assert empty.status_code == 200
        assert empty.json()["total"] == 0

    assert await run_backfill() == 0

    for name, (params, where) in queries.items():
        expected = sorted(
            (r for r in rows if where(r)), key=lambda r: (r.sol, r.taken_at, r.id), reverse=True
        )
        response = await client.get(f"{V2}/photos", params={**params, "per_page": "100"})
        body = response.json()
        assert body["total"] == len(expected) > 0, name
        assert [i["id"] for i in body["items"]] == ids(expected), name
