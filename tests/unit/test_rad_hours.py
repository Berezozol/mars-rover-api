"""Hours of a RAD sol: `combine_hours` and the pieces around it (stage 22, phase C).

Spec: docs/proposals/sol-conditions.md, AC-35 ... AC-37, AC-56, AC-57 (AC-58 is in
test_rad_combine.py, AC-34 in test_mars_time_lmst.py). `combine_hours(used, file_sol)` takes the
observations that `combine_sol` counted and returns `HourlyResult(hours, dropped, reject)`; an
hour is `HourSummary(hour, n_obs, live_s, dose_b_mean, dose_e_mean, bu_count)`. The hour is the
LMST hour of the middle of the observation (`START_OBS_UTC + d / 2`), computed from UTC; the
file's `START_OBS_MARS` label is never used.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services import rad_sync
from app.services.rad_sync import combine_hours, combine_sol
from app.sources.rad import RadObservation, RadParser
from tests._rad import (
    F_1649_B,
    F_4843,
    F_SOL10,
    attr,
    fixture,
    obs_block,
    rad_file,
    utc_text,
)
from tests.unit.test_rad_parser import counters

pytestmark = pytest.mark.unit

# Start of local sol 4844 (LMST 0:00:00) on 2026-03-22, from the Mars24 model of the spec.
MIDNIGHT_4844 = datetime(2026, 3, 22, 17, 51, 23, 436019, tzinfo=UTC)
SOL_S = 88_775.244


def at(*args: int, micro: int = 0) -> datetime:
    return datetime(*args, microsecond=micro, tzinfo=UTC)


def make_obs(
    index: int,
    start: datetime | None,
    *,
    d: float | None = 961,
    live: float = 930.0,
    dose_b: float = 6.0,
    dose_e: float = 5.5,
    bu: int = 7200,
    qc: str = "00000000",
) -> Any:
    """A synthetic observation; `d=None` leaves `obs_duration_s` unset (the early format)."""
    kwargs: dict[str, Any] = {
        "index": index,
        "start_utc_raw": utc_text(start) if start else None,
        "start_utc": start,
        "qc_raw": qc,
        "dose_b": dose_b,
        "dose_e": dose_e,
        "live_s": live,
        "bu_count": bu,
        "bu_read": bu,
    }
    if d is not None:
        kwargs["obs_duration_s"] = d
    return RadObservation(**kwargs)


def parse(data: bytes) -> list[Any]:
    parser = RadParser()
    parser.feed(data)
    return list(attr(parser.result(), "observations"))


def pick(observations: list[Any], *indexes: int) -> list[Any]:
    by_index = {attr(o, "index"): o for o in observations}
    return [by_index[i] for i in indexes]


def hours_of(result: Any) -> list[Any]:
    return list(attr(result, "hours"))


def shape(result: Any) -> tuple[Any, ...]:
    """The comparable content of a `HourlyResult`."""
    return (
        tuple(
            (
                attr(h, "hour"),
                attr(h, "n_obs"),
                attr(h, "live_s"),
                attr(h, "dose_b_mean"),
                attr(h, "dose_e_mean"),
                attr(h, "bu_count"),
            )
            for h in hours_of(result)
        ),
        attr(result, "dropped"),
        attr(result, "reject"),
    )


def hour_numbers(result: Any) -> list[int]:
    return [attr(h, "hour") for h in hours_of(result)]


def in_sol_batch(*, n_in: int, n_out: int = 0, d: float = 961) -> list[Any]:
    """`n_in` observations inside sol 4843 and `n_out` a sol earlier (sol 4842), no overlaps."""
    base = at(2026, 3, 21, 21, 0, 0)
    inside = [make_obs(2 * i, base + timedelta(seconds=1200 * i), d=d) for i in range(n_in)]
    outside = [
        make_obs(100 + 2 * i, base + timedelta(seconds=1200 * i - SOL_S), d=d) for i in range(n_out)
    ]
    return inside + outside


# --- constants and names of the contract ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CURIOSITY_LON_E", 137.4),
        ("CURIOSITY_SOL0_MSD", 49269),
        ("OVERLAP_TOLERANCE_S", 60.0),
        ("MIN_ON_SOL_SHARE", 0.5),
        ("MIN_DOSE_UGY_H", 1.0),
        ("HOUR_LIVE_S", 3698.97),
        ("RAD_HOURLY_REVISION", 1),
    ],
)
def test_rad_sync_constants_have_the_values_of_the_spec(name: str, value: float) -> None:
    assert getattr(rad_sync, name) == value


def test_rad_transfer_version_and_schema_revision_are_those_of_stage_22_ac46() -> None:
    """AC-46: the export format is version 2, the schema revision 0021."""
    from app.services import rad_transfer

    assert rad_transfer.EXPORT_VERSION == 2
    assert rad_transfer.SCHEMA_REVISION == "0021"


# --- parser: OBS_DURATION, never START_OBS_MARS (AC-35, AC-36) -----------------------------------


def test_parser_reads_obs_duration_of_recent_files_ac35() -> None:
    observations = parse(fixture(F_4843))

    assert {attr(o, "index"): attr(o, "obs_duration_s") for o in observations} == {
        0: 961,
        2: 961,
        22: 272,
        174: 961,
    }


def test_parser_early_format_has_no_obs_duration_ac35() -> None:
    """Sol 10 has no `OBS_DURATION`: the field stays unset and `live_s` is the fallback."""
    observations = parse(fixture(F_SOL10))

    assert [attr(o, "obs_duration_s") for o in observations] == [None] * 4
    assert {attr(o, "live_s") for o in observations} == {960}


def test_parser_sol_1649_obs_duration_per_observation_ac35() -> None:
    observations = parse(fixture(F_1649_B))

    assert {attr(o, "index"): attr(o, "obs_duration_s") for o in observations} == {
        1: 961,
        3: 961,
        14: 358,
        17: 961,
    }


# --- AC-35: the hour of an observation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "hour"),
    [
        (at(2026, 3, 22, 7, 26, 22), 13),  # middle 07:34:22.5Z -> LMST 13:59:30
        (at(2026, 3, 22, 7, 27, 24), 14),  # middle 07:35:24.5Z -> LMST 14:00:30
    ],
    ids=["13-59-30", "14-00-30"],
)
def test_combine_hours_takes_the_hour_of_the_middle_with_obs_duration_ac35(
    start: datetime, hour: int
) -> None:
    """AC-35: sol 4843, d = 961; LMST of the middle 13:59:30 -> hour 13, 14:00:30 -> hour 14."""
    result = combine_hours([make_obs(0, start, d=961)], 4843)

    assert hour_numbers(result) == [hour]
    assert (attr(result, "dropped"), attr(result, "reject")) == (0, None)


def test_combine_hours_without_obs_duration_the_live_time_is_the_length_ac35() -> None:
    """The same start, once with d = 961 (hour 14) and once without d, live 930 s (hour 13)."""
    start = at(2026, 3, 22, 7, 27, 4)  # middle at +480.5 s -> 14:00:09, at +465 s -> 13:59:53

    with_d = combine_hours([make_obs(0, start, d=961, live=930)], 4843)
    without_d = combine_hours([make_obs(0, start, d=None, live=930)], 4843)

    assert hour_numbers(with_d) == [14]
    assert hour_numbers(without_d) == [13]


def test_combine_hours_fixture_4843_observations_0_and_2_are_in_hour_0_ac35() -> None:
    """AC-35: the middles are LMST 00:13:50 and 00:30:28 -> both hour 0, one HourSummary."""
    observations = parse(fixture(F_4843))

    result = combine_hours(pick(observations, 0, 2), 4843)

    assert hour_numbers(result) == [0]
    hour = hours_of(result)[0]
    assert attr(hour, "n_obs") == 2
    assert attr(hour, "live_s") == pytest.approx(1860.0)
    assert attr(hour, "bu_count") == 6454 + 6487


def test_combine_hours_fixture_sol_10_observation_0_is_hour_0_and_25_is_hour_23_ac35() -> None:
    """AC-35: no OBS_DURATION (d from live_s); obs 12 (UTC 2013-123) is dropped, see AC-36."""
    observations = parse(fixture(F_SOL10))

    result = combine_hours(pick(observations, 0, 12, 25), 10)

    assert hour_numbers(result) == [0, 23]
    assert [attr(h, "n_obs") for h in hours_of(result)] == [1, 1]
    assert [attr(h, "bu_count") for h in hours_of(result)] == [7817, 7707]


def test_combine_hours_hours_come_sorted_and_only_hours_with_observations_ac35() -> None:
    """Sparse: no rows for empty hours; ascending by hour whatever the input order."""
    base = at(2026, 3, 21, 17, 20, 0)
    spread = [make_obs(i, base + timedelta(hours=h)) for i, h in enumerate((5, 1, 9, 3))]

    result = combine_hours(spread, 4843)

    assert hour_numbers(result) == sorted(hour_numbers(result))
    assert len(hours_of(result)) == 4
    assert all(0 <= h <= 23 for h in hour_numbers(result))


def test_combine_hours_single_observation_sol_is_accepted_ac35() -> None:
    """Edge: a sol with one observation (1 of 1 on the sol) is a valid sol with one hour."""
    result = combine_hours([make_obs(0, at(2026, 3, 21, 17, 20, 0))], 4843)

    assert shape(result)[1:] == (0, None)
    assert hour_numbers(result) == [0]


def test_combine_hours_local_midnight_belongs_to_the_new_sol_and_hour_0_ac35() -> None:
    """Edge: a middle just after LMST 00:00:00 is sol 4844, hour 0; just before is 4843, hour 23.

    Two ordinary observations make the sol valid; the one at the boundary is the test subject.
    """
    after_mid = MIDNIGHT_4844 + timedelta(seconds=0.05)
    before_mid = MIDNIGHT_4844 - timedelta(seconds=0.05)
    sol_4844 = [
        make_obs(2, MIDNIGHT_4844 + timedelta(hours=3), d=960),
        make_obs(4, MIDNIGHT_4844 + timedelta(hours=6), d=960),
    ]
    sol_4843 = [
        make_obs(2, MIDNIGHT_4844 - timedelta(hours=3), d=960),
        make_obs(4, MIDNIGHT_4844 - timedelta(hours=6), d=960),
    ]

    new_sol = combine_hours(
        [make_obs(0, after_mid - timedelta(seconds=480), d=960), *sol_4844], 4844
    )
    old_sol = combine_hours(
        [make_obs(0, before_mid - timedelta(seconds=480), d=960), *sol_4843], 4843
    )
    old_file_sees_it_as_foreign = combine_hours(
        [make_obs(0, after_mid - timedelta(seconds=480), d=960), *sol_4843], 4843
    )

    assert hour_numbers(new_sol)[0] == 0 and (
        attr(new_sol, "dropped"),
        attr(new_sol, "reject"),
    ) == (
        0,
        None,
    )
    assert hour_numbers(old_sol)[-1] == 23 and attr(old_sol, "dropped") == 0
    assert attr(old_file_sees_it_as_foreign, "dropped") == 1
    assert attr(old_file_sees_it_as_foreign, "reject") is None


# --- AC-36: observations that do not belong to the sol -------------------------------------------


def test_combine_hours_fixture_4843_observation_174_is_dropped_but_stays_in_the_day_ac36() -> None:
    """AC-36: obs 174 (middle 2026-03-22 17:54:10.5Z = sol 4844, 00:02:43) is not in the hours."""
    observations = parse(fixture(F_4843))

    summary = combine_sol(observations, 0.5)
    result = combine_hours(pick(observations, 0, 2, 174), 4843)

    assert counters(summary) == (3, 1, 0, 0)  # the day still counts it
    assert attr(result, "dropped") == 1
    assert attr(result, "reject") is None
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 2


def test_combine_hours_fixture_sol_10_observation_12_with_garbage_utc_is_dropped_ac36() -> None:
    """AC-36: obs 12 (`2013-123`) is sol 264 by UTC: dropped; sol 10 is still accepted (2 of 3)."""
    result = combine_hours(pick(parse(fixture(F_SOL10)), 0, 12, 25), 10)

    assert attr(result, "dropped") == 1
    assert attr(result, "reject") is None
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 2


def test_combine_hours_unreadable_start_utc_is_dropped_ac36() -> None:
    """AC-36: `start_utc = None` -> not in the hours, counted in `dropped`."""
    ok = in_sol_batch(n_in=3)
    result = combine_hours([*ok, make_obs(9, None)], 4843)

    assert attr(result, "dropped") == 1
    assert attr(result, "reject") is None
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 3


def test_combine_hours_garbage_utc_text_from_a_file_is_dropped_ac36() -> None:
    """The same through the parser: a block with `START_OBS_UTC="garbage"`."""
    blocks = [
        obs_block(0, utc="2026-080 21:00:00"),
        obs_block(2, utc="2026-080 21:20:00"),
        obs_block(4, utc="2026-080 21:40:00"),
        obs_block(6, utc="garbage"),
    ]
    observations = parse(rad_file(4843, blocks, start_time="2026-080 21:00:00"))
    assert attr(observations[3], "start_utc") is None

    result = combine_hours(observations, 4843)

    assert attr(result, "dropped") == 1
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 3


@pytest.mark.parametrize("label", ["N/A", "zzz garbage", "4843 99:99:99", "1 0:00:00"])
def test_combine_hours_ignores_the_start_obs_mars_label_ac36(label: str) -> None:
    """AC-36: replacing every `START_OBS_MARS` label changes nothing in the hours."""
    data = fixture(F_4843)
    changed = re.sub(rb'START_OBS_MARS="[^"]*"', b'START_OBS_MARS="' + label.encode() + b'"', data)
    assert changed != data

    original = combine_hours(pick(parse(data), 0, 2, 174), 4843)
    relabelled = combine_hours(pick(parse(changed), 0, 2, 174), 4843)

    assert shape(relabelled) == shape(original)
    assert hour_numbers(original) == [0]


def test_combine_hours_all_observations_from_another_sol_reject_the_sol_ac36() -> None:
    """Nothing of the file is on its sol: that is `off_sol` (AC-57), not an empty sol."""
    result = combine_hours(in_sol_batch(n_in=3), 4000)

    assert attr(result, "reject") == "off_sol"
    assert hours_of(result) == []
    assert attr(result, "dropped") == 3


# --- AC-37: what the hours contain ---------------------------------------------------------------


def test_combine_hours_hour_dose_is_weighted_by_the_live_time_ac37() -> None:
    """AC-37: (B 6.0 x 900 s + B 8.0 x 300 s) / 1200 s = 6.5; E (4.0 x 900 + 8.0 x 300) / 1200."""
    first = make_obs(0, at(2026, 3, 21, 17, 20, 0), live=900, dose_b=6.0, dose_e=4.0, bu=7200)
    second = make_obs(
        2, at(2026, 3, 21, 17, 40, 0), d=400, live=300, dose_b=8.0, dose_e=8.0, bu=2700
    )

    result = combine_hours([second, first], 4843)

    (hour,) = hours_of(result)
    assert attr(hour, "hour") == 0
    assert attr(hour, "n_obs") == 2
    assert attr(hour, "live_s") == pytest.approx(1200.0)
    assert attr(hour, "dose_b_mean") == pytest.approx(6.5)
    assert attr(hour, "dose_e_mean") == pytest.approx((4.0 * 900 + 8.0 * 300) / 1200)
    assert attr(hour, "bu_count") == 9900


def test_combine_hours_hour_with_one_observation_keeps_its_values_ac37() -> None:
    result = combine_hours(
        [make_obs(0, at(2026, 3, 21, 17, 20, 0), live=900, dose_b=7.25, dose_e=6.5, bu=7000)],
        4843,
    )

    (hour,) = hours_of(result)
    assert (attr(hour, "n_obs"), attr(hour, "live_s")) == (1, 900.0)
    assert attr(hour, "dose_b_mean") == pytest.approx(7.25)
    assert attr(hour, "dose_e_mean") == pytest.approx(6.5)
    assert attr(hour, "bu_count") == 7000


def test_combine_hours_fixture_4843_hour_values_ac37() -> None:
    """Obs 0 and 2 (930 s each): the means are plain averages."""
    observations = parse(fixture(F_4843))

    result = combine_hours(pick(observations, 0, 2), 4843)

    (hour,) = hours_of(result)
    assert attr(hour, "dose_b_mean") == pytest.approx((6.84128 + 6.78671) / 2)
    assert attr(hour, "dose_e_mean") == pytest.approx((6.58689 + 6.53178) / 2)


@pytest.mark.parametrize(
    ("name", "file_sol", "used_indexes"),
    [
        (F_4843, 4843, (0, 2, 174)),  # obs 22 has QC bit 0: not used
        (F_SOL10, 10, (0, 12, 25)),  # obs 13 is dropped by the low count rate: not used
    ],
    ids=["sol-4843", "sol-10"],
)
def test_combine_hours_counts_add_up_to_the_day_ac37(
    name: str, file_sol: int, used_indexes: tuple[int, ...]
) -> None:
    """AC-37: sum(n_obs of the hours) + dropped = n_used; sum(live_s of the hours) <= live_s."""
    observations = parse(fixture(name))
    summary = combine_sol(observations, 0.5)
    assert summary is not None
    assert attr(summary, "used", "n_used") == len(used_indexes)  # the right ones are picked

    result = combine_hours(pick(observations, *used_indexes), file_sol)

    assert attr(result, "reject") is None
    in_hours = sum(attr(h, "n_obs") for h in hours_of(result))
    assert in_hours + attr(result, "dropped") == attr(summary, "used", "n_used")
    assert sum(attr(h, "live_s") for h in hours_of(result)) <= attr(summary, "live_s") + 1e-9
    assert sum(attr(h, "bu_count") for h in hours_of(result)) <= attr(summary, "bu_count")


def test_combine_hours_do_not_know_the_observations_the_day_dropped_ac37() -> None:
    """AC-37: `combine_hours` works on what it is given; the day's filters stay in `combine_sol`.

    QC, low rate and implausible dose are removed before: only `used` reaches the hours, so the
    hours of a sol never contain an observation that the day rejected.
    """
    good = in_sol_batch(n_in=3)
    bad_qc = make_obs(20, at(2026, 3, 21, 19, 0, 0), qc="00000001", dose_b=0.5)
    bad_dose = make_obs(22, at(2026, 3, 21, 19, 20, 0), dose_b=0.5, dose_e=0.4)
    day = combine_sol([*good, bad_qc, bad_dose], 0.5)
    assert day is not None
    assert counters(day) == (3, 1, 0, 1)

    result = combine_hours(good, 4843)

    assert sum(attr(h, "n_obs") for h in hours_of(result)) + attr(result, "dropped") == 3


# --- AC-56: overlapping observations -------------------------------------------------------------


def two_obs(overlap_s: float, *, d: float | None = 961, live: float = 930.0) -> list[Any]:
    """Two observations whose second one starts `overlap_s` seconds before the first one ends."""
    first = at(2026, 3, 21, 21, 0, 0)
    length = d if d is not None else live
    second = first + timedelta(seconds=length - overlap_s)
    return [make_obs(0, first, d=d, live=live), make_obs(2, second, d=d, live=live)]


@pytest.mark.parametrize("overlap_s", [61.0, 60.5, 120.0, 961.0], ids=["61", "60.5", "120", "same"])
def test_combine_hours_overlap_above_the_tolerance_rejects_the_whole_sol_ac56(
    overlap_s: float,
) -> None:
    """AC-56: the second starts more than 60 s before the first ends -> `overlap`, no hours."""
    used = two_obs(overlap_s)

    result = combine_hours(used, 4843)

    assert attr(result, "reject") == "overlap"
    assert hours_of(result) == []
    assert attr(result, "dropped") == len(used)


@pytest.mark.parametrize(
    "overlap_s",
    [60.0, 59.0, 30.0, 0.0, -47.0, -300.0],
    ids=["60", "59", "30", "touch", "gap47", "gap300"],
)
def test_combine_hours_overlap_of_at_most_60_s_or_a_gap_is_accepted_ac56(
    overlap_s: float,
) -> None:
    """AC-56: exactly 60 s of overlap or a gap -> the sol is accepted."""
    result = combine_hours(two_obs(overlap_s), 4843)

    assert attr(result, "reject") is None
    assert attr(result, "dropped") == 0
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 2


def test_combine_hours_overlap_is_found_whatever_the_input_order_ac56() -> None:
    """AC-56: the observations are sorted by their start before the pairs are compared."""
    used = two_obs(120.0)

    assert attr(combine_hours(list(reversed(used)), 4843), "reject") == "overlap"


def test_combine_hours_one_overlapping_pair_among_good_ones_rejects_the_sol_ac56() -> None:
    base = at(2026, 3, 21, 20, 0, 0)
    used = [
        make_obs(0, base),
        make_obs(2, base + timedelta(seconds=1008)),
        make_obs(4, base + timedelta(seconds=2016)),
        make_obs(6, base + timedelta(seconds=2016 + 961 - 200)),  # 200 s over the previous one
        make_obs(8, base + timedelta(seconds=5000)),
    ]

    result = combine_hours(used, 4843)

    assert attr(result, "reject") == "overlap"
    assert hours_of(result) == []
    assert attr(result, "dropped") == 5


def test_combine_hours_the_overlap_is_measured_with_d_not_with_the_live_time_ac56() -> None:
    """d = 961 s, live 930 s, the second starts 900 s after the first: 61 s over d (reject),
    only 30 s over the live time."""
    first = at(2026, 3, 21, 21, 0, 0)
    used = [make_obs(0, first), make_obs(2, first + timedelta(seconds=900))]

    assert attr(combine_hours(used, 4843), "reject") == "overlap"


def test_combine_hours_without_obs_duration_the_overlap_uses_the_live_time_ac56() -> None:
    """The early format: d is the live time (930 s here)."""
    assert attr(combine_hours(two_obs(61.0, d=None, live=930.0), 4843), "reject") == "overlap"
    assert attr(combine_hours(two_obs(60.0, d=None, live=930.0), 4843), "reject") is None


def test_combine_hours_regular_spacing_of_the_fixtures_is_not_an_overlap_ac56() -> None:
    """Real data: the shortest step is about 1008 s with d = 961 s (a gap of 47 s)."""
    base = at(2026, 3, 21, 18, 0, 0)
    used = [make_obs(i, base + timedelta(seconds=1008 * i)) for i in range(20)]

    result = combine_hours(used, 4843)

    assert attr(result, "reject") is None
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 20


# --- AC-57: too few observations on the sol ------------------------------------------------------


@pytest.mark.parametrize(
    ("n_in", "n_out", "reject"),
    [
        (1, 2, "off_sol"),  # 1 of 3
        (0, 1, "off_sol"),  # 0 of 1
        (1, 3, "off_sol"),  # 1 of 4
        (1, 1, None),  # exactly half
        (2, 2, None),  # exactly half
        (2, 1, None),  # 2 of 3
        (5, 0, None),
    ],
    ids=["1-of-3", "0-of-1", "1-of-4", "half-1-of-2", "half-2-of-4", "2-of-3", "all"],
)
def test_combine_hours_less_than_half_on_the_sol_rejects_it_ac57(
    n_in: int, n_out: int, reject: str | None
) -> None:
    """AC-57: fewer than half of the used observations on the sol by UTC -> `off_sol`."""
    used = in_sol_batch(n_in=n_in, n_out=n_out)

    result = combine_hours(used, 4843)

    assert attr(result, "reject") == reject
    if reject:
        assert hours_of(result) == []
        assert attr(result, "dropped") == len(used)
    else:
        assert attr(result, "dropped") == n_out
        assert sum(attr(h, "n_obs") for h in hours_of(result)) == n_in


def test_combine_hours_fixture_1649_is_rejected_off_sol_ac57() -> None:
    """AC-57: used 1, 3, 17; 1 and 3 are sol 1648 by UTC (18:05 and 18:21) -> 1 of 3."""
    observations = parse(fixture(F_1649_B))
    summary = combine_sol(observations, 0.5)
    assert summary is not None and attr(summary, "used", "n_used") == 3

    result = combine_hours(pick(observations, 1, 3, 17), 1649)

    assert attr(result, "reject") == "off_sol"
    assert hours_of(result) == []
    assert attr(result, "dropped") == 3


def test_combine_hours_fixture_4843_two_of_three_is_accepted_ac57() -> None:
    """AC-57: obs 0 and 2 are on sol 4843, obs 174 is not -> 2 of 3, accepted."""
    result = combine_hours(pick(parse(fixture(F_4843)), 0, 2, 174), 4843)

    assert attr(result, "reject") is None
    assert hour_numbers(result) == [0]


def test_combine_hours_the_file_sol_decides_what_is_foreign_ac57() -> None:
    """The same observations are accepted for the sol they are in and rejected for another."""
    used = in_sol_batch(n_in=3)

    assert attr(combine_hours(used, 4843), "reject") is None
    assert attr(combine_hours(used, 4844), "reject") == "off_sol"
    assert attr(combine_hours(used, 4842), "reject") == "off_sol"


@pytest.mark.parametrize(
    ("name", "file_sol", "used_indexes"),
    [(F_4843, 4843, [0, 2, 174]), (F_SOL10, 10, [0, 12, 25]), (F_1649_B, 1649, [1, 3, 17])],
    ids=["sol-4843", "sol-10", "sol-1649"],
)
def test_summary_used_observations_feed_combine_hours_ac37(
    name: str, file_sol: int, used_indexes: list[int]
) -> None:
    """AC-37: `SolSummary.used_observations` are exactly the observations the day counted
    (QC, low rate and implausible dose are out); the hours are built from them."""
    summary = combine_sol(parse(fixture(name)), 0.5)
    assert summary is not None

    used = list(summary.used_observations)

    assert sorted(attr(o, "index") for o in used) == used_indexes
    result = combine_hours(used, file_sol)
    in_hours = sum(attr(h, "n_obs") for h in hours_of(result))
    assert in_hours + attr(result, "dropped") == attr(summary, "used", "n_used")


# --- review round: overflow, bounds, exact midnight ---


def test_parser_obs_duration_limit_constant_is_4_hours() -> None:
    from app.sources import rad

    assert rad.MAX_OBS_DURATION_S == 14400.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1e15", None), ("14401", None), ("14400", 14400), ("0", None), ("-5", None), ("nan", None)],
    ids=["1e15", "over-limit", "at-limit", "zero", "negative", "nan"],
)
def test_parser_implausible_obs_duration_is_unset_ac35(text: str, expected: float | None) -> None:
    data = rad_file(4843, [obs_block(0)]).replace(
        b"OBS_DURATION=961", f"OBS_DURATION={text}".encode()
    )

    (observation,) = parse(data)

    assert attr(observation, "obs_duration_s") == expected


def test_combine_hours_start_near_the_end_of_time_is_dropped_without_an_error_ac36() -> None:
    far = datetime(9999, 12, 31, 23, 0, 0, tzinfo=UTC)

    result = combine_hours([*in_sol_batch(n_in=3), make_obs(9, far)], 4843)

    assert attr(result, "dropped") == 1
    assert attr(result, "reject") is None
    assert sum(attr(h, "n_obs") for h in hours_of(result)) == 3


def test_combine_hours_exact_midnight_by_inverse_msd_is_the_new_sol_hour_0_ac35() -> None:
    """The first microsecond with local day 4844 (found from `local_mean_time` itself): a middle
    exactly there is sol 4844, hour 0 (not 23 of 4843)."""
    from app.core.mars_time import local_mean_time

    moment = MIDNIGHT_4844 + timedelta(seconds=1)
    while local_mean_time(moment, 137.4)[0] - 49269 == 4844:
        moment -= timedelta(microseconds=100)
    while local_mean_time(moment, 137.4)[0] - 49269 != 4844:
        moment += timedelta(microseconds=1)
    start = moment - timedelta(seconds=480)
    others = [
        make_obs(2, moment + timedelta(hours=3), d=960),
        make_obs(4, moment + timedelta(hours=6), d=960),
    ]

    new = combine_hours([make_obs(0, start, d=960), *others], 4844)
    old = combine_hours([make_obs(0, start, d=960), *others[:0]], 4843)

    assert hour_numbers(new)[0] == 0 and attr(new, "dropped") == 0
    assert attr(old, "reject") == "off_sol"  # the only observation belongs to sol 4844
