"""`rad sync` end to end: CLI -> RadLoader -> database, the PDS host mocked with respx.

Stage 21, AC-8 (limits), AC-11, AC-12 ... AC-26 (docs/proposals/stage-21-rad.md). The host is
`tests/_rad.RadHost` (conditional lists, Range, failures); asyncio sleeps are faked, so pauses,
attempts and the cool-down are checked by what was asked to sleep, not by waiting.
"""

from __future__ import annotations

import builtins
import io
from datetime import date
from typing import Any

import pytest
import respx

from app.sources.http import UpstreamClient
from tests._rad import (
    CHECKSUMS,
    F_1649_A,
    F_1649_B,
    F_4842,
    F_4843,
    F_SOL10,
    PAUSE,
    TIMING,
    Factory,
    RadHost,
    daily_row,
    file_row,
    fixture,
    fixture_host,
    good_file,
    link_of,
    md5,
    obs_block,
    rad_daily,
    rad_dataset,
    rad_file,
    rad_files,
    rel_path,
    run_rad,
    sleep_between_requests,
    synthetic_host,
    synthetic_name,
    synthetic_path,
    twenty_sols,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

P4842 = rel_path(F_4842)
P4843 = rel_path(F_4843)
P1649_A = rel_path(F_1649_A)
P1649_B = rel_path(F_1649_B)
P10 = rel_path(F_SOL10)

# Expected sol summary of the sol 4843 / 4842 fixtures: obs 0, 2, 174 are used (930 s each),
# obs 22 has QC=00000001. Doses are uGy/h as stored.
B_4843 = (6.84128, 6.78671, 6.97246)
E_4843 = (6.58689, 6.53178, 6.43259)


def approx(value: float) -> Any:
    return pytest.approx(value, rel=1e-9)


def check_4843_summary(row: dict[str, Any]) -> None:
    assert row["dose_b_mean"] == approx(sum(B_4843) / 3)
    assert row["dose_b_min"] == approx(min(B_4843))
    assert row["dose_b_max"] == approx(max(B_4843))
    assert row["dose_e_mean"] == approx(sum(E_4843) / 3)
    assert row["dose_e_min"] == approx(min(E_4843))
    assert row["dose_e_max"] == approx(max(E_4843))
    assert row["bu_count"] == 6454 + 6487 + 6509
    assert row["live_s"] == approx(3 * 930)
    assert (row["n_used"], row["n_dropped_qc"], row["n_dropped_low_rate"]) == (3, 1, 0)
    assert row["n_dropped_invalid"] == 0
    assert row["qc_bits"] == 0


@pytest.fixture
def make_host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> Any:
    """`make_host(*fixture_names)` -> RadHost serving those trimmed fixtures + TIMING_ERRORS."""

    def make(*names: str) -> RadHost:
        return fixture_host(upstream_router, events, *names)

    return make


@pytest.fixture
def host_4842_4843(make_host: Any) -> RadHost:
    return make_host(F_4842, F_4843)


# --- AC-12: first load ---------------------------------------------------------------------------


async def test_sync_empty_database_loads_two_sols_with_four_requests_ac12(
    host_4842_4843: RadHost, clean_db: Factory, events: list[tuple[str, Any]]
) -> None:
    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 0
    paths = [r.path for r in host_4842_4843.calls()]
    assert len(paths) == 4
    assert sorted(paths[:2]) == [CHECKSUMS, TIMING]
    assert paths[2:] == [P4842, P4843]  # ascending sol order
    assert host_4842_4843.max_inflight == 1  # one request at a time
    files = await rad_files(clean_db)
    assert [(f["path"], f["status"], f["sol"]) for f in files] == [
        (P4842, "loaded", 4842),
        (P4843, "loaded", 4843),
    ]
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842, 4843]


async def test_sync_pauses_between_requests_ac12(
    host_4842_4843: RadHost, events: list[tuple[str, Any]]
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    gaps = sleep_between_requests(events)
    assert len(gaps) == 3
    # RAD_PAUSE_SECONDS=3.0 from the end of one request to the start of the next; the real
    # clock ran a few milliseconds, so allow a small tolerance.
    assert all(gap >= PAUSE - 0.5 for gap in gaps), gaps


async def test_sync_stores_file_rows_with_header_facts_ac12(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    row = await file_row(clean_db, P4843)
    assert row["md5"] == md5(fixture(F_4843))
    assert row["role"] == "primary"
    assert row["duplicate_of"] is None and row["dup_check"] is None
    assert row["size_bytes"] == len(fixture(F_4843))
    assert row["rdr_version"] == 0
    assert (row["blocks"], row["dose_obs"]) == (7, 4)
    assert row["creation_time"].isoformat().startswith("2026-06-29T17:52:08")
    assert row["timing_listed"] is False
    assert row["error"] is None
    assert row["processed_at"] is not None


async def test_sync_stores_the_daily_summary_in_uGy_per_hour_ac12(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    check_4843_summary(row)
    assert row["earth_date"] == date(2026, 3, 21)  # START_TIME of the file, day 080
    assert row["source_path"] == P4843
    assert row["timing_caveat"] is False
    assert row["updated_at"] is not None
    other = await daily_row(clean_db, 4842)
    assert other is not None and other["earth_date"] == date(2026, 3, 20)
    assert other["source_path"] == P4842


async def test_sync_records_the_list_validators_and_run_state_ac12(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    dataset = await rad_dataset(clean_db)
    assert dataset is not None
    assert dataset["checksums_etag"] == '"cs-1"'
    assert dataset["timing_etag"] == '"tm-1"'
    assert dataset["checksums_last_modified"] is not None
    assert dataset["files_listed"] == 2
    assert dataset["checked_at"] is not None and dataset["succeeded_at"] is not None
    assert dataset["last_error"] is None


async def test_sync_sends_the_user_agent_of_the_project(
    host_4842_4843: RadHost, upstream_router: respx.MockRouter
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    assert all(r.user_agent and "mars-rover-api" in r.user_agent for r in host_4842_4843.calls())


async def test_sync_range_limits_the_files_to_the_requested_sols_ac12(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_SOL10, F_4842, F_4843)

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert [r.path for r in host.calls("files")] == [P4843]
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4843]


async def test_sync_without_range_loads_every_listed_sol(make_host: Any, clean_db: Factory) -> None:
    host = make_host(F_SOL10, F_4842, F_4843)

    assert await run_rad("sync") == 0

    assert sorted(r.path for r in host.calls("files")) == sorted([P10, P4842, P4843])
    assert [r["sol"] for r in await rad_daily(clean_db)] == [10, 4842, 4843]


async def test_sync_early_format_file_gets_low_rate_observation_dropped_and_date_ac12(
    make_host: Any, clean_db: Factory
) -> None:
    make_host(F_SOL10)

    assert await run_rad("sync", "--from-sol", "10", "--to-sol", "10") == 0

    row = await daily_row(clean_db, 10)
    assert row is not None
    assert (row["n_used"], row["n_dropped_low_rate"], row["n_dropped_qc"]) == (3, 1, 0)
    assert row["live_s"] == approx(3 * 960)
    assert row["dose_b_mean"] == approx((8.40398 + 8.72671 + 9.77913) / 3)
    assert row["bu_count"] == 7817 + 7817 + 7707
    # The garbage UTC 2013-123 of obs 12 changes neither the sol nor the date.
    assert row["earth_date"] == date(2012, 8, 15)  # START_TIME 2012-228
    assert row["timing_caveat"] is True  # the Date Range Error names this file


async def test_sync_with_an_inverted_range_exits_2_without_requests(
    host_4842_4843: RadHost,
) -> None:
    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4842")

    assert code == 2
    assert host_4842_4843.calls() == []


# --- AC-13: repeat -------------------------------------------------------------------------------


async def test_sync_again_asks_two_conditional_questions_and_downloads_nothing_ac13(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0
    before = await rad_daily(clean_db)
    host_4842_4843.requests.clear()

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 0
    calls = host_4842_4843.calls()
    assert sorted(r.path for r in calls) == [CHECKSUMS, TIMING]
    etags = {r.path: r.if_none_match for r in calls}
    assert etags == {CHECKSUMS: '"cs-1"', TIMING: '"tm-1"'}
    assert host_4842_4843.calls("files") == []
    assert await rad_daily(clean_db) == before  # updated_at too


async def test_sync_again_with_a_changed_list_only_reads_the_changed_files_ac13(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_4842, F_4843)
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0
    host.requests.clear()
    host.bump(CHECKSUMS)  # new ETag, same content: the list is read again, no file changed

    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    assert host.calls("files") == []  # every md5 is the same as stored
    assert [r.path for r in host.calls("lists")].count(CHECKSUMS) == 1


# --- AC-14 / AC-15: several files of one sol -----------------------------------------------------


def sol_1649_expected() -> dict[str, float]:
    live = (909, 910, 910)
    b = (10.09816, 10.15369, 10.87134)
    e = (11.33325, 11.33346, 11.06383)
    return {
        "b_mean": sum(x * w for x, w in zip(b, live, strict=True)) / sum(live),
        "e_mean": sum(x * w for x, w in zip(e, live, strict=True)) / sum(live),
        "live": float(sum(live)),
        "count": 8552 + 8538 + 8580,
    }


async def test_sync_duplicate_file_is_checked_by_a_tail_probe_not_downloaded_ac14(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_1649_A, F_1649_B)

    code = await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649")

    assert code == 0
    file_calls = host.calls("files")
    assert len(file_calls) == 2  # one full download + one probe
    full = [r for r in file_calls if r.range is None]
    probes = host.calls("probes")
    assert [r.path for r in full] == [P1649_B]  # the later name is the primary file
    assert [(r.path, r.range) for r in probes] == [(P1649_A, "bytes=-65536")]
    assert host.streamed.get(P1649_A, 0) <= 65536  # a probe reads at most the tail

    primary = await file_row(clean_db, P1649_B)
    extra = await file_row(clean_db, P1649_A)
    assert (primary["status"], primary["role"]) == ("loaded", "primary")
    assert (extra["status"], extra["role"]) == ("duplicate", "extra")
    assert extra["duplicate_of"] == P1649_B
    assert extra["dup_check"] == "tail"
    assert extra["size_bytes"] == primary["size_bytes"] == len(fixture(F_1649_B))


async def test_sync_duplicate_group_summary_comes_from_the_primary_file_ac14(
    make_host: Any, clean_db: Factory
) -> None:
    make_host(F_1649_A, F_1649_B)

    assert await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649") == 0

    row = await daily_row(clean_db, 1649)
    assert row is not None
    expected = sol_1649_expected()
    assert row["dose_b_mean"] == approx(expected["b_mean"])
    assert row["dose_e_mean"] == approx(expected["e_mean"])
    assert row["live_s"] == approx(expected["live"])
    assert row["bu_count"] == expected["count"]
    # obs 14 carries QC=00000001; obs 17 has a UTC jump of +6 h and is still used.
    assert (row["n_used"], row["n_dropped_qc"]) == (3, 1)
    assert row["source_path"] == P1649_B
    assert row["earth_date"] == date(2017, 3, 26)  # START_TIME of the primary, day 085


async def test_sync_second_run_does_not_repeat_the_probe_ac14(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_1649_A, F_1649_B)
    assert await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649") == 0
    host.requests.clear()

    assert await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649") == 0

    assert host.calls("files") == []


async def test_sync_server_without_range_and_same_length_marks_a_duplicate_by_size_ac15(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    primary = good_file(1649, pad_to=300_000, creation_time="2026-180 17:52:08")
    extra = good_file(1649, pad_to=300_000, creation_time="2026-180 17:30:00")
    assert len(primary) == len(extra)
    path_a = synthetic_path(synthetic_name(1649, hh_mm="09_00"))
    path_b = synthetic_path(synthetic_name(1649, hh_mm="10_00"))
    host = synthetic_host(upstream_router, events, {path_a: extra, path_b: primary})
    host.range_supported = False

    code = await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649")

    assert code == 0
    row = await file_row(clean_db, path_a)
    assert (row["status"], row["dup_check"], row["duplicate_of"]) == ("duplicate", "size", path_b)
    assert host.streamed.get(path_a, 0) < len(extra)  # the body is not read past the first chunk


@pytest.mark.parametrize("range_supported", [True, False], ids=["range-206", "no-range-200"])
@pytest.mark.parametrize("same_creation", [False, True], ids=["later-creation", "equal-creation"])
async def test_sync_different_copies_are_downloaded_and_merged_by_observation_key_ac15(
    upstream_router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    range_supported: bool,
    same_creation: bool,
) -> None:
    """Shared key: the value of the file with the later CREATION_TIME (the greater path on a
    tie); new keys of the other copy are added; both files end up `loaded`."""
    u = "2026-080 1{}:00:00"
    primary = rad_file(
        300,
        [obs_block(0, dose_b=6.0, utc=u.format(0)), obs_block(2, dose_b=6.2, utc=u.format(1))],
        creation_time="2026-180 17:52:08",
        pad_to=80_000,
    )
    extra = rad_file(
        300,
        [obs_block(0, dose_b=9.9, utc=u.format(1)), obs_block(2, dose_b=6.4, utc=u.format(2))],
        creation_time="2026-180 17:52:08" if same_creation else "2026-180 17:00:00",
        pad_to=90_000,
    )
    path_p = synthetic_path(synthetic_name(300, hh_mm="10_00"))
    path_e = synthetic_path(synthetic_name(300, hh_mm="09_00"))
    host = synthetic_host(upstream_router, events, {path_p: primary, path_e: extra})
    host.range_supported = range_supported

    code = await run_rad("sync", "--from-sol", "300", "--to-sol", "300")

    assert code == 0
    assert (await file_row(clean_db, path_p))["status"] == "loaded"
    extra_row = await file_row(clean_db, path_e)
    assert (extra_row["status"], extra_row["role"]) == ("loaded", "extra")
    row = await daily_row(clean_db, 300)
    assert row is not None
    assert row["n_used"] == 3
    assert row["dose_b_max"] == 6.4  # 9.9 of the older copy lost to 6.2 of the later one
    assert row["dose_b_min"] == 6.0
    assert row["dose_b_mean"] == approx((6.0 + 6.2 + 6.4) / 3)
    assert row["source_path"] == path_p


async def test_sync_extra_copy_with_a_different_tail_is_not_a_duplicate_ac14(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    """Same size, different body: the tail comparison must not call it a duplicate."""
    one = good_file(301, dose_b=6.0, pad_to=100_000)
    other = good_file(301, dose_b=7.0, pad_to=100_000, creation_time="2026-180 17:00:00")
    path_a = synthetic_path(synthetic_name(301, hh_mm="09_00"))
    path_b = synthetic_path(synthetic_name(301, hh_mm="10_00"))
    # Make the tails differ: the dose sits at the start, so change one filler byte at the end.
    other = other[:-1] + (b"y" if other[-1:] != b"y" else b"z")
    assert len(one) == len(other) and one[-65536:] != other[-65536:]
    host = synthetic_host(upstream_router, events, {path_a: other, path_b: one})

    assert await run_rad("sync", "--from-sol", "301", "--to-sol", "301") == 0

    assert (await file_row(clean_db, path_a))["status"] == "loaded"
    assert [r.range for r in host.file_calls(path_a)].count(None) >= 1  # downloaded in full


# --- AC-16: changed md5 --------------------------------------------------------------------------


async def test_sync_changed_md5_reprocesses_only_that_sol_ac16(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0
    before_4842 = await daily_row(clean_db, 4842)
    before_4843 = await daily_row(clean_db, 4843)
    assert before_4842 and before_4843
    host.requests.clear()
    new_body = fixture(F_4843).replace(b"6.84128", b"7.84128", 1)
    host.set_file(P4843, new_body)

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 0
    assert [r.path for r in host.calls("files")] == [P4843]
    after_4843 = await daily_row(clean_db, 4843)
    assert after_4843 and after_4843["dose_b_max"] == approx(7.84128)
    assert after_4843["updated_at"] > before_4843["updated_at"]
    assert await daily_row(clean_db, 4842) == before_4842  # the neighbour is untouched
    assert (await file_row(clean_db, P4843))["md5"] == md5(new_body)


async def test_sync_changed_md5_with_the_same_summary_keeps_updated_at_ac16(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    before = await daily_row(clean_db, 4843)
    host.requests.clear()
    host.set_file(P4843, fixture(F_4843) + b"[PHA: 90]\r\n\r\n1 2 3\r\n")  # PHA only

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert [r.path for r in host.calls("files")] == [P4843]
    assert await daily_row(clean_db, 4843) == before  # same values: the row is not rewritten
    assert (await file_row(clean_db, P4843))["md5"] == md5(
        fixture(F_4843) + b"[PHA: 90]\r\n\r\n1 2 3\r\n"
    )


# --- AC-17: md5 mismatch -------------------------------------------------------------------------


async def test_sync_md5_mismatch_gets_one_more_attempt_then_fails_the_file_ac17(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.md5_override[P4843] = "0" * 32

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 1
    assert len(host.file_calls(P4843)) == 2  # the first body and one more full attempt
    row = await file_row(clean_db, P4843)
    assert row["status"] == "md5_mismatch"
    assert row["md5"] is None and row["error"]
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842]  # other sols are loaded


async def test_sync_md5_mismatch_keeps_the_stored_summary_of_the_sol_ac17(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    before = await daily_row(clean_db, 4843)
    host.md5_override[P4843] = "f" * 32
    host.bump(CHECKSUMS)

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 1
    assert await daily_row(clean_db, 4843) == before
    assert (await file_row(clean_db, P4843))["status"] == "md5_mismatch"


async def test_sync_md5_mismatch_is_retried_on_the_next_run_ac17(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.md5_override[P4843] = "0" * 32
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 1
    host.md5_override.clear()
    host.bump(CHECKSUMS)
    host.requests.clear()

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert (await file_row(clean_db, P4843))["status"] == "loaded"
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4843]


async def test_sync_body_corrupted_once_is_fixed_by_the_second_attempt_ac17(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["corrupt"]

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert len(host.file_calls(P4843)) == 2
    assert (await file_row(clean_db, P4843))["status"] == "loaded"


# --- AC-18: broken bodies ------------------------------------------------------------------------


@pytest.mark.parametrize("behaviour", ["truncate", "reset"], ids=["short-body", "connection-reset"])
async def test_sync_broken_body_is_retried_from_the_start_ac18(
    host_4842_4843: RadHost, clean_db: Factory, behaviour: str
) -> None:
    host = host_4842_4843
    host.fail[P4843] = [behaviour]

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert len(host.file_calls(P4843)) == 2  # success on the 2nd attempt
    assert (await file_row(clean_db, P4843))["status"] == "loaded"
    row = await daily_row(clean_db, 4843)
    assert row is not None
    check_4843_summary(row)  # the partial parse of attempt 1 left no trace


async def test_sync_pauses_between_attempts_at_one_file_ac18(
    host_4842_4843: RadHost, fake_sleep: list[float]
) -> None:
    host_4842_4843.fail[P4843] = ["truncate"]

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert any(seconds >= 30 for seconds in fake_sleep)  # the first back-off is 30 s


async def test_sync_three_broken_bodies_in_a_row_use_exactly_the_attempts_ac18(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["truncate", "truncate", "truncate", "truncate", "truncate"]

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code in (1, 3)  # the file is not stored; whether the run goes on is AC-19
    assert len(host.file_calls(P4843)) in (3, 4)
    assert await daily_row(clean_db, 4843) is None


# --- AC-19: server down --------------------------------------------------------------------------


async def test_sync_server_down_waits_the_cooldown_then_stops_with_code_3_ac19(
    host_4842_4843: RadHost, clean_db: Factory, fake_sleep: list[float]
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["503"] * 20

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 3
    assert len(host.file_calls(P4843)) == 4  # 3 attempts, the cool-down, one more
    assert 900 in fake_sleep  # RAD_COOLDOWN_SECONDS
    assert 30 in fake_sleep and 120 in fake_sleep  # the pauses between attempts
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842]  # committed sols stay
    dataset = await rad_dataset(clean_db)
    assert dataset and dataset["last_error"]


async def test_sync_next_run_continues_from_the_failed_sol_ac19(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["503"] * 20
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 3
    host.fail.clear()
    host.requests.clear()

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 0
    assert [r.path for r in host.calls("files")] == [P4843]  # 4842 is skipped
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842, 4843]


async def test_sync_does_not_touch_later_sols_after_the_server_gave_up_ac19(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_SOL10, F_4842, F_4843)
    host.fail[P4842] = ["503"] * 20

    code = await run_rad("sync")

    assert code == 3
    assert host.file_calls(P4843) == []
    assert [r["sol"] for r in await rad_daily(clean_db)] == [10]


async def test_sync_server_error_that_clears_up_after_the_cooldown_goes_on_ac19(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["503"] * 3  # all three attempts fail, the post-cooldown try works

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert len(host.file_calls(P4843)) == 4
    assert (await file_row(clean_db, P4843))["status"] == "loaded"


async def test_sync_429_is_treated_like_a_server_error_ac19(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["429"]

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert len(host.file_calls(P4843)) == 2


async def test_sync_missing_file_404_fails_that_file_and_the_run_goes_on(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4842] = ["404"]

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 1
    assert len(host.file_calls(P4842)) == 1  # a 4xx is not retried
    assert (await file_row(clean_db, P4842))["status"] == "failed"
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4843]


# --- AC-20: interruption -------------------------------------------------------------------------


async def test_sync_restart_after_a_crash_skips_the_committed_sol_ac20(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4843] = ["boom"]
    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843", crash_ok=True)
    assert code != 0
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842]  # sol 1 was committed
    host.requests.clear()

    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    assert [r.path for r in host.calls("files")] == [P4843]
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842, 4843]


async def test_sync_interrupted_sol_leaves_no_partial_rows_ac20(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    host.fail[P4842] = ["boom"]

    await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843", crash_ok=True)

    assert await rad_files(clean_db) == []
    assert await rad_daily(clean_db) == []


# --- AC-21: files that left the list -------------------------------------------------------------


async def test_sync_without_range_deletes_files_that_left_the_list_ac21(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host, paths = twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    assert len(await rad_daily(clean_db)) == 20
    host.remove_file(paths[105])
    host.requests.clear()

    code = await run_rad("sync")

    assert code == 0
    assert host.calls("files") == []
    assert {f["path"] for f in await rad_files(clean_db)} == set(paths.values()) - {paths[105]}
    assert 105 not in {r["sol"] for r in await rad_daily(clean_db)}
    assert len(await rad_daily(clean_db)) == 19


async def test_sync_removed_file_leaves_the_rest_of_its_sol_recalculated_ac21(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host, paths = twenty_sols(upstream_router, events)
    twin = synthetic_path(synthetic_name(105, hh_mm="09_00"))
    host.files[twin] = host.files[paths[105]]  # an identical earlier copy of sol 105
    host.bump(CHECKSUMS)
    assert await run_rad("sync") == 0
    host.remove_file(paths[105])  # the primary file goes, the earlier copy stays

    assert await run_rad("sync") == 0

    assert (await file_row(clean_db, twin))["status"] == "loaded"
    assert (await file_row(clean_db, twin))["role"] == "primary"
    row = await daily_row(clean_db, 105)
    assert row is not None and row["source_path"] == twin
    assert paths[105] not in {f["path"] for f in await rad_files(clean_db)}


async def test_sync_list_that_lost_too_many_files_deletes_nothing_ac21(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host, paths = twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    for sol in range(100, 105):  # 5 of 20 gone: 15/20 = 0.75 < RAD_MIN_KEEP_RATIO (0.9)
        host.remove_file(paths[sol])

    code = await run_rad("sync")

    assert code == 1
    assert len(await rad_files(clean_db)) == 20
    assert len(await rad_daily(clean_db)) == 20
    dataset = await rad_dataset(clean_db)
    assert dataset and dataset["last_error"]


async def test_sync_with_a_range_never_deletes_ac21(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host, paths = twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    host.remove_file(paths[105])

    assert await run_rad("sync", "--from-sol", "100", "--to-sol", "119") == 0

    assert len(await rad_files(clean_db)) == 20
    assert len(await rad_daily(clean_db)) == 20


async def test_sync_keep_ratio_setting_is_honoured_ac21(
    upstream_router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    rad_env: Any,
) -> None:
    host, paths = twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    host.remove_file(paths[105])
    rad_env(RAD_MIN_KEEP_RATIO="1.0")  # 19/20 < 1.0: nothing may go

    code = await run_rad("sync")

    assert code == 1
    assert len(await rad_files(clean_db)) == 20


# --- AC-22: nothing usable -----------------------------------------------------------------------


async def test_sync_sol_without_a_usable_observation_loses_its_row_and_file_is_empty_ac22(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    host = host_4842_4843
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    assert await daily_row(clean_db, 4843) is not None
    host.set_file(P4843, rad_file(4843, [obs_block(0, qc="00000001"), obs_block(2, live=0)]))

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert await daily_row(clean_db, 4843) is None
    assert (await file_row(clean_db, P4843))["status"] == "empty"


async def test_sync_file_without_dose_blocks_is_empty_ac22(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    from tests._rad import twin_block

    host_4842_4843.set_file(P4843, rad_file(4843, [twin_block(0), twin_block(1)]))

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert (await file_row(clean_db, P4843))["status"] == "empty"
    assert await rad_daily(clean_db) == []


# --- AC-23: timing caveat ------------------------------------------------------------------------


async def test_sync_timing_caveat_follows_the_error_list_ac23(
    make_host: Any, clean_db: Factory
) -> None:
    make_host(F_SOL10, F_4843)

    assert await run_rad("sync", "--from-sol", "10", "--to-sol", "4843") == 0

    assert (await daily_row(clean_db, 10))["timing_caveat"] is True  # type: ignore[index]
    assert (await daily_row(clean_db, 4843))["timing_caveat"] is False  # type: ignore[index]
    assert (await file_row(clean_db, P10))["timing_listed"] is True
    assert (await file_row(clean_db, P4843))["timing_listed"] is False


async def test_sync_without_the_error_list_keeps_old_flags_and_leaves_new_unknown_ac23(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_SOL10, F_4843)
    assert await run_rad("sync", "--from-sol", "10", "--to-sol", "4843") == 0
    host.fail[TIMING] = ["404"] * 10
    host.bump(TIMING)
    host.set_file(P10, fixture(F_SOL10).replace(b"8.40398", b"8.50398", 1))  # sol 10 is re-read
    host.set_file(P4842, fixture(F_4842))  # a new sol

    code = await run_rad("sync", "--from-sol", "10", "--to-sol", "4843")

    assert code == 0
    assert (await daily_row(clean_db, 10))["timing_caveat"] is True  # type: ignore[index]
    assert (await daily_row(clean_db, 4843))["timing_caveat"] is False  # type: ignore[index]
    assert (await daily_row(clean_db, 4842))["timing_caveat"] is None  # type: ignore[index]
    dataset = await rad_dataset(clean_db)
    assert dataset and dataset["last_error"]  # the warning


async def test_sync_error_list_change_updates_the_flag_of_a_known_sol_ac23(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_4843)
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    host.timing += (
        b"***** Timing Error *****\r\n     Time found running backward\r\n"
        + f"     Found in File: {F_4843}\r\n     Observation: 3\r\n".encode()
    )
    host.bump(TIMING)

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert (await daily_row(clean_db, 4843))["timing_caveat"] is True  # type: ignore[index]


# --- AC-11: date of the sol ----------------------------------------------------------------------


async def test_sync_utc_jump_of_a_year_changes_neither_sol_nor_date_ac11(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    body = rad_file(
        500,
        [
            obs_block(0, utc="2026-080 17:18:00"),
            obs_block(2, utc="2027-080 17:35:06"),  # jump of one year
            obs_block(4, utc="2026-080 17:52:00"),
        ],
        start_time="2026-080 17:18:00",
    )
    path = synthetic_path(synthetic_name(500))
    synthetic_host(upstream_router, events, {path: body})

    assert await run_rad("sync", "--from-sol", "500", "--to-sol", "500") == 0

    row = await daily_row(clean_db, 500)
    assert row is not None
    assert row["earth_date"] == date(2026, 3, 21)
    assert row["n_used"] == 3  # the observation with the bad UTC is still counted


async def test_sync_header_time_far_from_the_observations_uses_the_earliest_near_the_median_ac11(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    body = rad_file(
        501,
        [
            obs_block(0, utc="2011-001 00:00:00"),  # garbage, far before the others
            obs_block(2, utc="2012-228 20:41:19"),
            obs_block(4, utc="2012-228 21:43:05"),
            obs_block(6, utc="2012-229 00:48:21"),
        ],
        start_time="2013-123 21:52:00",  # START_TIME is garbage too
    )
    path = synthetic_path(synthetic_name(501))
    synthetic_host(upstream_router, events, {path: body})

    assert await run_rad("sync", "--from-sol", "501", "--to-sol", "501") == 0

    row = await daily_row(clean_db, 501)
    assert row is not None
    assert row["earth_date"] == date(2012, 8, 15)  # 2012-228 20:41:19, within 2 days of median


async def test_sync_header_time_close_to_the_median_is_used_as_it_is_ac11(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    body = rad_file(
        502,
        [obs_block(0, utc="2026-081 01:00:00"), obs_block(2, utc="2026-081 02:00:00")],
        start_time="2026-080 23:30:00",  # day before the observations, still within 2 days
    )
    path = synthetic_path(synthetic_name(502))
    synthetic_host(upstream_router, events, {path: body})

    assert await run_rad("sync", "--from-sol", "502", "--to-sol", "502") == 0

    row = await daily_row(clean_db, 502)
    assert row is not None and row["earth_date"] == date(2026, 3, 21)  # day 080, not 081


# --- format problems and limits (AC-8) -----------------------------------------------------------


async def test_sync_start_sol_different_from_the_name_fails_the_file_ac8(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    path = synthetic_path(synthetic_name(600))
    synthetic_host(
        upstream_router,
        events,
        {path: rad_file(601, [obs_block(0)], start_time="2026-080 17:18:00")},
    )

    code = await run_rad("sync", "--from-sol", "600", "--to-sol", "600")

    assert code == 1
    assert (await file_row(clean_db, path))["status"] == "failed"
    assert await rad_daily(clean_db) == []


async def test_sync_file_over_the_size_limit_fails_and_keeps_the_stored_row_ac8(
    host_4842_4843: RadHost, clean_db: Factory, rad_env: Any
) -> None:
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    before = await daily_row(clean_db, 4843)
    host_4842_4843.set_file(P4843, good_file(4843, dose_b=9.0, pad_to=200_000))
    rad_env(RAD_MAX_FILE_BYTES="100000")

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 1
    assert (await file_row(clean_db, P4843))["status"] == "failed"
    assert await daily_row(clean_db, 4843) == before


async def test_sync_line_over_the_line_limit_fails_the_file_ac8(
    upstream_router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    rad_env: Any,
) -> None:
    long_line = b"[PHA: 99]\r\n" + b"1 " * 3000 + b"\r\n"
    path = synthetic_path(synthetic_name(602))
    synthetic_host(upstream_router, events, {path: good_file(602) + long_line})
    rad_env(RAD_MAX_LINE_BYTES="4096")

    code = await run_rad("sync", "--from-sol", "602", "--to-sol", "602")

    assert code == 1
    assert (await file_row(clean_db, path))["status"] == "failed"
    assert await rad_daily(clean_db) == []


async def test_sync_empty_list_loads_nothing_and_deletes_nothing(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    host = synthetic_host(upstream_router, events, {})

    code = await run_rad("sync")

    assert code in (0, 1)
    assert host.calls("files") == []
    assert await rad_daily(clean_db) == []


async def test_sync_list_with_only_unrelated_entries_has_no_sols(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host()
    host.extra_checksum_lines = [
        "af10ea76e30a7ae0149ca852217f73b3  ./AAREADME.TXT",
        "7a3d810511291f6585fb6f1b3273d526  ./DATA/CRUISE/D_LETCNT.FMT",
        "broken line",
    ]

    code = await run_rad("sync")

    assert code in (0, 1)
    assert host.calls("files") == []


async def test_sync_link_of_a_stored_file_points_to_the_archive_ac35(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """The path stored in `rad_daily.source_path` is a volume path, never a URL."""
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert row["source_path"].startswith("DATA/SOL_")
    assert link_of(row["source_path"]).startswith("https://pds-ppi.igpp.ucla.edu/data/")


# --- AC-25: only the configured host -------------------------------------------------------------


async def test_sync_redirect_to_another_host_fails_the_file_without_a_request_there_ac25(
    host_4842_4843: RadHost, upstream_router: respx.MockRouter, clean_db: Factory
) -> None:
    evil = upstream_router.route(host="evil.example.test").respond(200, content=b"evil")
    host_4842_4843.fail[P4843] = ["redirect"] * 10

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 1
    assert evil.call_count == 0
    assert (await file_row(clean_db, P4843))["status"] == "failed"
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842]


async def test_sync_redirect_of_the_list_is_refused_ac25(
    host_4842_4843: RadHost, upstream_router: respx.MockRouter, clean_db: Factory
) -> None:
    evil = upstream_router.route(host="evil.example.test").respond(200, content=b"evil")
    host_4842_4843.fail[CHECKSUMS] = ["redirect"] * 10

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code != 0
    assert evil.call_count == 0
    assert await rad_daily(clean_db) == []


# --- AC-26: nothing is written to disk -----------------------------------------------------------


async def test_sync_writes_no_files_and_never_calls_download_ac26(
    host_4842_4843: RadHost,
    monkeypatch: pytest.MonkeyPatch,
    clean_db: Factory,
    tmp_path: Any,
) -> None:
    opened: list[tuple[Any, str]] = []
    real_open = builtins.open

    def guard_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in str(mode) for flag in "wax+"):
            opened.append((file, mode))
            raise AssertionError(f"file opened for writing: {file!r} mode {mode!r}")
        return real_open(file, mode, *args, **kwargs)

    async def no_download(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("UpstreamClient.download must not be used for RAD files")

    monkeypatch.setattr(UpstreamClient, "download", no_download)
    monkeypatch.setattr(builtins, "open", guard_open)
    monkeypatch.setattr(io, "open", guard_open)

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 0
    assert opened == []
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842, 4843]
