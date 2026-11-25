"""GET /api/v2/rovers/{rover}/radiation/{sol} (docs/API.md, stage 22, phase C).

Spec: docs/proposals/sol-conditions.md, AC-38 ... AC-40, AC-47. Rows are inserted straight into
`rad_daily` / `rad_hourly` (doses in uGy/h as stored); expected JSON is computed by hand from the
contract: dose to 0.01, count rate to 0.001, `coverage` = live time over 3698.97 s capped at 1,
rounded to 0.01.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._rad import CREDIT_RAD, Factory, insert_daily, insert_hours
from tests.api.v2.conftest import V2, assert_error
from tests.api.v2.test_radiation import CITATION, EXPECTED_4843, ITEM_KEYS, seed_4843

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

ROOT = Path(__file__).resolve().parents[3]
LIST_URL = f"{V2}/rovers/curiosity/radiation"
CACHE = "public, max-age=3600"
HOUR_KEYS = [
    "hour",
    "coverage",
    "observations",
    "dose_e_ugy_h",
    "dose_b_ugy_h",
    "count_rate_b_per_s",
]


def url(sol: int | str, rover: str = "curiosity") -> str:
    return f"{V2}/rovers/{rover}/radiation/{sol}"


def empty_hour(hour: int) -> dict[str, Any]:
    return {
        "hour": hour,
        "coverage": 0.0,
        "observations": 0,
        "dose_e_ugy_h": None,
        "dose_b_ugy_h": None,
        "count_rate_b_per_s": None,
    }


# Fixture 4843, hour 0: observations 0 and 2, 930 s each.
HOUR_0_4843 = {
    "hour": 0,
    "n_obs": 2,
    "live_s": 1860.0,
    "dose_b_mean": (6.84128 + 6.78671) / 2,
    "dose_e_mean": (6.58689 + 6.53178) / 2,
    "bu_count": 6454 + 6487,
}
EXPECTED_HOUR_0_4843 = {
    "hour": 0,
    "coverage": 0.5,  # 1860 / 3698.97 = 0.5028
    "observations": 2,
    "dose_e_ugy_h": 6.56,  # 6.559335
    "dose_b_ugy_h": 6.81,  # 6.813995
    "count_rate_b_per_s": 6.958,  # 12941 / 1860 = 6.95753
}


async def seed_hours_4843(factory: Factory) -> None:
    """The fixture base: sol 4843 with `hourly_revision` 1, `hourly_dropped` 1 and hour 0."""
    await seed_4843(factory, hourly_revision=1, hourly_dropped=1)
    await insert_hours(factory, 4843, [HOUR_0_4843])


# --- AC-38: the answer ---------------------------------------------------------------------------


async def test_radiation_sol_returns_24_hours_and_the_fields_of_the_list_item_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-38: fields of RadiationSol equal the list item; `hours` has 24 elements."""
    await seed_hours_4843(clean_db)

    response = await client.get(url(4843))
    listing = (await client.get(LIST_URL)).json()["items"][0]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert listing == EXPECTED_4843  # the list is what it was
    for key in ITEM_KEYS:
        assert body[key] == listing[key], key
    assert body["rover"] == "curiosity"
    assert body["credit"] == CREDIT_RAD
    assert body["citation"] == CITATION
    assert len(body["hours"]) == 24
    assert [hour["hour"] for hour in body["hours"]] == list(range(24))


async def test_radiation_sol_hour_0_has_two_observations_and_rounded_values_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-38: hour 0 -> observations 2 (obs 0 and 2); dose to 0.01, rate to 0.001, coverage 0.01."""
    await seed_hours_4843(clean_db)

    body = (await client.get(url(4843))).json()

    assert body["hours"][0] == EXPECTED_HOUR_0_4843
    assert list(body["hours"][0]) == HOUR_KEYS


async def test_radiation_sol_other_hours_are_empty_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-38: the other 23 hours have `coverage` 0, `observations` 0 and `null` values."""
    await seed_hours_4843(clean_db)

    hours = (await client.get(url(4843))).json()["hours"]

    assert hours[1:] == [empty_hour(h) for h in range(1, 24)]


async def test_radiation_sol_numbers_are_json_numbers_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_hours_4843(clean_db)

    hour = (await client.get(url(4843))).json()["hours"][0]

    assert isinstance(hour["hour"], int) and isinstance(hour["observations"], int)
    for key in ("coverage", "dose_e_ugy_h", "dose_b_ugy_h", "count_rate_b_per_s"):
        assert isinstance(hour[key], float), key


@pytest.mark.parametrize(
    ("live_s", "coverage"),
    [
        (3698.97, 1.0),
        (5000.0, 1.0),
        (3600.0, 0.97),
        (1849.485, 0.5),
        (369.897, 0.1),
        (10.0, 0.0),
    ],
    ids=["full-hour", "over-an-hour", "97-percent", "half", "tenth", "tiny"],
)
async def test_radiation_sol_coverage_is_live_time_over_an_lmst_hour_capped_at_one_ac38(
    client: httpx.AsyncClient, clean_db: Factory, live_s: float, coverage: float
) -> None:
    await insert_daily(clean_db, 100, hourly_revision=1)
    await insert_hours(
        clean_db,
        100,
        [
            {
                "hour": 5,
                "n_obs": 1,
                "live_s": live_s,
                "dose_b_mean": 7.0,
                "dose_e_mean": 6.0,
                "bu_count": 7000,
            }
        ],
    )

    hour = (await client.get(url(100))).json()["hours"][5]

    assert hour["coverage"] == coverage
    assert hour["observations"] == 1


@pytest.mark.parametrize(
    ("dose_b", "dose_e", "bu", "live", "expected"),
    [
        (12.3456, 1.0049, 7200, 900.0, (12.35, 1.0, 8.0)),
        (6.5, 6.0, 1, 3.0, (6.5, 6.0, 0.333)),
        (99.994, 0.0, 0, 900.0, (99.99, 0.0, 0.0)),
        (6.0, 5.0, 12345, 1500.0, (6.0, 5.0, 8.23)),
    ],
    ids=["two-places", "third", "zero-rate", "rate-two-places"],
)
async def test_radiation_sol_rounding_of_hour_values_ac38(
    client: httpx.AsyncClient,
    clean_db: Factory,
    dose_b: float,
    dose_e: float,
    bu: int,
    live: float,
    expected: tuple[float, float, float],
) -> None:
    """Dose to 0.01, count rate = bu_count / live_s to 0.001; the doses stay uGy/h (no x24)."""
    await insert_daily(clean_db, 100, hourly_revision=1)
    await insert_hours(
        clean_db,
        100,
        [
            {
                "hour": 7,
                "n_obs": 1,
                "live_s": live,
                "dose_b_mean": dose_b,
                "dose_e_mean": dose_e,
                "bu_count": bu,
            }
        ],
    )

    hour = (await client.get(url(100))).json()["hours"][7]

    assert (hour["dose_b_ugy_h"], hour["dose_e_ugy_h"], hour["count_rate_b_per_s"]) == expected


async def test_radiation_sol_hours_of_a_sol_with_many_hours_are_in_order_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 100, hourly_revision=1)
    rows = [
        {
            "hour": h,
            "n_obs": 4,
            "live_s": 3600.0,
            "dose_b_mean": 7.0 + h / 100,
            "dose_e_mean": 6.0,
            "bu_count": 28800,
        }
        for h in (23, 0, 12)  # inserted out of order
    ]
    await insert_hours(clean_db, 100, rows)

    hours = (await client.get(url(100))).json()["hours"]

    assert [h["hour"] for h in hours] == list(range(24))
    assert [h["observations"] for h in hours if h["observations"]] == [4, 4, 4]
    assert hours[12]["dose_b_ugy_h"] == 7.12
    assert hours[23]["dose_b_ugy_h"] == 7.23


async def test_radiation_sol_carries_the_timing_flag_and_the_source_of_the_sol_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10, timing_caveat=True, hourly_revision=1)

    body = (await client.get(url(10))).json()

    assert body["timing_caveat"] is True
    assert body["source"]["file"].startswith("DATA/SOL_")
    assert body["source"]["link"].startswith("https://pds-ppi.igpp.ucla.edu/data/")


async def test_radiation_sol_zero_is_a_valid_sol_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 0, hourly_revision=1)

    response = await client.get(url(0))

    assert response.status_code == 200
    assert response.json()["sol"] == 0
    assert len(response.json()["hours"]) == 24


async def test_radiation_sol_rover_id_is_case_insensitive_ac38(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 100, hourly_revision=1)

    response = await client.get(url(100, "CURIOSITY"))

    assert response.status_code == 200 and response.json()["rover"] == "curiosity"


# --- AC-39: errors -------------------------------------------------------------------------------


async def test_radiation_sol_without_a_summary_is_sol_not_found_ac39(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-39: `/radiation/5000` -> `404 sol_not_found`."""
    await seed_hours_4843(clean_db)

    assert_error(await client.get(url(5000)), 404, "sol_not_found")


async def test_radiation_sol_max_sol_without_a_summary_is_sol_not_found_ac39(
    client: httpx.AsyncClient,
) -> None:
    """2147483647 is a valid number: a missing sol, not a validation error."""
    assert_error(await client.get(url(2147483647)), 404, "sol_not_found")


@pytest.mark.parametrize("rover", ["perseverance", "opportunity", "spirit", "ingenuity"])
async def test_radiation_sol_rover_without_radiation_is_not_found_ac39(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    """AC-39: `/rovers/perseverance/radiation/1` -> `404 not_found`."""
    await insert_daily(clean_db, 1, hourly_revision=1)

    assert_error(await client.get(url(1, rover)), 404, "not_found")


async def test_radiation_sol_unknown_rover_is_rover_not_found_ac39(
    client: httpx.AsyncClient,
) -> None:
    """AC-39: `/rovers/foo/radiation/1` -> `404 rover_not_found`."""
    assert_error(await client.get(url(1, "foo")), 404, "rover_not_found")


@pytest.mark.parametrize(
    "sol",
    ["-1", "2147483648", "abc", "1.5", "99999999999", "0x10"],
    ids=["negative", "over-int32", "letters", "fraction", "huge", "hex"],
)
async def test_radiation_sol_invalid_sol_is_a_validation_error_ac39(
    client: httpx.AsyncClient, clean_db: Factory, sol: str
) -> None:
    """AC-39: sol `-1`, `2147483648`, `abc` -> `422 validation_error`."""
    await seed_hours_4843(clean_db)

    assert_error(await client.get(url(sol)), 422, "validation_error")


@pytest.mark.parametrize(
    "path",
    [
        f"{V2}/rovers/perseverance/radiation/1",
        f"{V2}/rovers/foo/radiation/1",
        f"{V2}/rovers/curiosity/radiation/5000",
        f"{V2}/rovers/curiosity/radiation/abc",
    ],
    ids=["no-radiation", "unknown-rover", "sol-not-found", "validation"],
)
async def test_radiation_sol_errors_are_not_cached_ac47(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path)

    assert response.status_code in (404, 422)
    assert response.headers["cache-control"] == "no-store"


# --- AC-40: the three states of `hours` ----------------------------------------------------------


async def test_radiation_sol_without_hours_has_null_hours_ac40(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-40: `hourly_revision` 0 (not calculated yet) -> `200`, `hours: null`, the day is there."""
    await seed_4843(clean_db)  # revision 0 by default

    response = await client.get(url(4843))

    assert response.status_code == 200
    body = response.json()
    assert body["hours"] is None
    for key in ITEM_KEYS:
        assert key in body
    assert body["observations"] == EXPECTED_4843["observations"]
    assert body["credit"] == CREDIT_RAD and body["citation"] == CITATION


@pytest.mark.parametrize("reason", ["overlap", "off_sol"])
async def test_radiation_sol_with_a_rejected_time_has_null_hours_ac40(
    client: httpx.AsyncClient, clean_db: Factory, reason: str
) -> None:
    """AC-40: `hourly_revision` 1 and `hourly_reject` set (ADR-7) -> `200`, `hours: null`."""
    await seed_4843(clean_db, hourly_revision=1, hourly_reject=reason, hourly_dropped=3)

    response = await client.get(url(4843))

    assert response.status_code == 200
    body = response.json()
    assert body["hours"] is None
    assert body["sol"] == 4843 and body["dose_b_ugy_day"]["mean"] == 164.8


async def test_radiation_sol_calculated_but_without_hour_rows_has_24_empty_hours_ac40(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-40: `hourly_revision` 1, no reject, no rows -> 24 empty hours (not `null`)."""
    await seed_4843(clean_db, hourly_revision=1, hourly_dropped=3)

    body = (await client.get(url(4843))).json()

    assert body["hours"] == [empty_hour(h) for h in range(24)]


async def test_radiation_sol_hours_are_null_for_a_rejected_sol_even_with_stale_rows_ac40(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """A rejected sol has no hours: stray rows are not shown (ADR-7: no half pictures)."""
    await seed_4843(clean_db, hourly_revision=1, hourly_reject="overlap", hourly_dropped=3)
    await insert_hours(clean_db, 4843, [HOUR_0_4843])

    assert (await client.get(url(4843))).json()["hours"] is None


# --- AC-47: cache and OpenAPI --------------------------------------------------------------------


@pytest.mark.parametrize("state", ["hours", "null-hours", "empty-hours"])
async def test_radiation_sol_success_is_cacheable_for_an_hour_ac47(
    client: httpx.AsyncClient, clean_db: Factory, state: str
) -> None:
    """AC-47: `Cache-Control: public, max-age=3600`, whatever the state of the hours."""
    if state == "hours":
        await seed_hours_4843(clean_db)
    elif state == "null-hours":
        await seed_4843(clean_db)
    else:
        await seed_4843(clean_db, hourly_revision=1)

    response = await client.get(url(4843))

    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE


async def test_radiation_sol_shows_a_change_of_its_hours_after_a_cached_answer_ac47(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """The reprocess changes hours without touching `rad_daily.updated_at`: no stale answer."""
    from sqlalchemy import text

    await seed_hours_4843(clean_db)
    first = (await client.get(url(4843))).json()
    async with clean_db() as session:
        await session.execute(text("UPDATE rad_hourly SET dose_b_mean = 8.0 WHERE sol = 4843"))
        await session.commit()

    second = (await client.get(url(4843))).json()

    assert first["hours"][0]["dose_b_ugy_h"] == 6.81
    assert second["hours"][0]["dose_b_ugy_h"] == 8.0


def load_openapi() -> dict[str, Any]:
    return json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))


def test_openapi_describes_the_radiation_sol_route_and_schemas_ac47() -> None:
    """AC-47: `docs/openapi.json` has the path and the schemas of the detail and its hours."""
    document = load_openapi()

    operation = document["paths"]["/api/v2/rovers/{rover}/radiation/{sol}"]["get"]

    assert {p["name"] for p in operation["parameters"]} >= {"rover", "sol"}
    assert {"200", "404", "422"} <= set(operation["responses"])
    schemas = document["components"]["schemas"]
    assert {"RadiationSolDetail", "RadiationHour"} <= set(schemas)
    assert list(schemas["RadiationHour"]["properties"]) == HOUR_KEYS
    detail = schemas["RadiationSolDetail"]["properties"]
    assert {"rover", "credit", "citation", "hours", *ITEM_KEYS} <= set(detail)


def test_openapi_hours_may_be_null_ac47() -> None:
    """`hours` is a list or `null` (not calculated / contradictory time)."""
    hours = load_openapi()["components"]["schemas"]["RadiationSolDetail"]["properties"]["hours"]

    flat = json.dumps(hours)
    assert "null" in flat and "RadiationHour" in flat


def test_app_openapi_equals_the_committed_document_for_the_radiation_sol_route_ac47(
    app: Any,
) -> None:
    committed = load_openapi()
    live = app.openapi()

    path = "/api/v2/rovers/{rover}/radiation/{sol}"
    assert live["paths"][path] == committed["paths"][path]
    for name in ("RadiationSolDetail", "RadiationHour"):
        assert live["components"]["schemas"][name] == committed["components"]["schemas"][name]


def test_examples_types_include_the_radiation_sol_types_ac47() -> None:
    types = (ROOT / "examples" / "types.ts").read_text(encoding="utf-8")

    assert "RadiationSolDetail" in types and "RadiationHour" in types
