"""Request counters of a RAD run (stage 21 follow-up): `RadRunResult.requests_*`,
`bytes_received`, the INFO log and the `rad sync` summary line.

The loader is run directly here (one client for two runs, so the result must be the delta of
the run, not the client's total); the CLI line is checked through `run_rad` and `_rad_summary`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
import respx

from app import cli
from app.db.session import create_engine
from app.services.rad_sync import RadLoader, RadRunResult, build_rad_client
from tests._rad import (
    F_1649_A,
    F_1649_B,
    F_4842,
    F_4843,
    RadHost,
    fixture,
    fixture_host,
    rel_path,
    run_rad,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

P1649_A = rel_path(F_1649_A)
P1649_B = rel_path(F_1649_B)
PROBE_BYTES = 65536


@pytest.fixture
def make_host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> Any:
    def make(*names: str) -> RadHost:
        return fixture_host(upstream_router, events, *names)

    return make


@pytest.fixture
async def loader(settings: Any) -> AsyncIterator[RadLoader]:
    engine = create_engine(settings.database_url)
    async with build_rad_client(settings) as client:
        yield RadLoader(engine, client, settings)
    await engine.dispose()


def list_bytes(host: RadHost) -> int:
    return len(host.checksums_bytes()) + len(host.timing)


async def test_rerun_with_unchanged_listings_counts_two_conditional_requests_only(
    make_host: Any, loader: RadLoader
) -> None:
    make_host(F_4842, F_4843)
    first = await loader.run(4842, 4843)
    assert first.exit_code == 0

    second = await loader.run(4842, 4843)

    assert second.not_modified is True
    assert (second.requests_get, second.requests_probe, second.requests_conditional) == (0, 0, 2)
    assert second.bytes_received == 0  # two 304 answers, no body


async def test_first_run_counts_are_a_delta_per_run_not_the_client_total(
    make_host: Any, loader: RadLoader
) -> None:
    host = make_host(F_4842, F_4843)

    first = await loader.run(4842, 4843)
    second = await loader.run(4842, 4843)

    assert (first.requests_get, first.requests_probe, first.requests_conditional) == (2, 0, 2)
    expected = list_bytes(host) + len(fixture(F_4842)) + len(fixture(F_4843))
    assert first.bytes_received == expected
    assert second.requests_get == 0 and second.bytes_received == 0
    assert first.slowest_wait >= 0.0


async def test_sol_with_a_duplicate_counts_one_full_get_and_one_probe_and_summed_bytes(
    make_host: Any, loader: RadLoader
) -> None:
    host = make_host(F_1649_A, F_1649_B)

    result = await loader.run(1649, 1649)

    assert result.exit_code == 0
    assert (result.requests_get, result.requests_probe, result.requests_conditional) == (1, 1, 2)
    tail = min(PROBE_BYTES, len(fixture(F_1649_A)))
    assert result.bytes_received == list_bytes(host) + len(fixture(F_1649_B)) + tail


async def test_retried_file_request_is_counted_again_but_its_failed_body_is_not(
    make_host: Any, loader: RadLoader
) -> None:
    host = make_host(F_4843)
    host.fail[rel_path(F_4843)] = ["503"]

    result = await loader.run(4843, 4843)

    assert result.exit_code == 0
    assert result.requests_get == 2  # the 503 attempt and the good one
    assert result.bytes_received == list_bytes(host) + len(fixture(F_4843))


async def test_run_logs_the_counters_at_info(
    make_host: Any, loader: RadLoader, caplog: pytest.LogCaptureFixture
) -> None:
    make_host(F_4843)
    caplog.set_level(logging.INFO)

    result = await loader.run(4843, 4843)

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    line = next(m for m in messages if "requests" in m and "range" in m)
    assert "1 full" in line and "0 range" in line and "2 conditional" in line
    assert str(result.bytes_received) in line


SUMMARY_TAIL = re.compile(r"; requests full=(\d+) range=(\d+) conditional=(\d+), bytes=(\d+)")


def test_summary_line_reports_requests_after_the_file_counts() -> None:
    result = RadRunResult(
        requests_get=5, requests_probe=3, requests_conditional=2, bytes_received=1234
    )

    line = cli._rad_summary(result)

    assert "; requests full=5 range=3 conditional=2, bytes=1234" in line
    assert line.index("files loaded=") < line.index("requests full=")


def test_summary_line_of_a_run_that_did_not_start_has_no_requests_fragment() -> None:
    assert "requests" not in cli._rad_summary(RadRunResult(locked=True))


async def test_cli_sync_prints_the_requests_fragment_with_real_counts(
    make_host: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    host = make_host(F_4843)

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("rad sync:"))
    match = SUMMARY_TAIL.search(line)
    assert match, line
    full, probe, conditional, received = map(int, match.groups())
    assert (full, probe, conditional) == (1, 0, 2)
    assert received == list_bytes(host) + len(fixture(F_4843))
