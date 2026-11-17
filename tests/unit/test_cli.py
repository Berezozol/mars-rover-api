"""CLI ``python -m app.cli sync`` (app/cli.py): argument parsing, exit codes, summary line.

The sync itself is replaced: either the async core ``run_sync_command`` or
``SyncService.run``, so no database or network is touched. DATABASE_URL still points to the
test database in case anything slips through.
"""

import inspect
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app import cli
from app.core.config import Settings, get_settings
from app.core.errors import SyncInProgress
from app.services import sync as sync_module
from app.services.sync import SyncResult
from app.sources.http import UpstreamClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def test_database_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("SYNC_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def exit_code(argv: list[str]) -> int:
    """main() return value; an argparse SystemExit is accepted as the same exit code."""
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


class CoreSpy:
    """Replacement for ``run_sync_command`` that records its bound arguments."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[dict[str, Any]] = []
        self.signature = inspect.signature(cli.run_sync_command)

    async def __call__(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(dict(self.signature.bind(*args, **kwargs).arguments))
        return self.code


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> CoreSpy:
    spy = CoreSpy()
    monkeypatch.setattr(cli, "run_sync_command", spy)
    return spy


# --- parsing -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "rover", "from_sol", "to_sol"),
    [
        pytest.param(["sync", "--rover", "perseverance"], "perseverance", None, None, id="incr"),
        pytest.param(["sync", "--rover", "Curiosity"], "curiosity", None, None, id="title-case"),
        pytest.param(["sync", "--rover", "PERSEVERANCE"], "perseverance", None, None, id="upper"),
        pytest.param(
            ["sync", "--rover", "perseverance", "--from-sol", "2029", "--to-sol", "2031"],
            "perseverance",
            2029,
            2031,
            id="backfill",
        ),
    ],
)
def test_main_parses_arguments_into_core_call(
    core: CoreSpy, argv: list[str], rover: str, from_sol: int | None, to_sol: int | None
) -> None:
    assert exit_code(argv) == 0

    [call] = core.calls
    assert call["rover"] == rover
    assert (call["from_sol"], call["to_sol"]) == (from_sol, to_sol)
    assert isinstance(call["settings"], Settings)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["sync", "--rover", "curiosity", "--from-sol", "5"], id="only-from"),
        pytest.param(["sync", "--rover", "curiosity", "--to-sol", "5"], id="only-to"),
    ],
)
def test_main_only_one_bound_returns_2_with_stderr_message(
    core: CoreSpy, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert cli.main(argv) == 2

    captured = capsys.readouterr()
    assert "--from-sol" in captured.err or "--to-sol" in captured.err
    assert core.calls == []


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["sync"], id="missing-rover"),
        pytest.param(
            ["sync", "--rover", "curiosity", "--from-sol", "x", "--to-sol", "5"], id="nan"
        ),
        pytest.param(["frobnicate"], id="unknown-command"),
        pytest.param([], id="no-command"),
    ],
)
def test_main_usage_errors_exit_2(
    core: CoreSpy, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert exit_code(argv) == 2
    assert capsys.readouterr().err
    assert core.calls == []


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_main_returns_core_exit_code(core: CoreSpy, code: int) -> None:
    core.code = code

    assert cli.main(["sync", "--rover", "curiosity"]) == code


# --- exit codes and summary through the real core (SyncService.run replaced) --------------


class RunSpy:
    def __init__(self, outcome: SyncResult | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, service: Any, adapter: Any, *, from_sol: int | None = None, to_sol: int | None = None
    ) -> SyncResult:
        self.calls.append({"rover_id": adapter.rover_id, "from_sol": from_sol, "to_sol": to_sol})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def make_result(status: str, **changes: Any) -> SyncResult:
    values: dict[str, Any] = {
        "run_id": 7,
        "rover_id": "perseverance",
        "mode": "backfill",
        "from_sol": 2029,
        "to_sol": 2031,
        "status": status,
        "photos_upserted": 12,
        "failed_sols": [2030, 2031] if status != "success" else [],
        "error": None if status == "success" else "failed sols: 2030-2031 ; last error: X: y",
    }
    values.update(changes)
    return SyncResult(**values)


def patch_run(monkeypatch: pytest.MonkeyPatch, outcome: SyncResult | BaseException) -> RunSpy:
    spy = RunSpy(outcome)

    async def run(self: Any, adapter: Any, **kwargs: Any) -> SyncResult:
        return await spy(self, adapter, **kwargs)

    monkeypatch.setattr(sync_module.SyncService, "run", run)
    return spy


@pytest.mark.parametrize(
    ("status", "code"),
    [("success", 0), ("partial", 1), ("failed", 2)],
)
def test_main_exit_code_by_sync_status_and_summary_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str, code: int
) -> None:
    run = patch_run(monkeypatch, make_result(status))

    assert (
        cli.main(["sync", "--rover", "Perseverance", "--from-sol", "2029", "--to-sol", "2031"])
        == code
    )

    assert run.calls == [{"rover_id": "perseverance", "from_sol": 2029, "to_sol": 2031}]
    failed = 0 if status == "success" else 2
    expected = (
        f"perseverance backfill 2029-2031: {status}, photos=12, failed_sols={failed}, run_id=7"
    )
    assert expected in capsys.readouterr().out.splitlines()


def test_main_incremental_summary_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = make_result(
        "success", rover_id="curiosity", mode="incremental", from_sol=4320, to_sol=4327, run_id=3
    )
    run = patch_run(monkeypatch, result)

    assert cli.main(["sync", "--rover", "curiosity"]) == 0

    assert run.calls == [{"rover_id": "curiosity", "from_sol": None, "to_sol": None}]
    assert (
        "curiosity incremental 4320-4327: success, photos=12, failed_sols=0, run_id=3"
        in capsys.readouterr().out.splitlines()
    )


def test_main_sync_in_progress_exit_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_run(monkeypatch, SyncInProgress("curiosity"))

    assert cli.main(["sync", "--rover", "curiosity"]) == 3
    assert capsys.readouterr().err


def test_main_invalid_range_value_error_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_run(monkeypatch, ValueError("from_sol 9 is greater than to_sol 5"))

    assert cli.main(["sync", "--rover", "curiosity", "--from-sol", "9", "--to-sol", "5"]) == 2
    assert "greater" in capsys.readouterr().err


@pytest.mark.parametrize(
    "rover",
    [
        pytest.param("pluto", id="unknown"),
        pytest.param("spirit", id="no-source-spirit"),
        pytest.param("Opportunity", id="no-source-opportunity"),
    ],
)
def test_main_rover_without_source_exit_2_with_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rover: str
) -> None:
    run = patch_run(monkeypatch, make_result("success"))

    assert exit_code(["sync", "--rover", rover]) == 2

    assert rover.lower() in capsys.readouterr().err.lower()
    assert run.calls == []


# --- resources of the async core ---------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        pytest.param(RuntimeError("boom"), 2, id="unexpected-error"),
        pytest.param(SyncInProgress("curiosity"), 3, id="in-progress"),
        pytest.param(ValueError("bad range"), 2, id="value-error"),
        pytest.param(make_result("success", rover_id="curiosity"), 0, id="success"),
    ],
)
async def test_run_sync_command_releases_engine_and_client(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    outcome: SyncResult | BaseException,
    code: int,
) -> None:
    patch_run(monkeypatch, outcome)
    disposed: list[Any] = []
    closed: list[Any] = []
    original_dispose = AsyncEngine.dispose
    original_aclose = UpstreamClient.aclose

    async def dispose(self: AsyncEngine, *args: Any, **kwargs: Any) -> None:
        disposed.append(self)
        await original_dispose(self, *args, **kwargs)

    async def aclose(self: UpstreamClient) -> None:
        closed.append(self)
        await original_aclose(self)

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(UpstreamClient, "aclose", aclose)

    try:
        result = await cli.run_sync_command("curiosity", None, None, settings)
    except Exception as exc:  # propagating is acceptable, leaking is not
        result = exc

    if isinstance(result, int):
        assert result == code
    assert len(disposed) == 1
    assert len(closed) == 1
