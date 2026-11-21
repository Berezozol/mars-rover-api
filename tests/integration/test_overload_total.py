"""Overload protection, integration part (docs/proposals/overload-protection.md).

AC-2: `_total` takes the source `classify_total` names; a count over `photos` happens only
for `scan_*`. The repository functions are spied on (no rows are needed).
AC-10: `SHOW jit` is `off` on connections of the API engine and the server value on the
sync (worker) engine.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app
from app.repositories import photos as photos_repo
from app.services.v2 import TotalSource, _total, classify_total
from tests._overload import CASES, ROVER, make_filter

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_db")]

SUMMARY_SOURCES = (
    "camera_total_photos",
    "sol_range_total_photos",
    "rover_total_photos_from_summaries",
    "sol_total_photos",
)
ROWS = [pytest.param(kwargs, expected, id=case_id) for case_id, kwargs, expected in CASES]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace every total source by a counter that returns 0."""
    counter = {name: 0 for name in ("count_photos", *SUMMARY_SOURCES)}

    def spy(name: str) -> Any:
        async def fake(*args: Any, **kwargs: Any) -> int:
            counter[name] += 1
            return 0

        return fake

    for name in counter:
        monkeypatch.setattr(photos_repo, name, spy(name))
    return counter


@pytest.mark.parametrize(("kwargs", "expected"), ROWS)
async def test_total_source_follows_classify_total(
    db_session: AsyncSession, calls: dict[str, int], kwargs: dict[str, Any], expected: str | None
) -> None:
    """AC-2: summary -> summaries/camera counts only; scan_light / scan_heavy -> one count."""
    flt = make_filter(kwargs)
    source = classify_total(flt)
    if expected is not None:
        assert source is TotalSource[expected]  # the table row, not just self-consistency

    await _total(db_session, ROVER, flt)

    summary_calls = sum(calls[name] for name in SUMMARY_SOURCES)
    if source is TotalSource.SUMMARY:
        assert calls["count_photos"] == 0, calls
        assert summary_calls >= 1, calls
    else:
        assert calls["count_photos"] == 1, calls
        assert summary_calls == 0, calls


async def test_total_full_without_sol_reads_summaries_not_photos(
    db_session: AsyncSession, calls: dict[str, int]
) -> None:
    """AC-2 / AC-11 (decision 0): `sample_type=full` alone is answered from sol_summaries."""
    await _total(db_session, ROVER, make_filter({"sample_type": "full"}))

    assert calls["count_photos"] == 0
    assert calls["sol_range_total_photos"] == 1


# --- AC-10 -----------------------------------------------------------------------------------


async def _show_jit(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return str((await connection.execute(text("SHOW jit"))).scalar_one())


async def test_api_engine_connection_has_jit_off_and_worker_engine_keeps_server_value(
    settings: Settings,
) -> None:
    """AC-10."""
    raw = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        server_value = await _show_jit(raw)
    finally:
        await raw.dispose()
    if server_value == "off":
        pytest.skip("the test server runs with jit=off already: the check would prove nothing")

    app = create_app(settings)
    try:
        assert await _show_jit(app.state.engine) == "off"
        assert await _show_jit(app.state.worker_engine) == server_value
    finally:
        await app.state.engine.dispose()
        await app.state.worker_engine.dispose()


@pytest.mark.parametrize("statement_timeout", [None, 5.0], ids=["no-timeout", "with-timeout"])
async def test_create_engine_disable_jit_turns_jit_off(
    settings: Settings, statement_timeout: float | None
) -> None:
    """AC-10: the option works on its own and together with the statement timeout."""
    engine = create_engine(
        settings.database_url, statement_timeout=statement_timeout, disable_jit=True
    )
    try:
        assert await _show_jit(engine) == "off"
    finally:
        await engine.dispose()
