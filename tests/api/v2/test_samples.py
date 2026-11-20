"""GET /api/v2/rovers/{rover}/samples and /samples/{number} (docs/API.md, stage 13).

Expected JSON comes from the contract: Sample keys in order, `sol` / `height_cm` as numbers,
`total_photos` from the rover's sol, `location.match` in its three branches (`exact`,
`nearest_before`, `by_sol`) and `null`, the envelope with `source_url`, `source_updated_at`,
`updated_at`; errors in the v2 envelope; `Cache-Control: public, max-age=3600`.
"""

import httpx
import pytest

from tests._samples import (
    NUMBERS,
    SAMPLE_KEYS,
    SAMPLES_RESPONSE_KEYS,
    SAMPLES_URL,
    SOURCE_MODIFIED_JSON,
    Factory,
    insert_samples,
    iso_z,
)
from tests.api.v2._samples_data import (
    CHANGED_AT,
    LOCATION_CASES,
    basic_expected,
    basic_items,
    location_expected,
    seed_basic,
    seed_dataset,
    seed_location_world,
)
from tests.api.v2.conftest import (
    V2,
    assert_error,
    assert_json,
    assert_validation_error,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

LIST = f"{V2}/rovers/perseverance/samples"
CACHE = "public, max-age=3600"


@pytest.fixture
async def basic(clean_db: Factory) -> Factory:
    await seed_basic(clean_db)
    return clean_db


@pytest.fixture
async def located(clean_db: Factory) -> Factory:
    await seed_location_world(clean_db)
    return clean_db


# --- list --------------------------------------------------------------------------------------


async def test_list_returns_the_envelope_and_the_samples_in_number_order(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    response = await client.get(LIST)

    assert response.status_code == 200
    body = response.json()
    assert list(body) == SAMPLES_RESPONSE_KEYS
    assert body["rover"] == "perseverance"
    assert body["source_url"] == SAMPLES_URL
    assert [item["number"] for item in body["items"]] == NUMBERS  # rows were inserted scrambled
    assert_json(body["items"], basic_items())


async def test_list_dates_are_the_page_change_and_our_last_change(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    body = (await client.get(LIST)).json()

    assert body["source_updated_at"] == SOURCE_MODIFIED_JSON  # 2026-06-20T16:45:46Z
    assert body["updated_at"] == iso_z(CHANGED_AT)  # `changed_at`, not the later `checked_at`


async def test_list_has_no_pagination_and_ignores_unknown_parameters(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    plain = (await client.get(LIST)).json()

    noisy = (await client.get(LIST, params={"page": 2, "per_page": 1, "foo": "bar"})).json()

    assert noisy == plain
    assert len(noisy["items"]) == 7


async def test_list_without_data_is_empty_with_null_dates(client: httpx.AsyncClient) -> None:
    response = await client.get(LIST)

    assert response.status_code == 200
    assert response.json() == {
        "rover": "perseverance",
        "source_url": SAMPLES_URL,
        "source_updated_at": None,
        "updated_at": None,
        "items": [],
    }
    assert list(response.json()) == SAMPLES_RESPONSE_KEYS


async def test_list_source_updated_at_is_null_when_the_page_has_no_modified_time(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_samples(clean_db, [{"number": 1, "name": "Roubion", "sol": 164}])
    await seed_dataset(clean_db, items=1, source_modified_at=None)

    body = (await client.get(LIST)).json()

    assert body["source_updated_at"] is None
    assert body["updated_at"] == iso_z(CHANGED_AT)
    assert [i["number"] for i in body["items"]] == [1]


async def test_list_slug_is_case_insensitive(client: httpx.AsyncClient, basic: Factory) -> None:
    upper = await client.get(f"{V2}/rovers/PERSEVERANCE/samples")
    mixed = await client.get(f"{V2}/rovers/PerSeverance/samples/25")

    assert upper.status_code == 200 and len(upper.json()["items"]) == 7
    assert mixed.status_code == 200 and mixed.json()["number"] == 25


# --- sample values -----------------------------------------------------------------------------


@pytest.mark.parametrize("number", NUMBERS, ids=[f"sample-{n}" for n in NUMBERS])
async def test_detail_returns_the_same_object_as_the_list(
    client: httpx.AsyncClient, basic: Factory, number: int
) -> None:
    response = await client.get(f"{LIST}/{number}")

    assert response.status_code == 200
    assert_json(response.json(), basic_expected(number))
    assert list(response.json()) == SAMPLE_KEYS


async def test_detail_25_matches_the_contract_example(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    body = (await client.get(f"{LIST}/25")).json()

    assert body == {
        "number": 25,
        "name": "Sapphire Canyon",
        "feature_name": "Cheyava Falls",
        "sampling_location": "Neretva Vallis",
        "status": "on_rover",
        "current_location": "Perseverance Rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Sedimentary",
        "sealed_on": "2024-07-21",
        "sol": 1215,
        "height_cm": 6.2,
        "height_note": None,
        "dossier_url": (
            "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/"
            "initial_reports/initial_reports_volume4.pdf#page=120"
        ),
        "total_photos": 2,
        "location": {
            "sol": 1212,
            "site": 56,
            "drive": 0,
            "lat": 18.4447,
            "lon": 77.3951,
            "elev_geoid": -2489.7,
            "match": "exact",
        },
    }


async def test_numbers_are_json_numbers_not_strings(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    items = (await client.get(LIST)).json()["items"]

    by_number = {item["number"]: item for item in items}
    assert type(by_number[25]["height_cm"]) is float and by_number[25]["height_cm"] == 6.2
    assert by_number[26]["height_cm"] == 2.91
    for item in items:
        assert type(item["number"]) is int
        assert item["sol"] is None or type(item["sol"]) is int
        assert item["total_photos"] is None or type(item["total_photos"]) is int


async def test_sample_with_tbd_has_null_date_sol_total_photos_and_location(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    body = (await client.get(f"{LIST}/29")).json()

    assert (body["sealed_on"], body["sol"], body["total_photos"], body["location"]) == (
        None,
        None,
        None,
        None,
    )
    assert body["height_cm"] is None
    assert body["height_note"] == "Est. 5-7 cm (2-2.8 in)"


async def test_sample_with_unknown_enumerations_keeps_the_page_text_and_null_codes(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_samples(
        clean_db,
        [
            {
                "number": 2,
                "name": "Mystery",
                "current_location": "Lost in Space",
                "status": None,
                "sample_type": None,
                "sample_type_label": "Exotic Core",
            }
        ],
    )
    await seed_dataset(clean_db, items=1)

    body = (await client.get(f"{LIST}/2")).json()

    assert body["status"] is None and body["current_location"] == "Lost in Space"
    assert body["sample_type"] is None and body["sample_type_label"] == "Exotic Core"


async def test_page_text_is_returned_as_is_with_diacritics_and_aka(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    body = (await client.get(f"{LIST}/8")).json()

    assert body["name"] == 'Ha\'ahóni (aka "Hahonih")'
    assert body["sampling_location"] == "Octavia E. Butler Landing / Ch’ał outcrop"


async def test_depot_sample_and_the_null_rock_type_of_the_atmospheric_sample(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    body = (await client.get(f"{LIST}/1")).json()

    assert body["status"] == "depot" and body["current_location"] == "Sample Depot"
    assert body["sample_type"] == "atmospheric" and body["rock_type"] is None
    assert body["height_cm"] is None and body["height_note"] is None


# --- total_photos ------------------------------------------------------------------------------


async def test_total_photos_counts_all_types_of_the_rovers_sol_only(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    items = {i["number"]: i for i in (await client.get(LIST)).json()["items"]}

    # Sol 164: 2 full + 1 subframe of Perseverance; the Curiosity photo of sol 164 is not counted.
    assert items[1]["total_photos"] == 3
    assert items[25]["total_photos"] == 2
    assert items[8]["total_photos"] == 0  # a sol without photos is 0, not null
    assert items[29]["total_photos"] is None  # no sol


# --- location ----------------------------------------------------------------------------------


@pytest.mark.parametrize("number", sorted(LOCATION_CASES), ids=lambda n: f"sample-{n}")
async def test_location_branches_in_the_list(
    client: httpx.AsyncClient, located: Factory, number: int
) -> None:
    items = {i["number"]: i for i in (await client.get(LIST)).json()["items"]}

    assert_json(items[number], location_expected(number))


@pytest.mark.parametrize(
    ("number", "match"),
    [
        pytest.param(1, "exact", id="exact"),
        pytest.param(2, "nearest_before", id="nearest-before"),
        pytest.param(3, "by_sol", id="by-sol-no-site-drive-photos"),
        pytest.param(4, "by_sol", id="by-sol-several-pairs"),
        pytest.param(7, "by_sol", id="by-sol-no-photos"),
        pytest.param(8, "by_sol", id="by-sol-several-stops-in-one-sol"),
        pytest.param(9, "by_sol", id="by-sol-pair-site-without-stops"),
        pytest.param(10, "by_sol", id="by-sol-pair-before-first-drive-of-site"),
        pytest.param(5, None, id="null-before-the-first-stop"),
        pytest.param(6, None, id="null-without-sol"),
    ],
)
async def test_location_match_in_the_detail(
    client: httpx.AsyncClient, located: Factory, number: int, match: str | None
) -> None:
    body = (await client.get(f"{LIST}/{number}")).json()

    if match is None:
        assert body["location"] is None
    else:
        assert body["location"]["match"] == match
    assert_json(body, location_expected(number))


async def test_location_exact_keeps_the_stops_own_sol_not_the_samples_sol(
    client: httpx.AsyncClient, located: Factory
) -> None:
    body = (await client.get(f"{LIST}/1")).json()

    assert body["sol"] == 100
    assert body["location"]["sol"] == 99


async def test_location_elevation_is_null_when_the_stop_has_none(
    client: httpx.AsyncClient, located: Factory
) -> None:
    body = (await client.get(f"{LIST}/3")).json()

    assert body["location"]["elev_geoid"] is None
    assert list(body["location"]) == ["sol", "site", "drive", "lat", "lon", "elev_geoid", "match"]


async def test_location_is_null_for_every_sample_when_there_are_no_stops(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_location_world(clean_db, with_stops=False)

    items = (await client.get(LIST)).json()["items"]

    assert [i["location"] for i in items] == [None] * len(LOCATION_CASES)
    assert_json(items, [location_expected(n, with_stops=False) for n in sorted(LOCATION_CASES)])


# --- errors ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    ["2", "0", "-1", "31", "32767", "32768", "99999999", "2147483648", "9" * 40],
    ids=lambda n: n[:12],
)
async def test_detail_of_a_missing_sample_is_404_sample_not_found(
    client: httpx.AsyncClient, basic: Factory, number: str
) -> None:
    response = await client.get(f"{LIST}/{number}")

    assert_error(response, 404, "sample_not_found")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("number", ["abc", "1.5", "1e2", "0x10", "one"])
async def test_detail_with_a_non_integer_number_is_422(
    client: httpx.AsyncClient, basic: Factory, number: str
) -> None:
    response = await client.get(f"{LIST}/{number}")

    assert_validation_error(response, "number", "n")


async def test_detail_without_data_is_404_sample_not_found(client: httpx.AsyncClient) -> None:
    assert_error(await client.get(f"{LIST}/1"), 404, "sample_not_found")


@pytest.mark.parametrize("rover", ["curiosity", "spirit", "opportunity", "ingenuity"])
@pytest.mark.parametrize("tail", ["", "/1", "/25"])
async def test_samples_of_another_existing_rover_are_404_not_found(
    client: httpx.AsyncClient, basic: Factory, rover: str, tail: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/samples{tail}")

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize("tail", ["", "/1"])
async def test_samples_of_an_unknown_rover_are_404_rover_not_found(
    client: httpx.AsyncClient, basic: Factory, tail: str
) -> None:
    response = await client.get(f"{V2}/rovers/xyz/samples{tail}")

    assert_error(response, 404, "rover_not_found")


# --- caching -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tail", ["", "/25"], ids=["list", "detail"])
async def test_samples_are_cacheable_for_one_hour(
    client: httpx.AsyncClient, basic: Factory, tail: str
) -> None:
    response = await client.get(f"{LIST}{tail}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE
    assert response.headers["etag"]


async def test_empty_list_is_cacheable_for_one_hour_too(client: httpx.AsyncClient) -> None:
    response = await client.get(LIST)

    assert response.headers["cache-control"] == CACHE


@pytest.mark.parametrize("tail", ["", "/25"], ids=["list", "detail"])
async def test_if_none_match_gets_304(client: httpx.AsyncClient, basic: Factory, tail: str) -> None:
    first = await client.get(f"{LIST}{tail}")

    again = await client.get(f"{LIST}{tail}", headers={"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304
    assert again.content == b""


async def test_etag_changes_when_a_sample_changes(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    from sqlalchemy import text

    first = await client.get(f"{LIST}/25")
    async with basic() as session:
        await session.execute(text("UPDATE rover_samples SET rock_type = 'X' WHERE number = 25"))
        await session.commit()

    second = await client.get(f"{LIST}/25")

    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert second.json()["rock_type"] == "X"


async def test_other_v2_lists_keep_their_cache_time(
    client: httpx.AsyncClient, basic: Factory
) -> None:
    rovers = await client.get(f"{V2}/rovers")

    assert rovers.headers["cache-control"] == "public, max-age=300"
