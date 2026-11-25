"""GET /api/v2/rovers/{rover}/weather/{sol}: hourly `_min`/`_max` and `wind_gust_ms` (stage 22 B)

Covers AC-15, AC-16, AC-17, AC-18, AC-19, AC-20 (and the OpenAPI snapshot of the hour schema)..

Spec: docs/proposals/sol-conditions.md AC-15 .. AC-20; contract: docs/API.md `/weather/{sol}`.
The database comes from the recorded MEDA/REMS fixtures through `weather parse` (no hand-made
hourly rows), so the tests see what the application really stores. Nothing touches the network.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._weather import (
    Factory,
    copy_fixture_sols,
    daily_rows,
    run_weather,
    set_hour,
)
from tests.api.v2.conftest import V2, assert_error
from tests.api.v2.test_weather import C, P, seed
from tests.api.v2.test_weather_ground import flag_off, flag_on  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

PARAM_KEYS = ("air_temp_c", "pressure_pa", "humidity_pct", "ground_temp_c")
P_SOLS = (100, 400, 1230, 1852)
C_SOLS = (9, 30)


def triples(keys: tuple[str, ...]) -> set[str]:
    return {name for key in keys for name in (key, f"{key}_min", f"{key}_max")}


P_HOUR_KEY_SET = (
    {"hour", "coverage"}
    | triples(PARAM_KEYS)
    | {
        "wind_speed_ms",
        "wind_gust_ms",
        "wind_direction_deg",
    }
)
C_HOUR_KEY_SET = {"hour", "coverage"} | triples((*PARAM_KEYS, "uv_wm2"))


@pytest.fixture
async def base(weather_env: Callable[..., Path], clean_db: Factory) -> Factory:
    """Parse the real fixture sols of both rovers into the test database."""
    raw = weather_env()
    copy_fixture_sols(raw, meda=P_SOLS, rems=C_SOLS)
    assert await run_weather("parse", "--rover", "perseverance") == 0
    assert await run_weather("parse", "--rover", "curiosity") == 0
    return clean_db


async def detail(client: httpx.AsyncClient, rover: str, sol: int) -> dict[str, Any]:
    response = await client.get(f"{V2}/rovers/{rover}/weather/{sol}")
    assert response.status_code == 200, response.text
    return response.json()


def values(hours: list[dict[str, Any]], key: str) -> list[float | None]:
    return [hour[key] for hour in hours]


def present(items: list[float | None]) -> list[float]:
    return [v for v in items if v is not None]


# --- AC-15: the keys, the rounding, min <= mean <= max ------------------------------------------


@pytest.mark.parametrize("sol", P_SOLS, ids=[f"sol-{s}" for s in P_SOLS])
async def test_perseverance_hours_have_all_min_max_and_gust_keys_ac15(
    client: httpx.AsyncClient, base: Factory, sol: int
) -> None:
    body = await detail(client, "perseverance", sol)

    assert [h["hour"] for h in body["hours"]] == list(range(24))
    for hour in body["hours"]:
        assert set(hour) == P_HOUR_KEY_SET, hour["hour"]


@pytest.mark.parametrize("sol", C_SOLS, ids=[f"sol-{s}" for s in C_SOLS])
async def test_curiosity_hours_have_uv_and_all_min_max_keys_and_no_gust_ac15_ac18(
    client: httpx.AsyncClient, base: Factory, sol: int
) -> None:
    response = await client.get(f"{C}/{sol}")

    assert response.status_code == 200
    hours = response.json()["hours"]
    assert all(set(hour) == C_HOUR_KEY_SET for hour in hours)
    assert not any("wind" in key or "gust" in key for hour in hours for key in hour)
    assert "gust" not in response.text and "wind" not in response.text.lower()


@pytest.mark.parametrize(
    ("rover", "sol", "keys"),
    [
        ("perseverance", 100, PARAM_KEYS),
        ("perseverance", 1852, PARAM_KEYS),
        ("curiosity", 30, (*PARAM_KEYS, "uv_wm2")),
        ("curiosity", 9, (*PARAM_KEYS, "uv_wm2")),
    ],
    ids=["m20-100", "m20-1852", "msl-30", "msl-9"],
)
async def test_hour_with_data_has_min_not_above_mean_not_above_max_ac15(
    client: httpx.AsyncClient, base: Factory, rover: str, sol: int, keys: tuple[str, ...]
) -> None:
    hours = (await detail(client, rover, sol))["hours"]

    with_data = 0
    for hour in hours:
        for key in keys:
            mean, low, high = hour[key], hour[f"{key}_min"], hour[f"{key}_max"]
            assert (mean is None) == (low is None) == (high is None), (hour["hour"], key)
            if mean is not None:
                with_data += 1
                assert low <= mean <= high, (hour["hour"], key, low, mean, high)
    assert with_data >= 40


@pytest.mark.parametrize(("rover", "sol"), [("perseverance", 100), ("curiosity", 30)])
async def test_hourly_extremes_are_rounded_like_the_means_ac15(
    client: httpx.AsyncClient, base: Factory, rover: str, sol: int
) -> None:
    """Temperatures, pressure and humidity to 0.1; UV to 0.01; the gust to 0.1."""
    hours = (await detail(client, rover, sol))["hours"]

    for hour in hours:
        for key, digits in [
            *[(f"{p}{s}", 1) for p in PARAM_KEYS for s in ("", "_min", "_max")],
            *[(f"uv_wm2{s}", 2) for s in ("", "_min", "_max")],
            ("wind_gust_ms", 1),
        ]:
            if key in hour and hour[key] is not None:
                assert hour[key] == round(hour[key], digits), (hour["hour"], key, hour[key])


async def test_hour_min_and_max_follow_the_ground_estimate_of_the_day_ac15(
    client: httpx.AsyncClient, base: Factory
) -> None:
    body = await detail(client, "perseverance", 100)

    ground = [h for h in body["hours"] if h["ground_temp_c"] is not None]
    assert len(ground) >= 18
    assert all(h["ground_temp_c_min"] is not None for h in ground)
    assert max(h["ground_temp_c_max"] for h in ground) > min(h["ground_temp_c_min"] for h in ground)


# --- AC-15 / AC-17 at the API level: the daily min/max are the extremes of the hourly ones --------


@pytest.mark.parametrize(
    ("rover", "sol"),
    [
        ("perseverance", 100),
        ("perseverance", 400),
        ("perseverance", 1230),
        ("perseverance", 1852),
        ("curiosity", 9),
        ("curiosity", 30),
    ],
    ids=["m20-100", "m20-400", "m20-1230", "m20-1852", "msl-9", "msl-30"],
)
async def test_daily_min_and_max_equal_the_extremes_of_the_hourly_ones_ac17(
    client: httpx.AsyncClient, base: Factory, rover: str, sol: int
) -> None:
    body = await detail(client, rover, sol)

    checked = 0
    for key in (*PARAM_KEYS, "uv_wm2"):
        daily = body.get(key)
        if f"{key}_min" not in body["hours"][0]:
            continue
        lows = present(values(body["hours"], f"{key}_min"))
        highs = present(values(body["hours"], f"{key}_max"))
        if daily is None:
            assert lows == [] and highs == [], key
            continue
        checked += 1
        if key != "uv_wm2":  # the daily UV object has `max` and `mean` only
            assert daily["min"] == min(lows), key
        assert daily["max"] == max(highs), key
    assert checked >= 3


async def test_daily_humidity_max_above_100_is_the_hourly_max_ac15_ac17(
    client: httpx.AsyncClient, base: Factory
) -> None:
    body = await detail(client, "perseverance", 1852)

    highs = present(values(body["hours"], "humidity_pct_max"))
    assert max(highs) == 183.8
    assert body["humidity_pct"]["max"] == 183.8
    assert max(highs) > 100.0


async def test_out_of_range_readings_of_sol_100_stay_out_of_the_hourly_extremes_ac15(
    client: httpx.AsyncClient, base: Factory
) -> None:
    """The fixture holds 999.9 K, 50 Pa and 2 680 332 % rows (flagged `out_of_range`)."""
    body = await detail(client, "perseverance", 100)

    assert {f["code"] for f in body["flags"]} >= {"out_of_range", "humidity_over_100"}
    hours = body["hours"]
    assert max(present(values(hours, "air_temp_c_max"))) < 0.0  # not 726 degC
    assert min(present(values(hours, "pressure_pa_min"))) >= 300.0  # not 50 Pa
    assert max(present(values(hours, "humidity_pct_max"))) <= 200.0
    assert hours[12]["humidity_pct_max"] >= 104.5  # the 104.5 % row is kept


# --- AC-16: an hour without measurements ----------------------------------------------------------


async def test_hours_without_measurements_have_null_in_the_mean_min_and_max_ac16(
    client: httpx.AsyncClient, base: Factory
) -> None:
    """Real sol 1230: the air sensor works in hours 0-7 only."""
    hours = (await detail(client, "perseverance", 1230))["hours"]

    quiet = hours[8:]
    assert len(quiet) == 16
    for hour in quiet:
        assert hour["coverage"] == 0
        for key in ("air_temp_c", "air_temp_c_min", "air_temp_c_max"):
            assert hour[key] is None, (hour["hour"], key)
    assert all(
        h["air_temp_c_min"] is not None and h["air_temp_c_max"] is not None for h in hours[:8]
    )


async def test_hour_without_any_parameter_is_null_in_every_data_key_ac16(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)
    await set_hour(clean_db, 100, 5, air_k=210.05, air_min_k=205.05, air_max_k=215.05)

    hours = (await client.get(f"{P}/100")).json()["hours"]

    empty = hours[6]
    assert empty["coverage"] == 0
    assert {k: v for k, v in empty.items() if k not in ("hour", "coverage")} == {
        k: None for k in P_HOUR_KEY_SET - {"hour", "coverage"}
    }
    assert hours[5]["air_temp_c"] == -63.1 and hours[5]["air_temp_c_min"] == -68.1
    assert hours[5]["air_temp_c_max"] == -58.1


async def test_stored_hourly_extremes_are_converted_and_rounded_like_the_means_ac15(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """K -> degC, hPa untouched, rounding as for the means; exact values from the stored SI row."""
    await seed(clean_db, "perseverance", 100)
    await set_hour(
        clean_db, 100, 0, coverage=0.5,
        air_k=201.95, air_min_k=199.35, air_max_k=204.55,
        pressure_pa=735.44, pressure_min_pa=734.14, pressure_max_pa=736.84,
        humidity_pct=5.84, humidity_min_pct=4.94, humidity_max_pct=6.74,
        ground_k=263.15, ground_min_k=253.15, ground_max_k=273.15,
        wind_ms=2.14, wind_gust_ms=6.44, wind_dir_deg=157.5,
    )  # fmt: skip

    hour = (await client.get(f"{P}/100")).json()["hours"][0]

    assert hour == {
        "hour": 0,
        "coverage": 0.5,
        "air_temp_c": -71.2,
        "air_temp_c_min": -73.8,
        "air_temp_c_max": -68.6,
        "pressure_pa": 735.4,
        "pressure_pa_min": 734.1,
        "pressure_pa_max": 736.8,
        "humidity_pct": 5.8,
        "humidity_pct_min": 4.9,
        "humidity_pct_max": 6.7,
        "wind_speed_ms": 2.1,
        "wind_gust_ms": 6.4,
        "wind_direction_deg": 157.5,
        "ground_temp_c": -10.0,
        "ground_temp_c_min": -20.0,
        "ground_temp_c_max": 0.0,
    }


# --- AC-18: per-rover keys, the Perseverance gust and the wind window ---------------------------


async def test_curiosity_hours_have_uv_min_max_and_no_wind_keys_ac18(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)
    await set_hour(clean_db, 30, 3, "curiosity", air_k=190.15, uv_wm2=2.5, uv_min_wm2=0.25,
                   uv_max_wm2=4.75, wind_gust_ms=9.0, wind_ms=7.0)  # fmt: skip

    response = await client.get(f"{C}/30")

    hour = response.json()["hours"][3]
    assert (hour["uv_wm2"], hour["uv_wm2_min"], hour["uv_wm2_max"]) == (2.5, 0.25, 4.75)
    assert set(hour) == C_HOUR_KEY_SET
    assert "wind" not in response.text.lower() and "gust" not in response.text


async def test_perseverance_hours_have_no_uv_keys_ac18(
    client: httpx.AsyncClient, base: Factory
) -> None:
    hours = (await detail(client, "perseverance", 100))["hours"]

    assert not any("uv" in key for hour in hours for key in hour)


async def test_perseverance_gust_is_present_inside_the_wind_window_ac18(
    client: httpx.AsyncClient, base: Factory
) -> None:
    hours = (await detail(client, "perseverance", 100))["hours"]  # 36 <= 100 <= 313

    with_wind = [h for h in hours if h["wind_speed_ms"] is not None]
    assert len(with_wind) >= 12
    for hour in with_wind:
        assert hour["wind_gust_ms"] is not None and 0 <= hour["wind_gust_ms"] <= 60
        assert hour["wind_direction_deg"] is not None
    for hour in hours:
        assert (hour["wind_gust_ms"] is None) == (hour["wind_speed_ms"] is None)


@pytest.mark.parametrize("sol", (400, 1230, 1852), ids=lambda s: f"sol-{s}")
async def test_perseverance_wind_keys_are_null_outside_sols_36_to_313_ac18(
    client: httpx.AsyncClient, base: Factory, sol: int
) -> None:
    body = await detail(client, "perseverance", sol)

    assert body["wind"] is None
    for hour in body["hours"]:
        assert {"wind_speed_ms", "wind_gust_ms", "wind_direction_deg"} <= set(hour)
        assert hour["wind_speed_ms"] is None
        assert hour["wind_gust_ms"] is None
        assert hour["wind_direction_deg"] is None


# --- AC-19: the 17G ground switch ----------------------------------------------------------------


async def test_flag_on_gives_the_ground_triple_in_every_hour_ac19(
    flag_on: httpx.AsyncClient,  # noqa: F811
    base: Factory,
) -> None:
    hours = (await detail(flag_on, "perseverance", 100))["hours"]

    assert all({"ground_temp_c", "ground_temp_c_min", "ground_temp_c_max"} <= set(h) for h in hours)
    assert any(h["ground_temp_c_min"] is not None for h in hours)


async def test_flag_off_hides_the_ground_mean_min_and_max_in_the_hours_ac19(
    flag_off: httpx.AsyncClient,  # noqa: F811
    base: Factory,
) -> None:
    response = await flag_off.get(f"{P}/100")

    assert response.status_code == 200
    hours = response.json()["hours"]
    assert not any(key.startswith("ground") for hour in hours for key in hour)
    assert "ground" not in response.text
    assert all(set(hour) == P_HOUR_KEY_SET - triples(("ground_temp_c",)) for hour in hours)


async def test_flag_off_keeps_the_other_extremes_and_the_gust_ac19(
    flag_off: httpx.AsyncClient,  # noqa: F811
    base: Factory,
) -> None:
    hours = (await detail(flag_off, "perseverance", 100))["hours"]

    assert any(h["air_temp_c_min"] is not None for h in hours)
    assert any(h["pressure_pa_max"] is not None for h in hours)
    assert any(h["wind_gust_ms"] is not None for h in hours)


async def test_flag_off_does_not_touch_curiosity_ground_extremes_ac19(
    flag_off: httpx.AsyncClient,  # noqa: F811
    base: Factory,
) -> None:
    hours = (await detail(flag_off, "curiosity", 30))["hours"]

    assert all({"ground_temp_c", "ground_temp_c_min", "ground_temp_c_max"} <= set(h) for h in hours)
    assert any(h["ground_temp_c_min"] is not None for h in hours)


# --- AC-20: the list is not changed by the stage -------------------------------------------------

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "golden"
FIXED_UPDATED_AT = "2026-11-25 00:00:00+00"


@pytest.mark.parametrize("rover", ["perseverance", "curiosity"])
async def test_list_body_is_byte_for_byte_the_golden_one_ac20(
    client: httpx.AsyncClient, base: Factory, rover: str
) -> None:
    """The list code did not change in stage 22: its body for the fixture base is frozen.

    Regenerate (only on a deliberate contract change) with `WEATHER_GOLDEN_WRITE=1`.
    """
    from sqlalchemy import text

    async with base() as session:  # `updated_at` of the answer is the parse time: fix it
        await session.execute(
            text("UPDATE weather_dataset SET updated_at = :t, changed_at = :t"),
            {"t": datetime.fromisoformat(FIXED_UPDATED_AT)},
        )
        await session.commit()
    response = await client.get(f"{V2}/rovers/{rover}/weather")
    golden = GOLDEN_DIR / f"weather_list_{rover}.json"
    if os.environ.get("WEATHER_GOLDEN_WRITE"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(response.content)

    assert response.status_code == 200
    assert response.content == golden.read_bytes()


def celsius(kelvin: float | None) -> float | None:
    return None if kelvin is None else round(kelvin - 273.15, 1)


@pytest.mark.parametrize("rover", ["perseverance", "curiosity"])
async def test_list_items_are_computed_from_the_daily_rows_only_ac20(
    client: httpx.AsyncClient, base: Factory, rover: str
) -> None:
    rows = await daily_rows(base, rover)
    response = await client.get(f"{V2}/rovers/{rover}/weather")

    items = response.json()["items"]
    assert [i["sol"] for i in items] == [r["sol"] for r in rows]
    for item, row in zip(items, rows, strict=True):
        assert not any(k.endswith(("_min", "_max")) or "gust" in k for k in item), item["sol"]
        assert "hours" not in item
        if row["air_mean_k"] is not None:
            assert item["air_temp_c"] == {
                "min": pytest.approx(celsius(row["air_min_k"]), abs=0.051),
                "max": pytest.approx(celsius(row["air_max_k"]), abs=0.051),
                "mean": pytest.approx(celsius(row["air_mean_k"]), abs=0.051),
            }
        if row["pressure_mean_pa"] is not None:
            assert item["pressure_pa"] == {
                "min": pytest.approx(row["pressure_min_pa"], abs=0.051),
                "max": pytest.approx(row["pressure_max_pa"], abs=0.051),
                "mean": pytest.approx(row["pressure_mean_pa"], abs=0.051),
            }
        assert item["flags"] == row["flags"]
        assert item["source_version"] == row["source_version"]


async def test_list_keys_are_the_same_as_before_the_stage_ac20(
    client: httpx.AsyncClient, base: Factory
) -> None:
    from tests.api.v2.test_weather import C_ITEM_KEYS, P_ITEM_KEYS

    perseverance = (await client.get(P)).json()["items"]
    curiosity = (await client.get(C)).json()["items"]

    assert all(list(item) == P_ITEM_KEYS for item in perseverance)
    assert all(list(item) == C_ITEM_KEYS for item in curiosity)


# --- errors keep their shape ----------------------------------------------------------------------


async def test_missing_sol_is_still_sol_not_found_ac15(
    client: httpx.AsyncClient, base: Factory
) -> None:
    response = await client.get(f"{P}/99999")

    assert_error(response, 404, "sol_not_found")


# --- the OpenAPI snapshot and the TypeScript types (AC-15) ----------------------------------------

ROOT = Path(__file__).resolve().parents[3]
ALWAYS = {"hour", "coverage"} | triples(("air_temp_c", "pressure_pa", "humidity_pct"))
PER_ROVER = triples(("ground_temp_c", "uv_wm2")) | {
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_direction_deg",
}


def check_hour_schema(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    hour = schemas["WeatherHour"]
    assert set(hour["properties"]) == ALWAYS | PER_ROVER
    required = set(hour.get("required", []))
    assert required >= ALWAYS
    assert not required & PER_ROVER  # keys that exist for one rover only are optional
    refs = json.dumps(schemas["WeatherSolDetail"])
    assert "WeatherHour" in refs
    for key in triples(("air_temp_c",)) | {"wind_gust_ms"}:
        assert hour["properties"][key].get("anyOf") or hour["properties"][key].get("type")


def test_openapi_weather_hour_has_the_min_max_and_gust_keys_ac15(app: Any) -> None:
    check_hour_schema(app.openapi())


def test_committed_openapi_has_the_same_weather_hour_as_the_live_one_ac15(app: Any) -> None:
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))

    check_hour_schema(committed)
    live = app.openapi()
    assert (
        committed["components"]["schemas"]["WeatherHour"]
        == (live["components"]["schemas"]["WeatherHour"])
    )
    path = "/api/v2/rovers/{rover}/weather/{sol}"
    assert committed["paths"][path] == live["paths"][path]


def test_examples_types_include_the_hourly_extremes_ac15() -> None:
    types = (ROOT / "examples" / "types.ts").read_text(encoding="utf-8")

    for key in ("air_temp_c_min", "pressure_pa_max", "humidity_pct_min", "wind_gust_ms"):
        assert key in types, key
