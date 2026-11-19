"""GET /api/v2/rovers/{rover}/traverse (docs/API.md; stage 11).

GeoJSON (RFC 7946) FeatureCollection, `Content-Type: application/geo+json`, the whole route in
travel order, every feature a 2D `LineString`, `properties.sol` may be null (always for
Curiosity), unknown query parameters are ignored, `available: false` for Spirit/Opportunity.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.location_sync import LocationSyncService
from app.sources.http import UpstreamClient
from tests._locations import BASE_URL, Factory, fixture_json, insert_dataset, mock_files
from tests.api.v2._locations_data import (
    CURIOSITY_TRAVERSE,
    CURIOSITY_UPDATED,
    PERSEVERANCE_TRAVERSE,
    PERSEVERANCE_TRAVERSE_UPDATED,
    seed_locations,
    traverse_response,
)
from tests.api.v2.conftest import V2, assert_error, assert_json

pytestmark = pytest.mark.api

P = "perseverance"
TOP_KEYS = ["type", "rover", "available", "updated_at", "source_max_sol", "features"]


@pytest.fixture
async def seeded(clean_db: Factory) -> Factory:
    await seed_locations(clean_db)
    return clean_db


def media_type(response: httpx.Response) -> str:
    return response.headers["content-type"].split(";")[0].strip().lower()


def assert_valid_geojson_route(body: dict[str, Any]) -> None:
    assert body["type"] == "FeatureCollection"
    for feature in body["features"]:
        assert list(feature) == ["type", "geometry", "properties"]
        assert feature["type"] == "Feature"
        geometry = feature["geometry"]
        assert list(geometry) == ["type", "coordinates"]
        assert geometry["type"] == "LineString"
        assert len(geometry["coordinates"]) >= 2
        for position in geometry["coordinates"]:
            assert len(position) == 2, "no elevation"
            lon, lat = position
            assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert list(feature["properties"]) == ["sol", "length_m"]


async def test_traverse_returns_exact_geojson_in_travel_order(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/traverse")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == TOP_KEYS
    expected = traverse_response(
        P, PERSEVERANCE_TRAVERSE, updated_at=PERSEVERANCE_TRAVERSE_UPDATED, source_max_sol=10
    )
    assert_json(body, expected)
    assert_valid_geojson_route(body)


async def test_traverse_content_type_is_geo_json(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/traverse")

    assert media_type(response) == "application/geo+json"


async def test_traverse_updated_at_is_the_route_change_time_not_the_waypoints_one(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/traverse")).json()

    assert body["updated_at"] == "2026-10-28T15:05:03Z"  # waypoints: ...:06Z


async def test_traverse_only_line_strings_with_two_numbers_per_position(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/traverse")).json()

    assert {f["geometry"]["type"] for f in body["features"]} == {"LineString"}
    assert [len(f["geometry"]["coordinates"]) for f in body["features"]] == [2, 3, 2]
    assert all(
        isinstance(v, float)
        for f in body["features"]
        for p in f["geometry"]["coordinates"]
        for v in p
    )


async def test_traverse_segment_properties_keep_null_sol_and_length(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    features = (await client.get(f"{V2}/rovers/{P}/traverse")).json()["features"]

    assert [f["properties"] for f in features] == [
        {"sol": 5, "length_m": 23.6},
        {"sol": 10, "length_m": None},
        {"sol": None, "length_m": 5.5},
    ]


async def test_traverse_curiosity_has_null_sol_everywhere_and_null_source_max_sol(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/traverse")).json()

    assert_json(
        body,
        traverse_response(
            "curiosity", CURIOSITY_TRAVERSE, updated_at=CURIOSITY_UPDATED, source_max_sol=None
        ),
    )
    assert [f["properties"]["sol"] for f in body["features"]] == [None, None]
    assert body["source_max_sol"] is None


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("from_sol=10&to_sol=20", id="range"),
        pytest.param("sol=5", id="sol"),
        pytest.param("from_sol=20&to_sol=10", id="inverted-range"),
        pytest.param("sol=abc&page=x&per_page=-1", id="garbage"),
        pytest.param("foo=bar", id="unknown"),
    ],
)
async def test_traverse_ignores_unknown_parameters_and_always_returns_the_whole_route(
    client: httpx.AsyncClient, seeded: Factory, query: str
) -> None:
    plain = await client.get(f"{V2}/rovers/{P}/traverse")

    response = await client.get(f"{V2}/rovers/{P}/traverse?{query}")

    assert response.status_code == 200
    assert response.content == plain.content
    assert len(response.json()["features"]) == 3


@pytest.mark.parametrize(
    "slug",
    ["pluto", "perseverance2", "%D0%BC%D0%B0%D1%80%D1%81"],
    ids=["unknown", "digit", "cyrillic"],
)
async def test_traverse_unknown_rover_returns_404_rover_not_found(
    client: httpx.AsyncClient, seeded: Factory, slug: str
) -> None:
    response = await client.get(f"{V2}/rovers/{slug}/traverse")

    assert_error(response, 404, "rover_not_found")
    assert media_type(response) == "application/json"


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_traverse_mer_rovers_are_available_false_with_no_features(
    client: httpx.AsyncClient, seeded: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/traverse")

    assert response.status_code == 200
    assert media_type(response) == "application/geo+json"
    assert_json(
        response.json(),
        traverse_response(rover, [], updated_at=None, source_max_sol=None, available=False),
    )


@pytest.mark.parametrize("rover", ["perseverance", "curiosity"])
async def test_traverse_before_the_first_load_is_available_true_and_empty(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/traverse")

    assert response.status_code == 200
    assert media_type(response) == "application/geo+json"
    assert_json(response.json(), traverse_response(rover, [], updated_at=None, source_max_sol=None))


async def test_traverse_failed_first_attempt_is_empty_with_null_state(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_dataset(
        clean_db, P, "traverse", checked_at=datetime(2026, 11, 18, tzinfo=UTC), last_error="boom"
    )

    body = (await client.get(f"{V2}/rovers/{P}/traverse")).json()

    assert body["features"] == [] and body["updated_at"] is None


async def test_traverse_other_rover_segments_do_not_leak(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    perseverance = (await client.get(f"{V2}/rovers/{P}/traverse")).json()
    curiosity = (await client.get(f"{V2}/rovers/curiosity/traverse")).json()

    assert len(perseverance["features"]) == 3
    assert len(curiosity["features"]) == 2
    assert perseverance["rover"] == P and curiosity["rover"] == "curiosity"


# --- sync -> API on the recorded files ---------------------------------------------------------


@pytest.fixture
async def synced(
    clean_db: Factory, settings: Settings, migrated_db: str, upstream_router: respx.MockRouter
) -> AsyncIterator[None]:
    mock_files(upstream_router)
    values = settings.model_copy(
        update={"location_base_url": BASE_URL, "upstream_max_rps": 0, "upstream_max_retries": 0}
    )
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    client = UpstreamClient(values)
    try:
        await LocationSyncService(engine, client, values).refresh_due(datetime.now(UTC))
        yield
    finally:
        await client.aclose()
        await engine.dispose()


async def test_traverse_after_a_sync_serves_the_recorded_m20_route(
    client: httpx.AsyncClient, synced: None
) -> None:
    raw = fixture_json(P, "traverse")["features"]
    body = (await client.get(f"{V2}/rovers/{P}/traverse")).json()

    assert_valid_geojson_route(body)
    assert body["source_max_sol"] == 2022
    assert [f["properties"]["sol"] for f in body["features"]] == [14, 15, 16, 668, 670, 2009, 2022]
    assert [f["properties"]["length_m"] for f in body["features"]] == [
        f["properties"]["length"] for f in raw
    ]
    for feature, source in zip(body["features"], raw, strict=True):
        assert feature["geometry"]["coordinates"] == [
            [round(c[0], 7), round(c[1], 7)] for c in source["geometry"]["coordinates"]
        ]


async def test_traverse_after_a_sync_splits_the_msl_multilinestring_into_line_strings(
    client: httpx.AsyncClient, synced: None
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/traverse")).json()

    assert_valid_geojson_route(body)
    assert body["source_max_sol"] is None
    assert len(body["features"]) == 10
    assert [len(f["geometry"]["coordinates"]) for f in body["features"][:5]] == [7, 50, 278, 63, 50]
    assert [f["properties"]["length_m"] for f in body["features"][:5]] == [
        935.64,
        None,
        None,
        None,
        None,
    ]
    assert {f["properties"]["sol"] for f in body["features"]} == {None}
