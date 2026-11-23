"""GET /api/v2/rovers/{rover}/radiation (docs/API.md `RadiationSol`, stage 21, AC-30 ... AC-38).

Rows are inserted straight into `rad_daily` / `rad_dataset` (doses in uGy/h as stored); the
expected JSON is computed by hand from the contract: doses x 24 rounded to 0.01, rate to
0.001, coverage to 0.01 of 88 775.244 s, ascending sol order, the v2 error envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._rad import BASE_URL, CREDIT_RAD, Factory, insert_daily, insert_dataset
from tests.api.v2.conftest import V2, assert_error, assert_json

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

URL = f"{V2}/rovers/curiosity/radiation"
CACHE = "public, max-age=3600"
ROOT = Path(__file__).resolve().parents[3]
CITATION = (
    "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, and D.M. Hassler, "
    "MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, MSL-M-RAD-3-RDR-V1.0, "
    "NASA Planetary Data System, 2013."
)
SOURCE_URL = "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/"
TOP_KEYS = [
    "rover",
    "credit",
    "citation",
    "source_url",
    "updated_at",
    "source_max_sol",
    "source_release",
    "items",
]
ITEM_KEYS = [
    "sol",
    "earth_date",
    "dose_b_ugy_day",
    "dose_e_ugy_day",
    "count_rate_b_per_s",
    "coverage",
    "observations",
    "timing_caveat",
    "source",
]
PATH_4843 = "DATA/SOL_04710_04843/RAD_RDR_2026_080_17_18_4843_V00.TXT"
UPDATED = "2026-11-02T03:04:05+00:00"


async def seed_4843(factory: Factory, **overrides: Any) -> None:
    """The sol 4843 summary of the fixture file (3 used observations, one dropped by QC)."""
    values: dict[str, Any] = {
        "earth_date": "2026-03-21",
        "dose_b": (6.866817, 6.78671, 6.97246),
        "dose_e": (6.517087, 6.43259, 6.58689),
        "bu_count": 19450,
        "live_s": 2790.0,
        "used": 3,
        "dropped_qc": 1,
        "source_path": PATH_4843,
        "updated_at": UPDATED,
    }
    values.update(overrides)
    await insert_daily(factory, 4843, **values)


EXPECTED_4843 = {
    "sol": 4843,
    "earth_date": "2026-03-21",
    "dose_b_ugy_day": {"mean": 164.8, "min": 162.88, "max": 167.34},
    "dose_e_ugy_day": {"mean": 156.41, "min": 154.38, "max": 158.09},
    "count_rate_b_per_s": 6.971,
    "coverage": 0.03,
    "observations": {"used": 3, "dropped_qc": 1, "dropped_low_rate": 0, "dropped_invalid": 0},
    "timing_caveat": False,
    "source": {"file": PATH_4843, "link": BASE_URL + PATH_4843},
}


async def seed_dataset(factory: Factory) -> None:
    await insert_dataset(
        factory,
        checksums_last_modified="Mon, 03 Aug 2026 12:00:00 GMT",
        changed_at=UPDATED,
        checked_at=UPDATED,
        succeeded_at=UPDATED,
        files_listed=1,
    )


# --- AC-30: the list  ----------------------------------------------------------------------------


async def test_radiation_returns_the_envelope_and_the_summaries_ascending_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)
    await insert_daily(clean_db, 1649, earth_date="2017-03-26")  # inserted after: must sort first
    await insert_daily(clean_db, 10, earth_date="2012-08-15")
    await seed_dataset(clean_db)

    response = await client.get(URL)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert list(body) == TOP_KEYS
    assert body["rover"] == "curiosity"
    assert body["credit"] == CREDIT_RAD
    assert body["citation"] == CITATION
    assert body["source_url"] == SOURCE_URL
    assert [item["sol"] for item in body["items"]] == [10, 1649, 4843]
    assert all(list(item) == ITEM_KEYS for item in body["items"])
    assert body["updated_at"] == "2026-11-02T03:04:05Z"
    assert body["source_max_sol"] == 4843
    assert body["source_release"] == "2026-08-03"


async def test_radiation_item_values_are_x24_and_rounded_as_the_contract_says_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)

    items = (await client.get(URL)).json()["items"]

    assert_json(items, [EXPECTED_4843])


async def test_radiation_item_with_the_numbers_of_the_contract_example_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """AC-7 in the API: B 6.5 / 6.0 / 8.0 uGy/h over 1200 s live and 9900 events."""
    await insert_daily(
        clean_db,
        1649,
        dose_b=(6.5, 6.0, 8.0),
        dose_e=(6.0, 5.0, 7.0),
        bu_count=9900,
        live_s=1200.0,
        used=2,
    )

    item = (await client.get(URL)).json()["items"][0]

    assert item["dose_b_ugy_day"] == {"mean": 156.0, "min": 144.0, "max": 192.0}
    assert item["dose_e_ugy_day"] == {"mean": 144.0, "min": 120.0, "max": 168.0}
    assert item["count_rate_b_per_s"] == 8.25
    assert item["coverage"] == 0.01  # 1200 / 88775.244


@pytest.mark.parametrize(
    ("live_s", "coverage"),
    [(88775.244, 1.0), (100000.0, 1.0), (44387.622, 0.5), (900.0, 0.01), (100.0, 0.0)],
    ids=["full-sol", "over-a-sol", "half", "one-percent", "tiny"],
)
async def test_radiation_coverage_is_live_time_over_a_sol_capped_at_one_ac30(
    client: httpx.AsyncClient, clean_db: Factory, live_s: float, coverage: float
) -> None:
    await insert_daily(clean_db, 100, live_s=live_s, bu_count=int(live_s * 8))

    item = (await client.get(URL)).json()["items"][0]

    assert item["coverage"] == coverage


@pytest.mark.parametrize(
    ("bu_count", "live_s", "rate"),
    [(7200, 900.0, 8.0), (6454, 930.0, 6.94), (1, 3.0, 0.333), (0, 900.0, 0.0)],
    ids=["round", "two-places", "third", "zero"],
)
async def test_radiation_count_rate_is_events_per_live_second_to_three_places_ac30(
    client: httpx.AsyncClient, clean_db: Factory, bu_count: int, live_s: float, rate: float
) -> None:
    await insert_daily(clean_db, 100, bu_count=bu_count, live_s=live_s)

    item = (await client.get(URL)).json()["items"][0]

    assert item["count_rate_b_per_s"] == rate


async def test_radiation_item_numbers_are_json_numbers_not_strings_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)

    item = (await client.get(URL)).json()["items"][0]

    for value in (item["sol"], item["count_rate_b_per_s"], item["coverage"]):
        assert isinstance(value, int | float) and not isinstance(value, bool)
    for group in ("dose_b_ugy_day", "dose_e_ugy_day"):
        assert all(isinstance(v, float) for v in item[group].values())
    assert all(isinstance(v, int) for v in item["observations"].values())


async def test_radiation_returns_the_whole_history_without_pagination_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for sol in range(1, 61):
        await insert_daily(clean_db, sol)

    body = (await client.get(URL)).json()

    assert [item["sol"] for item in body["items"]] == list(range(1, 61))
    assert "next" not in body and "total" not in body


async def test_radiation_source_max_sol_and_updated_at_follow_the_summaries_ac30(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 10, updated_at="2026-11-01T00:00:00+00:00")
    await insert_daily(clean_db, 20, updated_at="2026-11-03T10:11:12+00:00")
    await insert_daily(clean_db, 15, updated_at="2026-11-02T00:00:00+00:00")

    body = (await client.get(URL)).json()

    assert body["source_max_sol"] == 20
    assert body["updated_at"] == "2026-11-03T10:11:12Z"


# --- AC-31: range  -------------------------------------------------------------------------------


@pytest.fixture
async def spread(clean_db: Factory) -> Factory:
    for sol in (4799, 4800, 4805, 4810, 4811):
        await insert_daily(clean_db, sol)
    return clean_db


async def test_radiation_range_is_inclusive_ac31(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    body = (await client.get(URL, params={"sol_min": 4800, "sol_max": 4810})).json()

    assert [item["sol"] for item in body["items"]] == [4800, 4805, 4810]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"sol_min": 4805}, [4805, 4810, 4811]),
        ({"sol_max": 4805}, [4799, 4800, 4805]),
        ({"sol_min": 4805, "sol_max": 4805}, [4805]),
        ({"sol_min": 4812}, []),
        ({"sol_min": 0}, [4799, 4800, 4805, 4810, 4811]),
        ({"sol_max": 2147483647}, [4799, 4800, 4805, 4810, 4811]),
        ({"sol_min": 4806, "sol_max": 4809}, []),
    ],
    ids=["min-only", "max-only", "one-sol", "above-all", "zero", "int-max", "gap"],
)
async def test_radiation_range_variants_ac31(
    client: httpx.AsyncClient, spread: Factory, params: dict[str, int], expected: list[int]
) -> None:
    response = await client.get(URL, params=params)

    assert response.status_code == 200
    assert [item["sol"] for item in response.json()["items"]] == expected


async def test_radiation_range_keeps_the_envelope_even_when_empty_ac31(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    body = (await client.get(URL, params={"sol_min": 9000})).json()

    assert body["items"] == []
    assert body["credit"] == CREDIT_RAD and body["citation"] == CITATION


@pytest.mark.parametrize(
    "query",
    [
        "sol_min=10&sol_max=5",
        "sol_min=abc",
        "sol_max=1.5",
        "sol_min=-1",
        "sol_max=2147483648",
        "sol_min=",
        "sol_min=1&sol_min=2",
        "sol_max=1&sol_max=2",
    ],
    ids=[
        "min-over-max",
        "not-a-number",
        "fraction",
        "negative",
        "over-int32",
        "empty",
        "min-twice",
        "max-twice",
    ],
)
async def test_radiation_invalid_range_is_a_validation_error_ac31(
    client: httpx.AsyncClient, spread: Factory, query: str
) -> None:
    response = await client.get(f"{URL}?{query}")

    assert_error(response, 422, "validation_error")


async def test_radiation_unknown_parameters_are_ignored_ac31(
    client: httpx.AsyncClient, spread: Factory
) -> None:
    response = await client.get(URL, params={"foo": "bar", "page": 3})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 5


# --- AC-32: rovers  ------------------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["perseverance", "spirit", "opportunity", "ingenuity"])
async def test_radiation_other_rovers_are_not_found_ac32(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    await insert_daily(clean_db, 100)

    response = await client.get(f"{V2}/rovers/{rover}/radiation")

    assert_error(response, 404, "not_found")


async def test_radiation_unknown_rover_is_rover_not_found_ac32(
    client: httpx.AsyncClient,
) -> None:
    assert_error(await client.get(f"{V2}/rovers/pluto/radiation"), 404, "rover_not_found")


async def test_radiation_rover_id_is_case_insensitive_ac32(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(clean_db, 100)

    response = await client.get(f"{V2}/rovers/CURIOSITY/radiation")

    assert response.status_code == 200
    assert response.json()["rover"] == "curiosity"


# --- AC-33: empty  -------------------------------------------------------------------------------


async def test_radiation_empty_tables_give_an_empty_list_and_null_dates_ac33(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(URL)

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "rover": "curiosity",
            "credit": CREDIT_RAD,
            "citation": CITATION,
            "source_url": SOURCE_URL,
            "updated_at": None,
            "source_max_sol": None,
            "source_release": None,
            "items": [],
        },
    )


async def test_radiation_dataset_row_without_summaries_still_has_empty_items_ac33(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_dataset(clean_db)

    body = (await client.get(URL)).json()

    assert body["items"] == []
    assert body["source_max_sol"] is None
    assert body["credit"] == CREDIT_RAD


# --- AC-34: cache  -------------------------------------------------------------------------------


async def test_radiation_success_is_cacheable_for_an_hour_with_an_etag_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)

    response = await client.get(URL)

    assert response.headers["cache-control"] == CACHE
    assert response.headers["etag"]


async def test_radiation_empty_answer_is_cacheable_too_ac34(client: httpx.AsyncClient) -> None:
    assert (await client.get(URL)).headers["cache-control"] == CACHE


async def test_radiation_if_none_match_gets_304_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)
    first = await client.get(URL)

    again = await client.get(URL, headers={"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


async def test_radiation_etag_changes_when_a_summary_changes_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)
    first = await client.get(URL)
    from sqlalchemy import text

    async with clean_db() as session:
        await session.execute(
            text(
                "UPDATE rad_daily SET dose_b_mean = 7.0, "
                "updated_at = '2026-12-30T00:00:00+00:00' WHERE sol = 4843"
            )
        )
        await session.commit()

    second = await client.get(URL)

    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]


async def test_radiation_head_has_the_same_headers_and_no_body_ac34(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_4843(clean_db)
    get = await client.get(URL)

    head = await client.head(URL)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == CACHE
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-type"] == get.headers["content-type"]


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/rovers/perseverance/radiation", 404),
        ("/rovers/pluto/radiation", 404),
        ("/rovers/curiosity/radiation?sol_min=abc", 422),
        ("/rovers/curiosity/radiation?sol_min=9&sol_max=1", 422),
    ],
    ids=["no-radiation", "unknown-rover", "not-a-number", "min-over-max"],
)
async def test_radiation_errors_are_not_cached_ac34(
    client: httpx.AsyncClient, path: str, status: int
) -> None:
    response = await client.get(f"{V2}{path}")

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


# --- AC-35: source and timing flag  --------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False, None], ids=["true", "false", "null"])
async def test_radiation_timing_caveat_is_true_false_or_null_ac35(
    client: httpx.AsyncClient, clean_db: Factory, flag: bool | None
) -> None:
    await insert_daily(clean_db, 10, timing_caveat=flag)

    item = (await client.get(URL)).json()["items"][0]

    assert item["timing_caveat"] is flag


async def test_radiation_source_is_the_volume_path_and_the_archive_link_ac35(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_daily(
        clean_db, 1649, source_path="DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT"
    )

    source = (await client.get(URL)).json()["items"][0]["source"]

    assert source == {
        "file": "DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT",
        "link": "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/"
        "DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT",
    }


# --- AC-36: the manifest is untouched  -----------------------------------------------------------


async def test_curiosity_manifest_does_not_change_with_radiation_data_ac36(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    manifest = f"{V2}/rovers/curiosity/manifest"
    before = await client.get(manifest)
    await seed_4843(clean_db)
    await seed_dataset(clean_db)

    after = await client.get(manifest)

    assert before.status_code == after.status_code == 200
    assert after.json() == before.json()
    assert "radiation" not in after.text.lower()


async def test_v1_has_no_radiation_route_ac36(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/rovers/curiosity/radiation")

    assert response.status_code == 404


# --- AC-38: OpenAPI and types  -------------------------------------------------------------------


def test_openapi_describes_the_radiation_route_and_schemas_ac38() -> None:
    document = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))

    operation = document["paths"]["/api/v2/rovers/{rover}/radiation"]["get"]

    names = {p["name"] for p in operation["parameters"]}
    assert {"rover", "sol_min", "sol_max"} <= names
    assert {"200", "404", "422"} <= set(operation["responses"])
    schemas = document["components"]["schemas"]
    assert list(schemas["RadiationResponse"]["properties"]) == TOP_KEYS
    assert list(schemas["RadiationSol"]["properties"]) == ITEM_KEYS
    assert {"RadiationSol", "RadiationResponse"} <= set(schemas)


def test_app_openapi_equals_the_committed_document_for_the_radiation_route_ac38(
    app: Any,
) -> None:
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    live = app.openapi()

    path = "/api/v2/rovers/{rover}/radiation"
    assert live["paths"][path] == committed["paths"][path]
    assert (
        live["components"]["schemas"]["RadiationSol"]
        == (committed["components"]["schemas"]["RadiationSol"])
    )


def test_examples_types_include_the_radiation_types_ac38() -> None:
    types = (ROOT / "examples" / "types.ts").read_text(encoding="utf-8")

    assert "RadiationSol" in types and "RadiationResponse" in types
