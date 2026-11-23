"""File discovery, labels, LMST parsing and the Ls function of `app/sources/weather.py` (stage 17).

AC-12 (versions, `.part`, orphan labels), the boundary cases of the spec (empty directory,
only `DER_PS`), `parse_lmst`, `read_meda_label` / `read_rems_label`, and the Mars24 Ls
(coordinator's addition: published dates within 0.1 deg, MEDA labels within 0.01 deg).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests._weather import (
    FIXTURES,
    MEDA_FIXTURE_SOLS,
    ats_csv,
    build_meda_sol,
    build_rems_sol,
    fixture_one,
    hourly_air,
    meda_filename,
    ps_csv,
    rmd_row,
    rmd_tab,
)

pytestmark = pytest.mark.unit


def scan(raw: Path, rover: str) -> list:
    """`scan_raw_dir(root, rover)`: the root is `WEATHER_RAW_DIR` (the contract says "root")."""
    from app.sources.weather import scan_raw_dir

    found = scan_raw_dir(raw, rover)
    if not found:  # tolerate an implementation whose root is the mission directory
        mission = raw / ("meda" if rover == "perseverance" else "rems")
        if mission.is_dir():
            found = scan_raw_dir(mission, rover)
    return list(found)


def key(found: list) -> set[tuple[int, str, str]]:
    return {(f.sol, f.product, f.version) for f in found}


# --- scan_raw_dir --------------------------------------------------------------------------------


def test_scan_of_an_empty_directory_finds_nothing(tmp_path: Path) -> None:
    assert scan(tmp_path, "perseverance") == []
    assert scan(tmp_path, "curiosity") == []


def test_scan_finds_meda_products_with_sol_version_size_and_relative_path_ac12(
    tmp_path: Path,
) -> None:
    paths = build_meda_sol(
        tmp_path,
        100,
        {
            "CAL_ATS": ats_csv(100, hourly_air(100, [0], per_hour=3)),
            "DER_PS": ps_csv(100, [(0.0, 740.0)]),
        },
        versions={"CAL_ATS": "P02"},
    )

    found = scan(tmp_path, "perseverance")

    assert key(found) == {(100, "CAL_ATS", "P02"), (100, "DER_PS", "P01")}
    ats = next(f for f in found if f.product == "CAL_ATS")
    assert ats.rover == "perseverance"
    assert ats.size == paths["CAL_ATS"].stat().st_size
    assert ats.mtime_ns == paths["CAL_ATS"].stat().st_mtime_ns
    assert ats.path == (
        "data_calibrated_env/sol_0090_0179/sol_0100/" + meda_filename(100, "CAL_ATS", "P02")
    )


def test_scan_keeps_every_version_so_that_the_newer_can_be_chosen_ac12(tmp_path: Path) -> None:
    build_meda_sol(tmp_path, 100, {"CAL_ATS": ats_csv(100, [(0.0, 200.0, 200.0, 200.0)])})
    build_meda_sol(
        tmp_path,
        100,
        {"CAL_ATS": ats_csv(100, [(0.0, 201.0, 201.0, 201.0)])},
        versions={"CAL_ATS": "P02"},
    )

    found = scan(tmp_path, "perseverance")

    versions = sorted(f.version for f in found if f.product == "CAL_ATS")
    assert versions[-1] == "P02"
    assert set(versions) <= {"P01", "P02"}


def test_scan_ignores_part_files_orphan_labels_and_other_products_ac12(tmp_path: Path) -> None:
    paths = build_meda_sol(tmp_path, 100, {"DER_PS": ps_csv(100, [(0.0, 740.0)])})
    folder = paths["DER_PS"].parent
    (folder / (meda_filename(100, "CAL_ATS", "P01") + ".part")).write_bytes(b"partial")
    (folder / meda_filename(100, "CAL_ATS", "P01", "part")).write_bytes(b"partial")
    (folder / meda_filename(100, "DER_RHS", "P01", "xml")).write_bytes(b"<x/>")  # no CSV
    (folder / meda_filename(100, "DER_WS", "P02")).write_bytes(b"SCLK\n")
    (folder / meda_filename(100, "DER_ANCILLARY", "P02")).write_bytes(b"SCLK\n")
    (folder / "notes.txt").write_bytes(b"hello")

    found = scan(tmp_path, "perseverance")

    assert key(found) == {(100, "DER_PS", "P01")}


def test_scan_of_a_sol_with_only_der_ps_returns_just_that_product(tmp_path: Path) -> None:
    build_meda_sol(tmp_path, 7, {"DER_PS": ps_csv(7, [(0.0, 740.0)])})

    assert key(scan(tmp_path, "perseverance")) == {(7, "DER_PS", "P01")}


def test_scan_does_not_mix_the_two_missions(tmp_path: Path) -> None:
    build_meda_sol(tmp_path, 100, {"DER_PS": ps_csv(100, [(0.0, 740.0)])})
    build_rems_sol(tmp_path, 9, rmd_tab([rmd_row(9, 0.0)]), version="PA")

    assert key(scan(tmp_path, "perseverance")) == {(100, "DER_PS", "P01")}
    assert key(scan(tmp_path, "curiosity")) == {(9, "RMD", "PA")}


@pytest.mark.parametrize("version", ["P1", "PA"], ids=["p1", "pa-sol-9"])
def test_scan_accepts_rems_versions_p1_and_pa_ac12(tmp_path: Path, version: str) -> None:
    path = build_rems_sol(tmp_path, 9, rmd_tab([rmd_row(9, 0.0)]), version=version)

    found = scan(tmp_path, "curiosity")

    assert key(found) == {(9, "RMD", version)}
    assert found[0].path == f"DATA/SOL_00001_00089/SOL00009/{path.name}"


def test_scan_ignores_rems_part_and_label_files(tmp_path: Path) -> None:
    path = build_rems_sol(tmp_path, 9, rmd_tab([rmd_row(9, 0.0)]), version="P1")
    (path.parent / (path.name + ".part")).write_bytes(b"x")
    (path.parent / path.name.replace("RMD", "ADR")).write_bytes(b"x")  # other REMS product

    assert key(scan(tmp_path, "curiosity")) == {(9, "RMD", "P1")}


def test_scan_of_the_real_fixture_tree(tmp_path: Path) -> None:
    """The thinned fixtures keep the layout of data/weather_raw/: 4 MEDA sols, 2 REMS sols."""
    from shutil import copytree

    copytree(FIXTURES, tmp_path / "raw", ignore=lambda *_: ["README.md"])

    meda = key(scan(tmp_path / "raw", "perseverance"))
    rems = key(scan(tmp_path / "raw", "curiosity"))

    assert {s for s, _, _ in meda} == set(MEDA_FIXTURE_SOLS)
    assert {(s, p, v) for s, p, v in meda if s == 100} == {
        (100, "CAL_ATS", "P01"),
        (100, "CAL_WS", "P03"),
        (100, "DER_PS", "P01"),
        (100, "DER_RHS", "P02"),
        (100, "DER_TIRS", "P03"),
    }
    assert not any(s == 400 and p == "CAL_WS" for s, p, _ in meda)
    assert rems == {(9, "RMD", "PA"), (30, "RMD", "PA")}


# --- parse_lmst ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "sol", "seconds"),
    [
        ("00136M00:00:00.471", 136, 0.471),
        ("00100M12:34:56.759", 100, 45296.759),
        ("00100M23:59:59.900", 100, 86399.9),
        ("04800M00:00:00.000", 4800, 0.0),
    ],
    ids=["start", "midday", "last-second", "four-digit-sol"],
)
def test_parse_lmst_gives_the_sol_and_the_seconds(text: str, sol: int, seconds: float) -> None:
    from app.sources.weather import parse_lmst

    got_sol, got_seconds = parse_lmst(text)

    assert got_sol == sol
    assert got_seconds == pytest.approx(seconds)


def test_parse_lmst_of_the_last_second_belongs_to_hour_23() -> None:
    from app.sources.weather import parse_lmst

    _, seconds = parse_lmst("00100M23:59:59.900")

    assert int(seconds // 3600) == 23
    assert int(seconds // 600) == 143  # the last of the 144 ten-minute bins


@pytest.mark.parametrize("text", ["", "garbage", "00100 12:00:00", "00100M25:00:00.000"])
def test_parse_lmst_rejects_malformed_text(text: str) -> None:
    from app.sources.weather import WeatherFormatError, parse_lmst

    with pytest.raises((ValueError, WeatherFormatError)):
        parse_lmst(text)


# --- labels --------------------------------------------------------------------------------------


def test_meda_label_gives_the_start_time_and_ls() -> None:
    from app.sources.weather import read_meda_label

    label = read_meda_label(fixture_one(100, "CAL_ATS").with_suffix(".xml"))

    assert label.start_time == datetime(2021, 5, 31, 22, 23, 0, 282000, tzinfo=UTC)
    assert label.ls == pytest.approx(52.734)


def test_rems_label_gives_the_start_time_and_no_ls() -> None:
    from app.sources.weather import read_rems_label

    label = read_rems_label(fixture_one(9, "RMD").with_suffix(".LBL"))

    assert label.start_time == datetime(2012, 8, 15, 8, 37, 32, 99000, tzinfo=UTC)
    assert label.ls is None


# --- the Mars24 Ls -------------------------------------------------------------------------------

LS_NAMES = (
    "mars_ls",
    "solar_longitude",
    "ls_from_utc",
    "compute_ls",
    "mars24_ls",
    "mars_solar_longitude",
    "ls",
    "calc_ls",
    "solar_longitude_deg",
)


def ls_function():
    """The Mars24 function of `app.sources.weather` (the contract does not fix its name)."""
    from app.core import mars_time
    from app.sources import weather

    for module in (mars_time, weather):
        for name in LS_NAMES:
            func = getattr(module, name, None)
            if callable(func):
                return func
    raise AssertionError(
        f"no Mars24 Ls function in app.core.mars_time / app.sources.weather: {LS_NAMES}"
    )


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2021, 2, 7, 12, 0, tzinfo=UTC), 0.02),  # about 1 h after Ls = 0 of Mars Year 36
        (datetime(2021, 2, 18, 20, 55, tzinfo=UTC), 5.65),  # Perseverance landing
        (datetime(2012, 8, 6, 5, 17, tzinfo=UTC), 150.7),  # Curiosity landing (Ls 150.7)
    ],
    ids=["my36-start", "perseverance-landing", "curiosity-landing"],
)
def test_ls_matches_published_mars24_values_within_a_tenth_of_a_degree(
    when: datetime, expected: float
) -> None:
    got = ls_function()(when)

    assert abs(angle_diff(got, expected)) < 0.1


def test_ls_is_zero_at_the_start_of_mars_year_36() -> None:
    func = ls_function()

    assert abs(angle_diff(func(datetime(2021, 2, 7, 11, 5, tzinfo=UTC)), 0.0)) < 0.1
    assert func(datetime(2021, 2, 7, 0, 0, tzinfo=UTC)) > 359.0  # still the end of MY 35


def test_ls_of_a_utc_datetime_in_june_2021_is_about_55_degrees() -> None:
    func = ls_function()
    aware = func(datetime(2021, 6, 1, tzinfo=UTC))

    assert 52.0 < aware < 58.0


def _meda_labels() -> list[Path]:
    return sorted(FIXTURES.glob("meda/*/*/*/*.xml"))


@pytest.mark.parametrize("label", _meda_labels(), ids=lambda p: p.name[:24] + p.name[-7:-4])
def test_ls_agrees_with_the_meda_label_within_a_hundredth_of_a_degree(label: Path) -> None:
    text = label.read_text(encoding="utf-8")
    start = re.search(r"<start_date_time>([^<]+)<", text)
    ls = re.search(r"start_solar_longitude[^>]*>([^<]+)<", text)
    assert start and ls
    when = datetime.fromisoformat(start.group(1).replace("Z", "+00:00"))

    got = ls_function()(when)

    assert abs(angle_diff(got, float(ls.group(1)))) < 0.01


# --- links inside the raw tree are never followed ------------------------------------------------


def _link_dir(link: Path, target: Path) -> None:
    """Directory link: a junction on Windows (no privilege needed), a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined]
    except ImportError:
        link.symlink_to(target, target_is_directory=True)


def _link_file(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no privilege to create symlinks")


def test_scan_skips_a_junction_sol_directory_and_keeps_the_real_ones(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    other = tmp_path / "outside"
    build_meda_sol(raw, 1, {"DER_PS": ps_csv(1, [(0.0, 740.0)])})
    build_meda_sol(other, 2, {"DER_PS": ps_csv(2, [(0.0, 740.0)])})
    real = next(other.glob("meda/*/*/sol_0002"))
    _link_dir(raw / "meda" / "data_derived_env" / "sol_0000_0089" / "sol_0002", real)

    found = scan(raw, "perseverance")

    assert {f.sol for f in found} == {1}


def test_scan_skips_a_symlinked_file_and_keeps_the_real_ones(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    other = tmp_path / "outside"
    build_meda_sol(raw, 1, {"DER_PS": ps_csv(1, [(0.0, 740.0)])})
    outside = build_meda_sol(other, 3, {"DER_PS": ps_csv(3, [(0.0, 740.0)])})["DER_PS"]
    folder = raw / "meda" / "data_derived_env" / "sol_0000_0089" / "sol_0003"
    folder.mkdir(parents=True)
    _link_file(folder / outside.name, outside)

    found = scan(raw, "perseverance")

    assert {f.sol for f in found} == {1}


def test_a_symlinked_label_is_ignored(tmp_path: Path) -> None:
    from app.sources.weather import read_meda_label

    real = fixture_one(100, "CAL_ATS").with_suffix(".xml")
    assert read_meda_label(real).start_time is not None  # control: the real label is read
    folder = tmp_path / "sol_0100"
    folder.mkdir()
    csv = folder / real.with_suffix(".CSV").name
    csv.write_bytes(b"")
    _link_file(csv.with_suffix(".xml"), real)

    info = read_meda_label(csv)

    assert info.start_time is None and info.ls is None
