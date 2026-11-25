"""GET /api/v2/rovers/{rover}/weather and /weather/{sol} (docs/API.md `WeatherSol`, stage 17).

AC-25 ... AC-35 and AC-48. Rows are inserted straight into `weather_daily` / `weather_hourly` /
`weather_files` / `weather_dataset` in SI units as stored (K, Pa, %, W/m2, m/s); the expected JSON
is computed by hand from the contract: K -> degC, rounding (0.1 / 0.1 / 0.1 / 0.01 / 0.1, coverage
0.01), `sol ASC`, keys per rover, the v2 error envelope.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests._weather import (
    CREDIT_CURIOSITY,
    CREDIT_PERSEVERANCE,
    SOURCE_URL,
    Factory,
    insert_daily,
    insert_dataset,
    set_hour,
)
from tests.api.v2.conftest import V2, assert_error, assert_json

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

P = f"{V2}/rovers/perseverance/weather"
C = f"{V2}/rovers/curiosity/weather"
CACHE = "public, max-age=3600"
ROOT = Path(__file__).resolve().parents[3]
UPDATED = "2026-11-02T03:04:05+00:00"

TOP_KEYS = [
    "rover",
    "credit",
    "source_url",
    "updated_at",
    "source_max_sol",
    "parameters",
    "items",
]
P_ITEM_KEYS = [
    "sol",
    "earth_date",
    "ls",
    "coverage",
    "hours_covered",
    "air_temp_c",
    "pressure_pa",
    "humidity_pct",
    "wind",
    "ground_temp_c",
    "flags",
    "source_version",
]
C_ITEM_KEYS = [
    "sol",
    "earth_date",
    "ls",
    "coverage",
    "hours_covered",
    "air_temp_c",
    "pressure_pa",
    "humidity_pct",
    "ground_temp_c",
    "uv_wm2",
    "flags",
    "source_version",
]
P_HOUR_KEYS = [
    "hour",
    "coverage",
    "air_temp_c",
    "air_temp_c_min",
    "air_temp_c_max",
    "pressure_pa",
    "pressure_pa_min",
    "pressure_pa_max",
    "humidity_pct",
    "humidity_pct_min",
    "humidity_pct_max",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_direction_deg",
    "ground_temp_c",
    "ground_temp_c_min",
    "ground_temp_c_max",
]
C_HOUR_KEYS = [
    "hour",
    "coverage",
    "air_temp_c",
    "air_temp_c_min",
    "air_temp_c_max",
    "pressure_pa",
    "pressure_pa_min",
    "pressure_pa_max",
    "humidity_pct",
    "humidity_pct_min",
    "humidity_pct_max",
    "ground_temp_c",
    "ground_temp_c_min",
    "ground_temp_c_max",
    "uv_wm2",
    "uv_wm2_min",
    "uv_wm2_max",
]
# Stage 22: the hourly extremes and the gust are null in rows that were not read again (AC-15).
NO_EXTREMES = {k: None for k in P_HOUR_KEYS if k.endswith(("_min", "_max")) or k == "wind_gust_ms"}
C_NO_EXTREMES = {k: None for k in C_HOUR_KEYS if k.endswith(("_min", "_max"))}
P_PARAMETERS = {
    "air_temp_c": {"unit": "°C", "quality": "measured"},
    "pressure_pa": {"unit": "Pa", "quality": "measured"},
    "humidity_pct": {"unit": "%", "quality": "preliminary"},
    "wind": {"unit": "m/s", "quality": "measured"},
    "ground_temp_c": {"unit": "°C", "quality": "estimate"},
}
P100_FLAGS = [{"code": "humidity_over_100", "parameter": "humidity_pct", "count": 12}]
P100_VERSION = {"CAL_ATS": "P02", "CAL_WS": "P03", "DER_PS": "P01", "DER_RHS": "P01"}

P100: dict[str, Any] = {
    "earth_date": date(2021, 5, 31),
    "ls": 52.4,
    "coverage": 0.614,
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
    "wind_mean_ms": 3.44,
    "wind_gust_ms": 11.84,
    "wind_dir_mode_deg": 112.5,
    "n_wind": 500,
    "ground_min_k": 200.15,
    "ground_max_k": 283.15,
    "ground_mean_k": 243.15,
    "n_ground": 150,
    "flags": P100_FLAGS,
    "source_version": P100_VERSION,
}
P100_EXPECTED = {
    "sol": 100,
    "earth_date": "2021-05-31",
    "ls": 52.4,
    "coverage": 0.61,
    "hours_covered": 24,
    "air_temp_c": {"min": -82.4, "max": -18.9, "mean": -53.7},
    "pressure_pa": {"min": 708.3, "max": 745.1, "mean": 726.0},
    "humidity_pct": {"min": 0.0, "max": 14.6, "mean": 2.3},
    "wind": {"speed_mean_ms": 3.4, "gust_ms": 11.8, "direction_mode_deg": 112.5},
    "ground_temp_c": {"min": -73.0, "max": 10.0, "mean": -30.0},
    "flags": P100_FLAGS,
    "source_version": P100_VERSION,
}
C30: dict[str, Any] = {
    "earth_date": date(2012, 9, 5),
    "ls": 156.3,
    "coverage": 0.33,
    "hours_covered": 20,
    "air_min_k": 185.15,
    "air_max_k": 260.65,
    "air_mean_k": 225.65,
    "pressure_min_pa": 780.04,
    "pressure_max_pa": 810.06,
    "pressure_mean_pa": 796.0,
    "humidity_min_pct": 0.0,
    "humidity_max_pct": 33.0,
    "humidity_mean_pct": 8.04,
    "ground_min_k": 185.15,
    "ground_max_k": 280.65,
    "ground_mean_k": 230.65,
    "uv_max_wm2": 19.236,
    "uv_mean_wm2": 5.114,
    "n_ground": 100,
    "n_uv": 100,
    "wind_mean_ms": 9.0,  # must never leak into a Curiosity answer
    "wind_gust_ms": 9.0,
    "wind_dir_mode_deg": 90.0,
    "n_wind": 5,
    "flags": [{"code": "partial_coverage", "parameter": None, "count": None}],
    "source_version": {"RMD": "P1"},
}


async def seed(factory: Factory, rover: str, sol: int, **overrides: Any) -> None:
    """A daily row (+ 24 empty hourly rows) and the dataset row of the rover."""
    base = P100 if rover == "perseverance" else C30
    await insert_daily(factory, sol, rover, **{**base, **overrides})
    await insert_dataset(factory, rover, source_max_sol=sol, updated_at=UPDATED)


async def seed_perseverance(factory: Factory) -> None:
    await seed(factory, "perseverance", 100)
    await insert_dataset(factory, "perseverance", source_max_sol=100, updated_at=UPDATED)


# --- AC-25: the list of Perseverance -------------------------------------------------------------


async def test_list_returns_the_envelope_and_the_items_ascending_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 400, wind_mean_ms=None, wind_gust_ms=None)
    await seed(clean_db, "perseverance", 100)
    await seed(clean_db, "perseverance", 36)

    response = await client.get(P)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert list(body) == TOP_KEYS
    assert body["rover"] == "perseverance"
    assert body["credit"] == CREDIT_PERSEVERANCE
    assert body["source_url"] == SOURCE_URL["perseverance"]
    assert body["updated_at"] == "2026-11-02T03:04:05Z"
    assert body["source_max_sol"] == 400
    assert body["parameters"] == P_PARAMETERS
    assert list(body["parameters"]) == list(P_PARAMETERS)
    assert [item["sol"] for item in body["items"]] == [36, 100, 400]
    assert all(list(item) == P_ITEM_KEYS for item in body["items"])


async def test_list_item_matches_the_contract_example_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_perseverance(clean_db)

    items = (await client.get(P)).json()["items"]

    assert_json(items, [P100_EXPECTED])


async def test_list_has_the_ground_estimate_and_never_uv_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """Part 17G: Perseverance has `ground_temp_c` (an estimate); the UV sensor is Curiosity's."""
    await seed(clean_db, "perseverance", 100, uv_max_wm2=5.0, uv_mean_wm2=1.0, n_uv=10)

    response = await client.get(P)

    body = response.json()
    assert body["parameters"]["ground_temp_c"] == {"unit": "°C", "quality": "estimate"}
    assert "uv_wm2" not in body["parameters"]
    assert body["items"][0]["ground_temp_c"] == {"min": -73.0, "max": 10.0, "mean": -30.0}
    assert "uv_wm2" not in response.text


async def test_ground_estimate_is_null_for_a_sol_without_a_complete_tirs_session_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 1, ground_min_k=None, ground_max_k=None,
               ground_mean_k=None, n_ground=0)  # fmt: skip

    item = (await client.get(P)).json()["items"][0]

    assert "ground_temp_c" in item and item["ground_temp_c"] is None


async def test_list_flags_and_source_version_are_taken_as_stored_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 7, flags=[], source_version={"CAL_ATS": "P01"})

    item = (await client.get(P)).json()["items"][0]

    assert item["flags"] == []
    assert item["source_version"] == {"CAL_ATS": "P01"}


async def test_list_has_no_hours_and_no_source_files_ac25(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_perseverance(clean_db)

    item = (await client.get(P)).json()["items"][0]

    assert "hours" not in item and "source" not in item


async def test_rover_in_the_path_is_case_insensitive(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_perseverance(clean_db)

    response = await client.get(f"{V2}/rovers/PERSEVERANCE/weather")

    assert response.status_code == 200
    assert response.json()["rover"] == "perseverance"


# --- AC-26: Curiosity ----------------------------------------------------------------------------


async def test_curiosity_list_has_ground_and_uv_and_no_wind_anywhere_ac26(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)
    await seed(clean_db, "curiosity", 9)

    response = await client.get(C)

    body = response.json()
    assert list(body) == TOP_KEYS
    assert body["rover"] == "curiosity"
    assert body["credit"] == CREDIT_CURIOSITY
    assert body["source_url"].startswith(SOURCE_URL["curiosity"])  # base, maybe with DATA/
    assert list(body["parameters"]) == [
        "air_temp_c",
        "pressure_pa",
        "humidity_pct",
        "ground_temp_c",
        "uv_wm2",
    ]
    assert body["parameters"]["ground_temp_c"] == {"unit": "°C", "quality": "measured"}
    assert body["parameters"]["uv_wm2"]["quality"] == "measured"
    assert body["parameters"]["uv_wm2"]["unit"]
    assert body["parameters"]["humidity_pct"] == {"unit": "%", "quality": "preliminary"}
    assert [item["sol"] for item in body["items"]] == [9, 30]
    assert all(list(item) == C_ITEM_KEYS for item in body["items"])
    assert "wind" not in response.text.lower()  # no key in any form, also not in `parameters`


async def test_curiosity_item_values_are_converted_and_rounded_ac26(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)

    items = (await client.get(C)).json()["items"]

    assert_json(
        items,
        [
            {
                "sol": 30,
                "earth_date": "2012-09-05",
                "ls": 156.3,
                "coverage": 0.33,
                "hours_covered": 20,
                "air_temp_c": {"min": -88.0, "max": -12.5, "mean": -47.5},
                "pressure_pa": {"min": 780.0, "max": 810.1, "mean": 796.0},
                "humidity_pct": {"min": 0.0, "max": 33.0, "mean": 8.0},
                "ground_temp_c": {"min": -88.0, "max": 7.5, "mean": -42.5},
                "uv_wm2": {"max": 19.24, "mean": 5.11},
                "flags": [{"code": "partial_coverage", "parameter": None, "count": None}],
                "source_version": {"RMD": "P1"},
            }
        ],
    )


async def test_curiosity_ground_and_uv_are_null_for_a_sol_without_values_ac26(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(
        clean_db, "curiosity", 12, ground_min_k=None, ground_max_k=None, ground_mean_k=None,
        uv_max_wm2=None, uv_mean_wm2=None, n_ground=0, n_uv=0,
    )  # fmt: skip

    item = (await client.get(C)).json()["items"][0]

    assert item["ground_temp_c"] is None and item["uv_wm2"] is None
    assert "wind" not in item


# --- AC-27: wind of Perseverance -----------------------------------------------------------------


async def test_wind_is_null_outside_sols_36_to_313_and_an_object_inside_ac27(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)
    await seed(clean_db, "perseverance", 400, wind_mean_ms=None, wind_gust_ms=None,
               wind_dir_mode_deg=None, n_wind=0)  # fmt: skip

    items = {i["sol"]: i for i in (await client.get(P)).json()["items"]}

    assert items[400]["wind"] is None
    assert "wind" in items[400]
    assert list(items[100]["wind"]) == ["speed_mean_ms", "gust_ms", "direction_mode_deg"]
    assert items[100]["wind"] == {
        "speed_mean_ms": 3.4,
        "gust_ms": 11.8,
        "direction_mode_deg": 112.5,
    }


# --- AC-28: units and rounding -------------------------------------------------------------------


async def test_units_and_rounding_follow_the_contract_ac28(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(
        clean_db, "perseverance", 100,
        air_min_k=220.15, air_max_k=273.14, air_mean_k=273.15,
        pressure_min_pa=726.04, pressure_max_pa=726.06, pressure_mean_pa=726.049,
        humidity_min_pct=0.04, humidity_max_pct=14.64, humidity_mean_pct=2.26,
        wind_mean_ms=3.44, wind_gust_ms=3.46, wind_dir_mode_deg=112.5, coverage=0.614,
    )  # fmt: skip
    response = await client.get(P)

    item = response.json()["items"][0]

    assert item["air_temp_c"] == {"min": -53.0, "max": 0.0, "mean": 0.0}
    assert item["pressure_pa"] == {"min": 726.0, "max": 726.1, "mean": 726.0}
    assert item["humidity_pct"] == {"min": 0.0, "max": 14.6, "mean": 2.3}
    assert item["wind"] == {"speed_mean_ms": 3.4, "gust_ms": 3.5, "direction_mode_deg": 112.5}
    assert item["coverage"] == 0.61
    assert "-0.0" not in response.text  # 273.14 K is -0.01 degC: printed as 0.0


async def test_uv_is_rounded_to_hundredths_and_other_values_to_tenths_ac28(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30, uv_max_wm2=19.236, uv_mean_wm2=5.114,
               ground_min_k=273.149, ground_max_k=273.151, ground_mean_k=200.0)  # fmt: skip

    item = (await client.get(C)).json()["items"][0]

    assert item["uv_wm2"] == {"max": 19.24, "mean": 5.11}
    assert item["ground_temp_c"]["min"] == 0.0 and item["ground_temp_c"]["max"] == 0.0


@pytest.mark.parametrize(
    ("kelvin", "celsius"),
    [(273.15, 0.0), (273.14, 0.0), (273.16, 0.0), (273.25, 0.1)],
    ids=["zero", "slightly-below", "slightly-above", "tenth"],
)
async def test_celsius_never_prints_negative_zero_ac28(
    client: httpx.AsyncClient, clean_db: Factory, kelvin: float, celsius: float
) -> None:
    await seed(clean_db, "perseverance", 100, air_mean_k=kelvin)

    response = await client.get(P)

    assert response.json()["items"][0]["air_temp_c"]["mean"] == celsius
    assert "-0.0," not in response.text and "-0.0}" not in response.text


# --- AC-29: sol range ----------------------------------------------------------------------------


@pytest.fixture
async def spread(clean_db: Factory) -> Factory:
    for sol in (5, 10, 15, 20, 25):
        await seed(clean_db, "perseverance", sol)
    return clean_db


@pytest.mark.parametrize(
    ("params", "sols"),
    [
        ({"sol_min": 10, "sol_max": 20}, [10, 15, 20]),
        ({"sol_min": 11}, [15, 20, 25]),
        ({"sol_max": 14}, [5, 10]),
        ({"sol_min": 15, "sol_max": 15}, [15]),
        ({"sol_min": 0}, [5, 10, 15, 20, 25]),
        ({"sol_min": 26}, []),
        ({"sol_min": 16, "sol_max": 19}, []),
        ({"sol_min": 10, "sol_max": 20, "unknown": "x", "page": 3}, [10, 15, 20]),
    ],
    ids=["both", "min-only", "max-only", "single", "zero", "above-all", "gap", "unknown-ignored"],
)
async def test_sol_range_selects_the_sols_inclusively_ac29(
    client: httpx.AsyncClient, spread: Factory, params: dict[str, Any], sols: list[int]
) -> None:
    response = await client.get(P, params=params)

    assert response.status_code == 200
    body = response.json()
    assert [item["sol"] for item in body["items"]] == sols
    assert body["parameters"] == P_PARAMETERS  # the header does not depend on the range
    assert body["source_max_sol"] == 25


@pytest.mark.parametrize(
    "query",
    [
        "sol_min=20&sol_max=10",
        "sol_min=-1",
        "sol_min=abc",
        "sol_max=1.5",
        "sol_max=-5",
        "sol_max=2147483648",
        "sol_min=",
        "sol_min=1&sol_min=2",
        "sol_max=1&sol_max=2",
    ],
    ids=[
        "min-over-max",
        "negative-min",
        "not-a-number",
        "fraction",
        "negative-max",
        "over-int32",
        "empty",
        "min-twice",
        "max-twice",
    ],
)
async def test_invalid_range_is_a_validation_error_ac29(
    client: httpx.AsyncClient, spread: Factory, query: str
) -> None:
    response = await client.get(f"{P}?{query}")

    assert_error(response, 422, "validation_error")


# --- AC-30: other rovers, empty base -------------------------------------------------------------


@pytest.mark.parametrize("rover", ["opportunity", "spirit", "ingenuity"])
async def test_rovers_without_weather_are_not_found_ac30(
    client: httpx.AsyncClient, rover: str
) -> None:
    assert_error(await client.get(f"{V2}/rovers/{rover}/weather"), 404, "not_found")
    assert_error(await client.get(f"{V2}/rovers/{rover}/weather/5"), 404, "not_found")


async def test_unknown_rover_is_rover_not_found_ac30(client: httpx.AsyncClient) -> None:
    assert_error(await client.get(f"{V2}/rovers/pluto/weather"), 404, "rover_not_found")
    assert_error(await client.get(f"{V2}/rovers/pluto/weather/5"), 404, "rover_not_found")


@pytest.mark.parametrize(
    ("rover", "credit", "parameters"),
    [
        ("perseverance", CREDIT_PERSEVERANCE, list(P_PARAMETERS)),
        (
            "curiosity",
            CREDIT_CURIOSITY,
            ["air_temp_c", "pressure_pa", "humidity_pct", "ground_temp_c", "uv_wm2"],
        ),
    ],
)
async def test_empty_base_gives_an_empty_list_with_null_dates_ac30(
    client: httpx.AsyncClient, rover: str, credit: str, parameters: list[str]
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/weather")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == TOP_KEYS
    assert body["items"] == []
    assert body["updated_at"] is None and body["source_max_sol"] is None
    assert body["credit"] == credit
    assert body["source_url"].startswith(SOURCE_URL[rover])
    assert list(body["parameters"]) == parameters


async def test_data_of_one_rover_does_not_show_in_the_other_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    body = (await client.get(C)).json()

    assert body["items"] == [] and body["source_max_sol"] is None


# --- AC-31 / AC-32: one sol with its hours -------------------------------------------------------


async def seed_p100_hours(factory: Factory) -> None:
    await seed(factory, "perseverance", 100)
    await set_hour(factory, 100, 0, coverage=0.5, air_k=201.95, pressure_pa=735.44, humidity_pct=5.84,  # noqa: E501
                   wind_ms=2.14, wind_dir_deg=157.5, ground_k=263.15)  # fmt: skip
    await set_hour(factory, 100, 12, coverage=0.17, air_k=250.15, pressure_pa=720.0,
                   humidity_pct=0.0, wind_ms=None, wind_dir_deg=None)  # fmt: skip


async def test_detail_has_the_sol_fields_and_24_hours_ac31(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_p100_hours(clean_db)

    response = await client.get(f"{P}/100")

    assert response.status_code == 200
    body = response.json()
    assert list(body)[:1] == ["rover"] and list(body)[-2:] == ["hours", "source"]
    assert list(body) == ["rover", *P_ITEM_KEYS, "hours", "source"]
    assert body["rover"] == "perseverance"
    for key, value in P100_EXPECTED.items():
        assert body[key] == value, key
    hours = body["hours"]
    assert [h["hour"] for h in hours] == list(range(24))
    assert all(list(h) == P_HOUR_KEYS for h in hours)
    assert hours[0] == {
        "hour": 0,
        "coverage": 0.5,
        "air_temp_c": -71.2,
        "pressure_pa": 735.4,
        "humidity_pct": 5.8,
        "wind_speed_ms": 2.1,
        "wind_direction_deg": 157.5,
        "ground_temp_c": -10.0,
        **NO_EXTREMES,
    }
    assert hours[12]["air_temp_c"] == -23.0
    assert hours[12]["wind_speed_ms"] is None and hours[12]["wind_direction_deg"] is None


async def test_hours_without_data_have_null_values_and_zero_coverage_ac31(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_p100_hours(clean_db)

    hours = (await client.get(f"{P}/100")).json()["hours"]

    assert hours[1] == {
        "hour": 1,
        "coverage": 0.0,
        "air_temp_c": None,
        "pressure_pa": None,
        "humidity_pct": None,
        "wind_speed_ms": None,
        "wind_direction_deg": None,
        "ground_temp_c": None,
        **NO_EXTREMES,
    }
    assert all(h["coverage"] == 0 and h["air_temp_c"] is None for h in hours[13:])


async def test_detail_of_a_sol_with_null_wind_keeps_the_hour_wind_keys_ac31(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 400, wind_mean_ms=None, wind_gust_ms=None,
               wind_dir_mode_deg=None, n_wind=0)  # fmt: skip

    body = (await client.get(f"{P}/400")).json()

    assert body["wind"] is None
    assert all(
        h["wind_speed_ms"] is None and h["wind_direction_deg"] is None for h in body["hours"]
    )
    assert all(list(h) == P_HOUR_KEYS for h in body["hours"])


async def test_curiosity_hours_have_ground_and_uv_and_no_wind_ac32(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)
    await set_hour(clean_db, 30, 3, "curiosity", coverage=0.67, air_k=190.15, pressure_pa=790.04,
                   humidity_pct=12.0, ground_k=185.65, uv_wm2=0.0, wind_ms=7.0, wind_dir_deg=10.0)  # fmt: skip  # noqa: E501

    response = await client.get(f"{C}/30")

    body = response.json()
    assert list(body) == ["rover", *C_ITEM_KEYS, "hours", "source"]
    assert all(list(h) == C_HOUR_KEYS for h in body["hours"])
    assert body["hours"][3] == {
        "hour": 3,
        "coverage": 0.67,
        "air_temp_c": -83.0,
        "pressure_pa": 790.0,
        "humidity_pct": 12.0,
        "ground_temp_c": -87.5,
        "uv_wm2": 0.0,
        **C_NO_EXTREMES,
    }
    assert "wind" not in response.text.lower()


async def insert_file(factory: Factory, rover: str, sol: int, path: str, product: str,
                      version: str, status: str = "parsed") -> None:  # fmt: skip
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO weather_files (rover_id, path, sol, product, version, size_bytes, "
                "mtime_ns, status, parsed_at) VALUES (:r, :p, :s, :pr, :v, 1000, 1, :st, now())"
            ),
            {"r": rover, "p": path, "s": sol, "pr": product, "v": version, "st": status},
        )
        await session.commit()


async def test_detail_lists_the_source_files_with_links_ac32(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)
    ats = "data_calibrated_env/sol_0090_0179/sol_0100/WE__0100___________CAL_ATS_________________P02.CSV"  # noqa: E501
    ps = (
        "data_derived_env/sol_0090_0179/sol_0100/WE__0100___________DER_PS__________________P01.CSV"
    )
    old = "data_calibrated_env/sol_0090_0179/sol_0100/WE__0100___________CAL_ATS_________________P01.CSV"  # noqa: E501
    await insert_file(clean_db, "perseverance", 100, ats, "CAL_ATS", "P02")
    await insert_file(clean_db, "perseverance", 100, ps, "DER_PS", "P01")
    await insert_file(clean_db, "perseverance", 100, old, "CAL_ATS", "P01", "superseded")
    await insert_file(clean_db, "perseverance", 101, ps.replace("0100", "0101"), "DER_PS", "P01")

    files = (await client.get(f"{P}/100")).json()["source"]["files"]

    base = SOURCE_URL["perseverance"]
    assert sorted(files, key=lambda f: f["product"]) == [
        {"product": "CAL_ATS", "version": "P02", "file": ats, "link": base + ats},
        {"product": "DER_PS", "version": "P01", "file": ps, "link": base + ps},
    ]
    assert all(list(f) == ["product", "version", "file", "link"] for f in files)


async def test_curiosity_detail_links_point_to_the_rems_volume_ac32(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)
    tab = "DATA/SOL_00001_00089/SOL00030/RME_400109702RMD00300000000_______P1.TAB"
    await insert_file(clean_db, "curiosity", 30, tab, "RMD", "P1")

    files = (await client.get(f"{C}/30")).json()["source"]["files"]

    assert files == [
        {"product": "RMD", "version": "P1", "file": tab, "link": SOURCE_URL["curiosity"] + tab}
    ]


async def test_detail_without_file_rows_has_an_empty_file_list_ac32(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    assert (await client.get(f"{P}/100")).json()["source"] == {"files": []}


# --- AC-33: errors of the detail -----------------------------------------------------------------


async def test_sol_without_data_is_sol_not_found_ac33(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    error = assert_error(await client.get(f"{P}/5"), 404, "sol_not_found")

    assert "5" in error["message"] or error["message"]


async def test_sol_of_another_rover_is_sol_not_found_ac33(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    assert_error(await client.get(f"{C}/100"), 404, "sol_not_found")


@pytest.mark.parametrize("sol", ["abc", "-1", "2147483648", "1.5", "1e3"])
async def test_invalid_sol_is_a_validation_error_ac33(client: httpx.AsyncClient, sol: str) -> None:
    response = await client.get(f"{P}/{sol}")

    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize("sol", [0, 2147483647])
async def test_sol_bounds_are_valid_numbers_ac33(client: httpx.AsyncClient, sol: int) -> None:
    assert_error(await client.get(f"{P}/{sol}"), 404, "sol_not_found")  # valid, just no data


async def test_sol_zero_with_data_is_found_ac33(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 0)

    assert (await client.get(f"{P}/0")).json()["sol"] == 0


# --- AC-34: caching ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/rovers/perseverance/weather", "/rovers/perseverance/weather/100"]
)
async def test_success_is_cacheable_for_an_hour_with_an_etag_ac34(
    client: httpx.AsyncClient, clean_db: Factory, path: str
) -> None:
    await seed(clean_db, "perseverance", 100)

    response = await client.get(f"{V2}{path}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE
    assert response.headers["etag"]


@pytest.mark.parametrize(
    "path", ["/rovers/perseverance/weather", "/rovers/perseverance/weather/100"]
)
async def test_if_none_match_gets_304_ac34(
    client: httpx.AsyncClient, clean_db: Factory, path: str
) -> None:
    await seed(clean_db, "perseverance", 100)
    first = await client.get(f"{V2}{path}")

    again = await client.get(f"{V2}{path}", headers={"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


async def test_empty_list_is_cacheable_too_ac34(client: httpx.AsyncClient) -> None:
    assert (await client.get(P)).headers["cache-control"] == CACHE


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/rovers/opportunity/weather", 404),
        ("/rovers/pluto/weather", 404),
        ("/rovers/perseverance/weather/5", 404),
        ("/rovers/perseverance/weather/abc", 422),
        ("/rovers/perseverance/weather?sol_min=abc", 422),
        ("/rovers/perseverance/weather?sol_min=9&sol_max=1", 422),
    ],
    ids=["no-weather", "unknown-rover", "sol-not-found", "bad-sol", "bad-range", "min-over-max"],
)
async def test_errors_are_not_cached_ac34(
    client: httpx.AsyncClient, path: str, status: int
) -> None:
    response = await client.get(f"{V2}{path}")

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


async def test_repeated_list_request_returns_the_identical_body_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    first = await client.get(P)
    second = await client.get(P)

    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]


async def test_list_changes_after_the_dataset_fingerprint_changes_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)
    first = await client.get(P)
    assert [i["sol"] for i in first.json()["items"]] == [100]

    await seed(clean_db, "perseverance", 101)
    async with clean_db() as session:
        await session.execute(
            text("UPDATE weather_dataset SET updated_at = '2026-12-24T10:00:00+00:00'")
        )
        await session.commit()
    second = await client.get(P)

    assert [i["sol"] for i in second.json()["items"]] == [100, 101]
    assert second.json()["updated_at"] == "2026-12-24T10:00:00Z"
    assert second.json()["source_max_sol"] == 101
    assert second.headers["etag"] != first.headers["etag"]


async def test_list_changes_after_a_weather_import_ac34(
    client: httpx.AsyncClient, clean_db: Factory, tmp_path: Path, weather_env: Any
) -> None:
    """Warm answer with one sol, then `weather import` of a file with two: the body is new."""
    from tests._weather import full_meda_sol, run_weather

    raw = weather_env()
    for sol in (1, 2):
        full_meda_sol(raw, sol)
    assert await run_weather("parse", "--rover", "perseverance") == 0
    target = tmp_path / "w.jsonl.gz"
    assert await run_weather("export", "--output", str(target)) == 0
    async with clean_db() as session:
        await session.execute(text("DELETE FROM weather_daily WHERE sol = 2"))
        await session.commit()
    warm = await client.get(P)
    assert [i["sol"] for i in warm.json()["items"]] == [1]
    async with clean_db() as session:  # the dataset row of the base is older than the file's
        await session.execute(
            text("UPDATE weather_dataset SET updated_at = '2026-01-01T00:00:00+00:00'")
        )
        await session.commit()

    assert await run_weather("import", "--input", str(target), "--force") == 0

    fresh = await client.get(P)
    assert [i["sol"] for i in fresh.json()["items"]] == [1, 2]
    assert fresh.headers["etag"] != warm.headers["etag"]


async def test_head_has_the_same_headers_and_no_body_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)
    get = await client.get(P)

    head = await client.head(P)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == CACHE
    assert head.headers["etag"] == get.headers["etag"]


# --- AC-35: flags --------------------------------------------------------------------------------


async def test_flags_are_returned_with_code_parameter_and_count_ac35(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    flags = [
        {"code": "partial_coverage", "parameter": None, "count": None},
        {"code": "humidity_over_100", "parameter": "humidity_pct", "count": 1601},
        {"code": "out_of_range", "parameter": "air_temp_c", "count": 3},
        {"code": "out_of_range", "parameter": "pressure_pa", "count": 1},
    ]
    await seed(clean_db, "perseverance", 1852, humidity_max_pct=183.76, flags=flags)

    item = (await client.get(P)).json()["items"][0]

    assert item["flags"] == flags
    assert [list(f) for f in item["flags"]] == [["code", "parameter", "count"]] * 4
    assert item["humidity_pct"]["max"] == 183.8  # above 100: printed as it is, not clipped


async def test_a_sol_without_remarks_has_an_empty_flag_list_ac35(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 7, flags=[])

    assert (await client.get(P)).json()["items"][0]["flags"] == []


async def test_flags_are_the_same_in_the_list_and_in_the_detail_ac35(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "perseverance", 100)

    listed = (await client.get(P)).json()["items"][0]["flags"]
    detail = (await client.get(f"{P}/100")).json()["flags"]

    assert listed == detail == P100_FLAGS


# --- AC-36: OpenAPI ------------------------------------------------------------------------------

OPTIONAL = ("wind", "ground_temp_c", "uv_wm2")


def _check_schema(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    sol = schemas["WeatherSol"]
    for key in OPTIONAL:
        assert key in sol["properties"], key
        assert key not in sol.get("required", []), key
    required = set(sol.get("required", []))
    assert {"sol", "earth_date", "coverage", "hours_covered", "air_temp_c", "flags"} <= required
    descriptions = {k: json.dumps(sol["properties"][k]).lower() for k in OPTIONAL}
    assert "perseverance" in descriptions["wind"]
    assert "curiosity" in descriptions["ground_temp_c"]
    assert "curiosity" in descriptions["uv_wm2"]
    assert {"WeatherResponse", "WeatherSolDetail"} <= set(schemas)
    detail = schemas["WeatherSolDetail"]
    props = set(detail.get("properties", {}))
    for part in detail.get("allOf", []):
        ref = part.get("$ref", "").rsplit("/", 1)[-1]
        props |= set(schemas.get(ref, {}).get("properties", {}))
    assert {"rover", "hours", "source", "sol"} <= props


def test_openapi_has_the_weather_routes_and_optional_keys_ac36(app: Any) -> None:
    document = app.openapi()

    for path in ("/api/v2/rovers/{rover}/weather", "/api/v2/rovers/{rover}/weather/{sol}"):
        operation = document["paths"][path]["get"]
        assert {"200", "404", "422"} <= set(operation["responses"])
    names = {
        p["name"] for p in document["paths"]["/api/v2/rovers/{rover}/weather"]["get"]["parameters"]
    }
    assert {"rover", "sol_min", "sol_max"} <= names
    _check_schema(document)


def test_committed_openapi_equals_the_live_one_for_weather_ac36(app: Any) -> None:
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    live = app.openapi()

    _check_schema(committed)
    for path in ("/api/v2/rovers/{rover}/weather", "/api/v2/rovers/{rover}/weather/{sol}"):
        assert committed["paths"][path] == live["paths"][path]
    for name in ("WeatherSol", "WeatherSolDetail", "WeatherResponse", "WeatherFlag"):
        assert committed["components"]["schemas"][name] == live["components"]["schemas"][name]


def test_examples_types_include_the_weather_types_ac36() -> None:
    types = (ROOT / "examples" / "types.ts").read_text(encoding="utf-8")

    assert "WeatherSol" in types and "WeatherResponse" in types


async def test_v1_weather_path_is_not_found(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/rovers/curiosity/weather")).status_code == 404
