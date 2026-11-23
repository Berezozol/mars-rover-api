"""Helpers for the stage-17 weather tests (docs/proposals/stage-17-weather.md, API.md WeatherSol).

Written from the contract (spec sections 2, 3, 6, 7 and DATA-SOURCES.md "Погода: разбор
локальных файлов"), not from the implementation. Everything the contract does not pin down
(the calling convention of `app.sources.weather.iter_*`, the names of `SolAggregate` fields)
is isolated here so that one place adapts when the implementation lands:

- `read_samples(kind, path, sol)`: call `iter_<kind>` and normalise to
  `(lmst_s, param, value)` tuples plus the `RawCounters` object (or None);
- synthetic file builders: MEDA CSV (`ats_csv`, `ps_csv`, `rhs_csv`, `ws_csv`), REMS `RMD`
  rows (`rmd_row`, `rmd_tab`), labels from the real fixture labels with the date replaced;
- `build_meda_sol` / `build_rems_sol` write a raw tree like `data/weather_raw/` (old mtimes:
  files younger than 60 s are skipped by the parser);
- `weather_env` fixture + `run_weather(*argv)` run the CLI; DB helpers read the four tables.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "upstream" / "weather"
Factory = async_sessionmaker[AsyncSession]

LOCK_KEY = 7_250_005
OLD_MTIME_NS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 1_000_000_000

CREDIT_PERSEVERANCE = (
    "Rodriguez-Manfredi, J. A.; de la Torre Juarez, M. (MEDA, Mars 2020) / "
    "NASA PDS Atmospheres Node, urn:nasa:pds:mars2020_meda"
)
CREDIT_CURIOSITY = (
    "REMS team (Centro de Astrobiologia) / NASA PDS Atmospheres Node, urn:nasa:pds:msl_rems"
)
SOURCE_URL = {
    "perseverance": "https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/",
    "curiosity": "https://atmos.nmsu.edu/PDS/data/mslrem_1001/",
}

# --- fixtures of the real files ---------------------------------------------------------------

MEDA_FIXTURE_SOLS = {
    100: "sol_0090_0179",
    400: "sol_0300_0419",
    1230: "sol_1140_1259",
    1852: "sol_1740_1859",
}


def fixture_files(sol: int, product: str | None = None) -> list[Path]:
    """Fixture files (CSV/TAB, not labels) of a sol, optionally of one product."""
    if sol in MEDA_FIXTURE_SOLS:
        pattern = f"meda/*/*/sol_{sol:04d}/*.CSV"
    else:
        pattern = f"rems/DATA/*/SOL{sol:05d}/*.TAB"
    found = sorted(FIXTURES.glob(pattern))
    if product:
        found = [p for p in found if f"_{product}_" in p.name or product in p.name]
    return found


def fixture_one(sol: int, product: str) -> Path:
    found = fixture_files(sol, product)
    assert len(found) == 1, (sol, product, found)
    return found[0]


def copy_fixture_sols(dst: Path, meda: Iterable[int] = (), rems: Iterable[int] = ()) -> Path:
    """Copy the real fixture folders of the sols into a raw tree `dst/{meda,rems}/...`."""
    for sol in meda:
        for folder in FIXTURES.glob(f"meda/*/*/sol_{sol:04d}"):
            target = dst / folder.relative_to(FIXTURES)
            shutil.copytree(folder, target, dirs_exist_ok=True)
    for sol in rems:
        for folder in FIXTURES.glob(f"rems/DATA/*/SOL{sol:05d}"):
            target = dst / folder.relative_to(FIXTURES)
            shutil.copytree(folder, target, dirs_exist_ok=True)
    age_tree(dst)
    return dst


def age_tree(root: Path, mtime_ns: int = OLD_MTIME_NS) -> None:
    """Give every file an old mtime (the parser skips files younger than 60 s)."""
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, ns=(mtime_ns, mtime_ns))


# --- reference reading of the real files (independent of the implementation) --------------------


def lmst_seconds(text_value: str) -> float:
    """`00100M12:00:01.000` -> seconds since the local midnight."""
    match = re.fullmatch(r"\d{5}M(\d\d):(\d\d):(\d\d(?:\.\d+)?)", text_value.strip())
    assert match, text_value
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def lmst_text(sol: int, seconds: float) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{sol:05d}M{int(h):02d}:{int(m):02d}:{s:06.3f}"


def median3(a: float, b: float, c: float) -> float:
    return sorted((a, b, c))[1]


# --- synthetic MEDA files ------------------------------------------------------------------------

ATS_HEADER = (
    "SCLK,LMST,LTST,ATS_LOCAL_TEMP1,ATS_LOCAL_TEMP2,ATS_LOCAL_TEMP3,ATS_LOCAL_TEMP4,ATS_LOCAL_TEMP5"
)
PS_HEADER = "SCLK,LMST,LTST,PRESSURE,PRESSURE_UNCERTAINTY,TRANSDUCER"
RHS_HEADER = (
    "SCLK,LMST,LTST,LOCAL_RELATIVE_HUMIDITY,LOCAL_RELATIVE_HUMIDITY_UNCERTAINTY,"
    "HUMIDITY_LOCAL_TEMP,HUMIDITY_LOCAL_TEMP_UNCERTAINTY,VOLUME_MIXING_RATIO,"
    "VOLUME_MIXING_RATIO_UNCERTAINTY"
)
WS_HEADER = (
    "SCLK,LMST,LTST,BOOM1_HORIZONTAL_WIND_SPEED,BOOM1_HORIZONTAL_WIND_SPEED_UNCERTAINTY,"
    "BOOM1_VERTICAL_WIND_SPEED,BOOM1_VERTICAL_WIND_SPEED_UNCERTAINTY,BOOM1_WIND_DIRECTION,"
    "BOOM1_WIND_DIRECTION_UNCERTAINTY,BOOM2_HORIZONTAL_WIND_SPEED,"
    "BOOM2_HORIZONTAL_WIND_SPEED_UNCERTAINTY,BOOM2_VERTICAL_WIND_SPEED,"
    "BOOM2_VERTICAL_WIND_SPEED_UNCERTAINTY,BOOM2_WIND_DIRECTION,"
    "BOOM2_WIND_DIRECTION_UNCERTAINTY,BOOM1_ALG_STATE_STABLE,BOOM2_ALG_STATE_STABLE,ROVER_STILL"
)


TIRS_HEADER = (
    "SCLK,LMST,LTST,DOWNWARD_LW_IRRADIANCE,DOWNWARD_LW_IRRADIANCE_UNCERTAINTY,"
    "UPWARD_LW_IRRADIANCE,UPWARD_LW_UNCERTAINTY,RSM_HEAD_OUTSIDE_TIRS_UPWARD_LOOKING_FOV,"
    "WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV,SUN_OUTSIDE_TIRS_FOV,ROVER_LOW_TILT,"
    "TIRS_GROUND_FOOTPRINT_NOT_IN_SHADOW,ROVER_HGA_OFF,SKYCAM_OFF,ROVER_STILL"
)
STEFAN_BOLTZMANN = 5.670374419e-8


def flux_of(kelvin: float) -> float:
    """`UPWARD_LW_IRRADIANCE` (W/m2) of a ground brightness temperature: sigma * T^4."""
    return STEFAN_BOLTZMANN * kelvin**4


def fmt(value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return f"{value:.2f}"


def _csv(header: str, rows: list[list[str]], nl: str, bom: bool, trailing_nl: bool = True) -> bytes:
    body = nl.join([header, *[",".join(r) for r in rows]]) + (nl if trailing_nl else "")
    return (("﻿" if bom else "") + body).encode("utf-8")


def ats_csv(
    sol: int,
    rows: list[tuple[float, float | str | None, ...]],
    *,
    nl: str = "\r\n",
    bom: bool = False,
) -> bytes:
    """Rows are `(lmst_s, t1, t2, t3)` or `(lmst_s, t1, t2, t3, t4, t5)`; T4/T5 default 290 K."""
    out = []
    for i, row in enumerate(rows):
        lmst_s, t1, t2, t3, *rest = row
        t4, t5 = (rest + [290.0, 290.0])[:2] if rest else (290.0, 290.0)
        out.append(
            [
                f"{675771620 + i}",
                lmst_text(sol, lmst_s),
                "0000 00:00:00",
                fmt(t1),
                fmt(t2),
                fmt(t3),
                fmt(t4),
                fmt(t5),
            ]
        )
    return _csv(ATS_HEADER, out, nl, bom)


def ps_csv(
    sol: int, rows: list[tuple[float, float | str | None]], *, nl: str = "\r\n", bom: bool = False
) -> bytes:
    out = [
        [f"{675771620 + i}", lmst_text(sol, s), "0000 00:00:00", fmt(p), "", "1"]
        for i, (s, p) in enumerate(rows)
    ]
    return _csv(PS_HEADER, out, nl, bom)


def rhs_csv(
    sol: int, rows: list[tuple[float, float | str | None]], *, nl: str = "\r\n", bom: bool = False
) -> bytes:
    out = [
        [f"{675771620 + i}", lmst_text(sol, s), "0000 00:00:00", fmt(h), "", "200.00", "0.22",
         "17.10", ""]
        for i, (s, h) in enumerate(rows)
    ]  # fmt: skip
    return _csv(RHS_HEADER, out, nl, bom)


def ws_csv(
    sol: int,
    rows: list[tuple[float, float | str | None, float | str | None, float | str | None,
                     float | str | None, int | str, int | str]],
    *,
    nl: str = "\r\n",
) -> bytes:  # fmt: skip
    """Rows `(lmst_s, b1_speed, b1_dir, b2_speed, b2_dir, b1_stable, b2_stable)`."""
    out = []
    for i, (s, v1, d1, v2, d2, st1, st2) in enumerate(rows):
        out.append(
            [f"{675786582 + i}", lmst_text(sol, s), "0000 00:00:00", fmt(v1), "", "", "", fmt(d1),
             "", fmt(v2), "", "", "", fmt(d2), "", str(st1), str(st2), "1"]
        )  # fmt: skip
    return _csv(WS_HEADER, out, nl, False)


def tirs_csv(
    sol: int,
    rows: list[tuple[float, float | str | None, int | str, int | str]],
    *,
    nl: str = "\r\n",
) -> bytes:
    """Rows `(lmst_s, upward_flux, rover_low_tilt, wheel_outside_fov)`; flux in W/m2."""
    out = []
    for i, (s, flux, tilt, wheel) in enumerate(rows):
        flux_text = flux if isinstance(flux, str) or flux is None else f"{flux:.4f}"
        out.append(
            [f"{675771620 + i}", lmst_text(sol, s), "0000 00:00:00", "27.86", "2.09",
             fmt(flux_text), "0.74", "0", str(wheel), "1", str(tilt), "1", "", "", "1"]
        )  # fmt: skip
    return _csv(TIRS_HEADER, out, nl, False)


def hourly_ground(
    hours: Iterable[int], value: Callable[[int], float], *, per_hour: int = 6
) -> list[tuple[float, float, int, int]]:
    """Level-rover, wheel-not-seen rows whose brightness temperature in hour h is value(h) K."""
    step = 3600 / per_hour
    return [(h * 3600 + k * step, flux_of(value(h)), 1, 1) for h in hours for k in range(per_hour)]


def hourly_air(
    sol: int,
    hours: Iterable[int],
    *,
    per_hour: int = 60,
    value: Callable[[int], float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """`per_hour` evenly spaced rows in every hour; T1 = T2 = T3 = value(hour) (default 200 + h)."""
    value = value or (lambda h: 200.0 + h)
    step = 3600 / per_hour
    return [
        (h * 3600 + k * step, value(h), value(h), value(h)) for h in hours for k in range(per_hour)
    ]


def hourly_scalar(
    hours: Iterable[int], value: Callable[[int], float], *, per_hour: int = 60
) -> list[tuple[float, float]]:
    step = 3600 / per_hour
    return [(h * 3600 + k * step, value(h)) for h in hours for k in range(per_hour)]


# --- synthetic REMS RMD --------------------------------------------------------------------------

_MASK_WIDTH = {6: 8, 10: 8, 12: 8, 14: 8, 16: 12, 29: 8, 35: 8, 39: 8}
GOOD_AIR_MASK = "101XX1111"  # bytes 7 and 8 are 1
GOOD_GTS_MASK = "1111"  # bytes 0 and 1 are 1
GOOD_PS_MASK = "11"  # byte 1 is 1


def rmd_row(
    sol: int,
    lmst_s: float,
    *,
    air: float | None = 210.0,
    air_mask: str = GOOD_AIR_MASK,
    pressure: float | None = 750.0,
    ps_mask: str = GOOD_PS_MASK,
    ground: float | None = 220.0,
    gts_mask: str = GOOD_GTS_MASK,
    uv: float | None = 5.0,
    humidity: float | None = 3.0,
    wind: tuple[float, float, float] | None = None,
    timestamp: int = 398291714,
    drop_fields: int = 0,
) -> str:
    """One 40-column `RMD` line (no line ending) in the real formatting.

    `None` writes `-999.00`; `wind` is `(horizontal speed, vertical speed, direction)`.
    Column numbers (0-based): wind 3, 5, 6; ground 7, mask 10; air 15, mask 16; UV_ABC 20;
    humidity 30; pressure 37, mask 39.
    """

    def num(v: float | None) -> str:
        return f"{-999.0 if v is None else v:7.2f}"

    cols: list[str] = ["" for _ in range(40)]
    cols[0] = f"{timestamp:10d}"
    cols[1] = f'"{lmst_text(sol, lmst_s)}"'
    cols[2] = f'"{sol:05d} 00:00:00"'
    cols[3], cols[4], cols[5] = (num(v) for v in (wind or (None, None, None)))
    cols[6] = "XXXXXX"
    cols[7] = num(ground)
    cols[8] = cols[9] = num(0.5)
    cols[10] = gts_mask
    cols[11] = cols[13] = num(air)
    cols[12] = cols[14] = "101XX001"
    cols[15] = num(air)
    cols[16] = air_mask
    for i in (17, 18, 19, 21, 22):
        cols[i] = num(0.0)
    cols[20] = num(uv)
    for i in range(23, 29):
        cols[i] = num(10.0)
    cols[29] = "XXXX"
    cols[30] = num(humidity)
    cols[31] = num(253.6)
    cols[32] = num(2.0)
    cols[33] = num(380.1)
    cols[34] = num(None)
    cols[35] = "0X1"
    cols[36] = "2H"
    cols[37] = num(pressure)
    cols[38] = num(2.7)
    cols[39] = ps_mask
    for i, width in _MASK_WIDTH.items():
        cols[i] = f'"{cols[i]:>{width}}"'
    cols[36] = '"2H"'
    if drop_fields:
        cols = cols[:-drop_fields]
    return ",".join(cols)


def rmd_tab(lines: list[str], *, nl: str = "\r\n") -> bytes:
    return (nl.join(lines) + nl).encode("ascii")


def rmd_hourly(sol: int, hours: Iterable[int], *, per_hour: int = 60, **kw: Any) -> list[str]:
    """Rows every `3600 / per_hour` s in the hours; `kw` may hold `air=callable(hour)`."""
    step = 3600 / per_hour
    rows = []
    for h in hours:
        for k in range(per_hour):
            kwargs = {key: (val(h) if callable(val) else val) for key, val in kw.items()}
            rows.append(rmd_row(sol, h * 3600 + k * step, **kwargs))
    return rows


# --- labels and trees ----------------------------------------------------------------------------

_REAL_LABEL = next(FIXTURES.glob("meda/data_calibrated_env/*/sol_0100/*CAL_ATS*.xml"), None)
_REAL_LBL = next(FIXTURES.glob("rems/DATA/*/SOL00009/*.LBL"), None)
_REAL_REMS_XML = next(FIXTURES.glob("rems/DATA/*/SOL00009/*.xml"), None)

PRODUCT_COLLECTION = {
    "CAL_ATS": "data_calibrated_env",
    "CAL_WS": "data_calibrated_env",
    "DER_PS": "data_derived_env",
    "DER_RHS": "data_derived_env",
    "DER_TIRS": "data_derived_env",
}


def meda_filename(sol: int, product: str, version: str, ext: str = "CSV") -> str:
    """`WE__0100___________CAL_ATS_________________P01.CSV`: the product padded to 24 with `_`."""
    return f"WE__{sol:04d}___________{product:_<24}{version}.{ext}"


def sol_start(sol: int) -> datetime:
    """A plausible UTC start of a Perseverance sol (landing + sol x 88775.244 s)."""
    return datetime(2021, 2, 18, 20, 55, tzinfo=UTC) + timedelta(seconds=sol * 88775.244)


def meda_label(sol: int, product: str, version: str, *, start: datetime | None = None,
               ls: float | None = None) -> bytes:  # fmt: skip
    """The real fixture label with the file name, start time and Ls replaced (CRLF kept)."""
    assert _REAL_LABEL is not None
    raw = _REAL_LABEL.read_bytes().decode("utf-8")
    start = start or sol_start(sol)
    stamp = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    raw = re.sub(r"<start_date_time>[^<]*<", f"<start_date_time>{stamp}<", raw)
    if ls is not None:
        raw = re.sub(r"(start_solar_longitude[^>]*>)[^<]*<", rf"\g<1>{ls}<", raw)
    raw = raw.replace(
        "WE__0100___________CAL_ATS_________________P01", meda_filename(sol, product, version)[:-4]
    )
    return raw.encode("utf-8")


def build_meda_sol(
    root: Path,
    sol: int,
    products: dict[str, bytes],
    *,
    versions: dict[str, str] | None = None,
    start: datetime | None = None,
    ls: float | None = None,
) -> dict[str, Path]:
    """Write `products` (name -> CSV bytes) of a sol under `root/meda/...`; returns the CSV paths.

    Product names are `CAL_ATS`, `CAL_WS`, `DER_PS`, `DER_RHS`; `versions` default to `P01`.
    The folder name of the 90-sol range is arbitrary (`sol_0000_0089` style).
    """
    versions = versions or {}
    paths: dict[str, Path] = {}
    low = sol // 90 * 90
    for product, data in products.items():
        version = versions.get(product, "P01")
        folder = (
            root / "meda" / PRODUCT_COLLECTION[product] / f"sol_{low:04d}_{low + 89:04d}"
            / f"sol_{sol:04d}"
        )  # fmt: skip
        folder.mkdir(parents=True, exist_ok=True)
        csv_path = folder / meda_filename(sol, product, version)
        csv_path.write_bytes(data)
        csv_path.with_suffix(".xml").write_bytes(
            meda_label(sol, product, version, start=start, ls=ls)
        )
        paths[product] = csv_path
    age_tree(root)
    return paths


def rems_filename(sol: int, version: str, ext: str = "TAB", sclk: int = 398245439) -> str:
    return f"RME_{sclk}RMD{sol:04d}0000000_______{version}.{ext}"


def build_rems_sol(
    root: Path, sol: int, tab: bytes, *, version: str = "P1", start: datetime | None = None
) -> Path:
    low = (sol - 1) // 89 * 89 + 1
    folder = root / "rems" / "DATA" / f"SOL_{low:05d}_{low + 88:05d}" / f"SOL{sol:05d}"
    folder.mkdir(parents=True, exist_ok=True)
    start = start or (datetime(2012, 8, 6, 5, 17, tzinfo=UTC) + timedelta(days=sol * 1.0275))
    name = rems_filename(sol, version)
    path = folder / name
    path.write_bytes(tab)
    assert _REAL_LBL is not None and _REAL_REMS_XML is not None
    lbl = _REAL_LBL.read_bytes().decode("ascii")
    lbl = re.sub(r"(START_TIME\s*=\s*)\S+", rf"\g<1>{start.strftime('%Y-%m-%dT%H:%M:%S.000')}", lbl)
    lbl = lbl.replace(_REAL_LBL.stem, path.stem)
    folder.joinpath(name.replace(".TAB", ".LBL")).write_bytes(lbl.encode("ascii"))
    xml = _REAL_REMS_XML.read_bytes().decode("utf-8")
    xml = re.sub(
        r"<start_date_time>[^<]*<",
        f"<start_date_time>{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}<",
        xml,
    )
    xml = xml.replace(_REAL_REMS_XML.stem, path.stem)
    folder.joinpath(name.replace(".TAB", ".xml")).write_bytes(xml.encode("utf-8"))
    age_tree(root)
    return path


def full_meda_sol(
    root: Path,
    sol: int,
    *,
    air: Callable[[int], float] | None = None,
    hours: Iterable[int] = range(24),
    with_ws: bool = False,
    ground: Callable[[int], float] | None = None,
    pressure: float = 740.0,
    humidity: float = 5.0,
    **kwargs: Any,
) -> dict[str, Path]:
    """A small valid MEDA sol: ATS/PS/RHS (and optionally WS) with 60 rows in each hour."""
    hours = list(hours)
    products = {
        "CAL_ATS": ats_csv(sol, hourly_air(sol, hours, value=air)),
        "DER_PS": ps_csv(sol, hourly_scalar(hours, lambda h: pressure)),
        "DER_RHS": rhs_csv(sol, hourly_scalar(hours, lambda h: humidity)),
    }
    if ground is not None:
        products["DER_TIRS"] = tirs_csv(sol, hourly_ground(hours, ground))
    if with_ws:
        products["CAL_WS"] = ws_csv(
            sol, [(s, 3.0, 90.0, 4.0, 100.0, 1, 1) for s, _ in hourly_scalar(hours, lambda h: 0)]
        )
    return build_meda_sol(root, sol, products, **kwargs)


def full_rems_sol(root: Path, sol: int, **kwargs: Any) -> Path:
    return build_rems_sol(root, sol, rmd_tab(rmd_hourly(sol, range(24), per_hour=6)), **kwargs)


# --- the adapter around app.sources.weather ------------------------------------------------------


def _param_of(kind: str) -> str:
    return {"ats": "air", "ps": "pressure", "rhs": "humidity", "rmd": "rmd", "ws": "wind"}[kind]


def read_samples(kind: str, path: Path, sol: int) -> tuple[list[tuple[float, str, float]], Any]:
    """Run `app.sources.weather.iter_meda_<kind>` / `iter_rems_rmd` and normalise the rows.

    Returns `([(lmst_s, param, value), ...], counters)`. A 2-tuple row is `(lmst_s, value)` of
    the one parameter of the kind; a row with `.param` (WeatherSample) or a `str` second item
    is already `(lmst_s, param, value)`. `counters` is the `RawCounters` passed in (if the
    function takes `counters`) or the `.counters` attribute of the result, else None.
    """
    import inspect

    from app.sources import weather as src

    func = getattr(src, "iter_rems_rmd" if kind == "rmd" else f"iter_meda_{kind}")
    kwargs: dict[str, Any] = {}
    counters = None
    params = inspect.signature(func).parameters
    for name in ("counters", "counter"):
        if name in params:
            counters = src.RawCounters()
            kwargs[name] = counters
    result = func(path, sol, **kwargs)
    rows: list[tuple[float, str, float]] = []
    for item in result:
        if hasattr(item, "param"):
            rows.append((item.lmst_s, item.param, item.value))
        elif len(item) == 3 and isinstance(item[1], str):
            rows.append((item[0], item[1], item[2]))
        elif len(item) == 2:
            rows.append((item[0], _param_of(kind), item[1]))
        else:
            raise AssertionError(f"unrecognised sample shape: {item!r}")
    if counters is None:
        counters = getattr(result, "counters", None)
    return rows, counters


def pick(rows: list[tuple[float, str, float]], key: str) -> list[tuple[float, float]]:
    """Samples whose parameter name contains `key` (`air`, `pressure`, `humid`, `ground`, ...)."""
    return [(s, v) for s, p, v in rows if key in p.lower()]


def counter_oor(counters: Any, key: str) -> int:
    """`counters.out_of_range[<param containing key>]` (0 if absent)."""
    table = counters.out_of_range
    return sum(int(v) for k, v in dict(table).items() if key in str(k).lower())


# --- CLI and DB ----------------------------------------------------------------------------------


@pytest.fixture
def weather_env(
    monkeypatch: pytest.MonkeyPatch, settings: Any, tmp_path: Path
) -> Callable[..., Path]:
    """Environment of the CLI under test; returns `set(**WEATHER_* overrides)`; value: raw dir.

    `WEATHER_RAW_DIR` is `tmp_path / "raw"` (created empty), one worker process.
    """
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    raw = tmp_path / "raw"
    raw.mkdir()

    def set_env(**extra: Any) -> Path:
        for key, value in {
            "DATABASE_URL": settings.database_url,
            "SYNC_ENABLED": "false",
            "WEATHER_RAW_DIR": str(raw),
            "WEATHER_PARSE_WORKERS": "1",
            **extra,
        }.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        return raw

    _GUARD["database"] = make_url(settings.database_url).database
    set_env()
    yield set_env  # type: ignore[misc]
    get_settings.cache_clear()


# Name of the database the `weather_env` fixture of the running test points the CLI at.
_GUARD: dict[str, str | None] = {"database": None}


async def run_weather(*argv: str, crash_ok: bool = False) -> int:
    """`python -m app.cli weather <argv>` in a worker thread; returns the exit code."""
    from sqlalchemy.engine import make_url

    from app import cli

    # Safety: the CLI reads DATABASE_URL from the environment; never let it reach a real database.
    database = make_url(os.environ.get("DATABASE_URL", "")).database or ""
    if not database or database != _GUARD["database"]:
        raise RuntimeError(
            "run_weather needs the `weather_env` fixture and the test database "
            f"{_GUARD['database']!r} (DATABASE_URL={database!r})"
        )
    try:
        return await asyncio.to_thread(cli.main, ["weather", *argv])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception:
        if crash_ok:
            return -1
        raise


async def fetch(factory: Factory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


async def daily_rows(factory: Factory, rover: str = "perseverance") -> list[dict[str, Any]]:
    return await fetch(
        factory, "SELECT * FROM weather_daily WHERE rover_id = :r ORDER BY sol", r=rover
    )


async def daily_row(factory: Factory, sol: int, rover: str = "perseverance") -> dict[str, Any]:
    rows = await fetch(
        factory, "SELECT * FROM weather_daily WHERE rover_id = :r AND sol = :s", r=rover, s=sol
    )
    assert len(rows) == 1, (rover, sol, rows)
    return rows[0]


async def hourly_rows(
    factory: Factory, sol: int, rover: str = "perseverance"
) -> list[dict[str, Any]]:
    return await fetch(
        factory,
        "SELECT * FROM weather_hourly WHERE rover_id = :r AND sol = :s ORDER BY hour",
        r=rover,
        s=sol,
    )


async def file_rows(factory: Factory, rover: str = "perseverance") -> list[dict[str, Any]]:
    return await fetch(
        factory, "SELECT * FROM weather_files WHERE rover_id = :r ORDER BY path", r=rover
    )


async def dataset_row(factory: Factory, rover: str = "perseverance") -> dict[str, Any] | None:
    rows = await fetch(factory, "SELECT * FROM weather_dataset WHERE rover_id = :r", r=rover)
    return rows[0] if rows else None


async def count(factory: Factory, table: str) -> int:
    return (await fetch(factory, f"SELECT count(*) AS n FROM {table}"))[0]["n"]


async def wipe_weather(factory: Factory) -> None:
    async with factory() as session:
        await session.execute(
            text("TRUNCATE weather_hourly, weather_daily, weather_files, weather_dataset")
        )
        await session.commit()


_DAILY_DEFAULTS: dict[str, Any] = {
    "earth_date": date(2021, 6, 1),
    "ls": 52.4,
    "coverage": 0.61,
    "hours_covered": 24,
    "air_min_k": 190.75,
    "air_max_k": 254.25,
    "air_mean_k": 219.45,
    "pressure_min_pa": 708.34,
    "pressure_max_pa": 745.06,
    "pressure_mean_pa": 726.04,
    "humidity_min_pct": 0.04,
    "humidity_max_pct": 14.61,
    "humidity_mean_pct": 2.34,
    "n_air": 100,
    "n_pressure": 100,
    "n_humidity": 100,
    "n_ground": 0,
    "n_uv": 0,
    "n_wind": 0,
    "flags": "[]",
    "source_version": '{"CAL_ATS": "P02", "DER_PS": "P01", "DER_RHS": "P01"}',
    "parser_revision": 1,
    "updated_at": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC),
}

_JSONB = {"flags", "source_version", "import_meta"}


async def insert_daily(
    factory: Factory, sol: int, rover: str = "perseverance", *, hours: bool = True, **values: Any
) -> None:
    """Insert one `weather_daily` row (+ 24 `weather_hourly` rows) with SI units as stored.

    `values` override the defaults above; JSON columns accept Python lists/dicts.
    """
    import json

    row = {**_DAILY_DEFAULTS, **values, "rover_id": rover, "sol": sol}
    for key in _JSONB & row.keys():
        if not isinstance(row[key], str):
            row[key] = json.dumps(row[key])
    cols = list(row)
    marks = [f"CAST(:{c} AS jsonb)" if c in _JSONB else f":{c}" for c in cols]
    async with factory() as session:
        await session.execute(
            text(f"INSERT INTO weather_daily ({', '.join(cols)}) VALUES ({', '.join(marks)})"), row
        )
        if hours:
            for hour in range(24):
                await session.execute(
                    text(
                        "INSERT INTO weather_hourly (rover_id, sol, hour, coverage) "
                        "VALUES (:r, :s, :h, 0)"
                    ),
                    {"r": rover, "s": sol, "h": hour},
                )
        await session.commit()


async def set_hour(
    factory: Factory, sol: int, hour: int, rover: str = "perseverance", **values: Any
) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in values)
    async with factory() as session:
        await session.execute(
            text(
                f"UPDATE weather_hourly SET {sets} WHERE rover_id = :r AND sol = :s AND hour = :h"
            ),
            {**values, "r": rover, "s": sol, "h": hour},
        )
        await session.commit()


async def insert_dataset(factory: Factory, rover: str = "perseverance", **values: Any) -> None:
    values = {
        "updated_at": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC),
        "changed_at": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC),
        "parsed_at": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC),
        "source_max_sol": 1859,
        **values,
        "rover_id": rover,
    }
    values = {k: datetime.fromisoformat(v) if k.endswith("_at") and isinstance(v, str) else v
              for k, v in values.items()}  # fmt: skip
    cols = list(values)
    async with factory() as session:
        await session.execute(
            text(
                f"INSERT INTO weather_dataset ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)}) "
                "ON CONFLICT (rover_id) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "rover_id")
            ),
            values,
        )
        await session.commit()


# --- reference Mars24 Ls (Allison & McEwen 2000), independent of the implementation ---------------

_PBS = [
    (0.0071, 2.2353, 49.409),
    (0.0057, 2.7543, 168.173),
    (0.0039, 1.1177, 191.837),
    (0.0037, 15.7866, 21.736),
    (0.0021, 2.1354, 15.704),
    (0.0020, 2.4694, 95.528),
    (0.0018, 32.8493, 49.095),
]


def reference_ls(when: datetime) -> float:
    """Areocentric solar longitude (degrees) of a UTC time; agrees with MEDA labels to 0.005."""
    import math

    jd_tt = when.timestamp() / 86400 + 2440587.5 + (37 + 32.184) / 86400
    t = jd_tt - 2451545.0
    m = math.radians(19.3871 + 0.52402073 * t)
    alpha = 270.3871 + 0.524038496 * t
    pbs = sum(a * math.cos(math.radians(0.985626 * t / tau + phi)) for a, tau, phi in _PBS)
    centre = (
        (10.691 + 3.0e-7 * t) * math.sin(m)
        + 0.623 * math.sin(2 * m)
        + 0.050 * math.sin(3 * m)
        + 0.005 * math.sin(4 * m)
        + 0.0005 * math.sin(5 * m)
    )
    return (alpha + centre + pbs) % 360.0
