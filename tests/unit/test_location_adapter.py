"""MMGIS location adapter (app/sources/locations.py; stage 11, section 4 of the stage plan).

Recorded files: tests/fixtures/upstream/mmgis/ (trimmed real MMGIS responses);
synthetic quirks: tests/fixtures/upstream/synthetic/mmgis_{waypoints,traverse}_edge.json.
Rules checked: explicit whitelist of properties, `z` dropped, `-9999`/"UNK"/"" -> None,
number strings -> numbers, MultiLineString split into LineStrings, longitude 0..360 -> -180..180,
duplicates by (site, drive), dropped objects counted.
"""

import dataclasses
import json
from typing import Any

import httpx
import pytest
import respx

from app.sources.http import ConditionalBody, UpstreamClient, UpstreamError
from app.sources.locations import (
    LOCATION_MISSIONS,
    LocationAdapter,
    LocationFormatError,
    ParsedDataset,
    TraverseSegment,
    WaypointRecord,
    parse_traverse,
    parse_waypoints,
)
from tests._locations import (
    BASE_URL,
    feature_collection,
    file_url,
    fixture_bytes,
    fixture_json,
    synthetic_bytes,
    to_body,
)
from tests.unit._upstream import FakeClock, make_settings

pytestmark = pytest.mark.unit

WAYPOINT_FIELDS = [
    "sol",
    "site",
    "drive",
    "lat",
    "lon",
    "elev_geoid",
    "easting",
    "northing",
    "roll",
    "pitch",
    "yaw",
    "tilt",
    "dist_m",
    "dist_total_m",
]


def keys(record: WaypointRecord) -> tuple[int, int]:
    return record.site, record.drive


# --- recorded waypoints -----------------------------------------------------------------------


def test_missions_map_rovers_to_mmgis_codes() -> None:
    assert LOCATION_MISSIONS == {"perseverance": "M20", "curiosity": "MSL"}


def test_waypoint_record_has_exactly_the_whitelisted_fields() -> None:
    assert [f.name for f in dataclasses.fields(WaypointRecord)] == WAYPOINT_FIELDS
    assert dataclasses.is_dataclass(WaypointRecord)
    with pytest.raises(dataclasses.FrozenInstanceError):
        WaypointRecord(1, 2, 3, 4.0, 5.0).sol = 9  # type: ignore[misc]


def test_parse_waypoints_m20_returns_every_point_in_order_with_max_sol() -> None:
    result = parse_waypoints(fixture_bytes("perseverance", "waypoints"))

    assert isinstance(result, ParsedDataset)
    assert result.dropped == 0
    assert result.max_sol == 2022
    assert [keys(r) for r in result.items] == [
        (3, 0),
        (3, 110),
        (3, 386),
        (3, 578),
        (9, 364),
        (9, 1064),
        (9, 1554),
        (81, 3608),
        (81, 4488),
        (91, 1542),
        (92, 0),
        (92, 130),
        (93, 0),
    ]
    assert all(isinstance(r, WaypointRecord) for r in result.items)


def test_parse_waypoints_m20_first_point_has_whitelisted_values_only() -> None:
    first = parse_waypoints(fixture_bytes("perseverance", "waypoints")).items[0]

    assert first == WaypointRecord(
        sol=13,
        site=3,
        drive=0,
        lat=18.44462715,
        lon=77.45088572,
        elev_geoid=-2569.91,
        easting=4354494.086,
        northing=1093299.695,
        roll=-1.181698,
        pitch=-0.025127,
        yaw=130.881569,
        tilt=1.181965113,
        dist_m=0.0,
        dist_total_m=0.0,
    )
    for name in ("sol", "site", "drive"):
        assert type(getattr(first, name)) is int


def test_parse_waypoints_m20_point_with_images_and_9999_placeholders_keeps_only_whitelist() -> None:
    raw = fixture_json("perseverance", "waypoints")["features"][7]["properties"]
    assert raw["radius"] == -9999.0 and raw["images"]  # the quirk is really in the fixture

    record = next(
        r
        for r in parse_waypoints(fixture_bytes("perseverance", "waypoints")).items
        if keys(r) == (81, 3608)
    )

    assert record.sol == 1690
    assert (record.lat, record.lon) == (18.42951034, 77.25253478)
    assert record.elev_geoid == -1863.685791
    assert [f.name for f in dataclasses.fields(record)] == WAYPOINT_FIELDS
    assert not hasattr(record, "images") and not hasattr(record, "radius")
    assert -9999.0 not in dataclasses.astuple(record)


def test_parse_waypoints_m20_keeps_a_gap_between_drives_of_one_site() -> None:
    items = parse_waypoints(fixture_bytes("perseverance", "waypoints")).items

    assert [r.drive for r in items if r.site == 3] == [0, 110, 386, 578]


def test_parse_waypoints_m20_several_stops_on_one_sol_are_all_kept() -> None:
    items = parse_waypoints(fixture_bytes("perseverance", "waypoints")).items

    assert [keys(r) for r in items if r.sol == 340] == [(9, 364), (9, 1064), (9, 1554)]


def test_parse_waypoints_msl_site_43_starts_at_drive_216_and_last_stop_is_125_2490() -> None:
    result = parse_waypoints(fixture_bytes("curiosity", "waypoints"))

    assert (result.dropped, result.max_sol, len(result.items)) == (0, 5073, 13)
    assert [r.drive for r in result.items if r.site == 43] == [216]
    last = result.items[-1]
    assert (last.site, last.drive, last.sol) == (125, 2490, 5073)
    assert (last.lat, last.lon, last.elev_geoid) == (-4.82602331, 137.388886, -3472.085)
    assert last.tilt == 12.45


def test_parse_waypoints_z_of_the_geometry_is_not_used() -> None:
    raw = fixture_json("curiosity", "waypoints")["features"][2]
    record = parse_waypoints(fixture_bytes("curiosity", "waypoints")).items[2]

    assert raw["geometry"]["coordinates"][2] == record.elev_geoid  # same value in the file...
    assert not hasattr(record, "z")  # ...but the elevation is the `elev_geoid` property
    assert (record.site, record.drive) == (3, 78)


# --- synthetic waypoints -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def edge() -> ParsedDataset[WaypointRecord]:
    return parse_waypoints(synthetic_bytes("mmgis_waypoints_edge.json"))


def edge_item(edge: ParsedDataset[WaypointRecord], site: int, drive: int) -> WaypointRecord:
    [found] = [r for r in edge.items if keys(r) == (site, drive)]
    return found


def test_edge_waypoints_keep_valid_objects_and_count_the_dropped(
    edge: ParsedDataset[WaypointRecord],
) -> None:
    assert sorted(keys(r) for r in edge.items) == [
        (7, 10),
        (9, 5),
        (9, 6),
        (10, 0),
        (12, 0),
        (12, 1),
        (12, 2),
        (13, 0),
    ]
    # no lat, no site, no sol, a LineString geometry + two repeated (site, drive)
    assert edge.dropped == 6
    assert edge.max_sol == 502


def test_edge_waypoint_number_strings_unk_empty_null_and_9999(
    edge: ParsedDataset[WaypointRecord],
) -> None:
    record = edge_item(edge, 7, 10)

    assert record == WaypointRecord(
        sol=100,
        site=7,
        drive=10,
        lat=18.5,  # properties, not the Point geometry (70.0, 10.0)
        lon=77.5,
        elev_geoid=-2500.5,  # "-2500.5" string; the geometry z is 12345.0
        easting=None,  # -9999.0
        northing=12.5,  # "12.5"
        roll=None,  # ""
        pitch=None,  # null
        yaw=None,  # "UNK"
        tilt=None,  # -9999 (int)
        dist_m=0.0,
        dist_total_m=None,  # "UNK"
    )
    assert isinstance(record.sol, int) and isinstance(record.site, int)
    assert isinstance(record.lat, float) and isinstance(record.elev_geoid, float)


def test_edge_waypoint_repeated_site_drive_keeps_the_object_with_the_greater_sol(
    edge: ParsedDataset[WaypointRecord],
) -> None:
    record = edge_item(edge, 9, 5)

    assert (record.sol, record.lat, record.lon) == (201, 3.0, 4.0)


def test_edge_waypoint_repeated_site_drive_with_equal_sol_keeps_the_last(
    edge: ParsedDataset[WaypointRecord],
) -> None:
    record = edge_item(edge, 9, 6)

    assert (record.sol, record.lat, record.lon) == (300, 6.0, 7.0)


def test_edge_waypoint_longitude_above_180_is_shifted_by_360(
    edge: ParsedDataset[WaypointRecord],
) -> None:
    assert edge_item(edge, 10, 0).lon == -90.0


@pytest.mark.parametrize(
    "properties",
    [
        pytest.param({"drive": 1, "sol": 5, "lat": 1.0, "lon": 2.0}, id="no-site"),
        pytest.param({"site": 1, "sol": 5, "lat": 1.0, "lon": 2.0}, id="no-drive"),
        pytest.param({"site": 1, "drive": 1, "lat": 1.0, "lon": 2.0}, id="no-sol"),
        pytest.param({"site": 1, "drive": 1, "sol": 5, "lon": 2.0}, id="no-lat"),
        pytest.param({"site": 1, "drive": 1, "sol": 5, "lat": 1.0}, id="no-lon"),
        pytest.param({"site": "UNK", "drive": 1, "sol": 5, "lat": 1.0, "lon": 2.0}, id="unk-site"),
        pytest.param({"site": 1, "drive": 1, "sol": 5, "lat": None, "lon": 2.0}, id="null-lat"),
    ],
)
def test_waypoint_without_a_required_field_is_dropped(properties: dict[str, Any]) -> None:
    good = {"site": 2, "drive": 0, "sol": 6, "lat": 3.0, "lon": 4.0}
    features = [
        {
            "type": "Feature",
            "properties": p,
            "geometry": {"type": "Point", "coordinates": [0, 0, 0]},
        }
        for p in (properties, good)
    ]

    result = parse_waypoints(feature_collection(features))

    assert [keys(r) for r in result.items] == [(2, 0)]
    assert result.dropped == 1


@pytest.mark.parametrize("field", ["sol", "site", "drive"])
@pytest.mark.parametrize("value", [2**31, 2**40, -1], ids=["int4-plus-1", "2-pow-40", "negative"])
def test_waypoint_integer_outside_int4_or_negative_is_dropped(field: str, value: int) -> None:
    properties = {"site": 2, "drive": 0, "sol": 6, "lat": 3.0, "lon": 4.0}
    bad = {**properties, field: value}
    features = [
        {
            "type": "Feature",
            "properties": p,
            "geometry": {"type": "Point", "coordinates": [0, 0, 0]},
        }
        for p in (bad, {**properties, "drive": 1})
    ]

    result = parse_waypoints(feature_collection(features))

    assert [keys(r) for r in result.items] == [(2, 1)]
    assert result.dropped == 1


def test_traverse_segment_sol_outside_int4_is_not_stored_as_is() -> None:
    feature = {
        "type": "Feature",
        "properties": {"sol": 2**40, "length": 1.0},
        "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0, 0], [1.1, 2.1, 0]]},
    }
    result = parse_traverse(feature_collection([feature]))

    assert all(s.sol is None or s.sol < 2**31 for s in result.items)


def test_waypoints_empty_collection_gives_an_empty_result() -> None:
    result = parse_waypoints(feature_collection([]))

    assert (result.items, result.dropped, result.max_sol) == ([], 0, None)


# --- recorded traverse -------------------------------------------------------------------------


def test_parse_traverse_m20_segments_in_order_with_sol_and_length() -> None:
    result = parse_traverse(fixture_bytes("perseverance", "traverse"))

    assert result.dropped == 0
    assert result.max_sol == 2022
    assert [s.seq for s in result.items] == list(range(7))
    assert [s.sol for s in result.items] == [14, 15, 16, 668, 670, 2009, 2022]
    assert [s.length_m for s in result.items] == [6.25, 36.39, 27.43, 9.57, 7.98, 6.26, 22.06]
    assert all(isinstance(s, TraverseSegment) for s in result.items)


def test_parse_traverse_m20_drops_z_and_keeps_lon_lat_pairs() -> None:
    raw = fixture_json("perseverance", "traverse")["features"]
    result = parse_traverse(fixture_bytes("perseverance", "traverse"))

    for feature, segment in zip(raw, result.items, strict=True):
        expected = tuple(
            (round(c[0], 7), round(c[1], 7)) for c in feature["geometry"]["coordinates"]
        )
        assert segment.coordinates == expected
        assert all(isinstance(point, tuple) and len(point) == 2 for point in segment.coordinates)
        assert len(segment.coordinates) >= 2
    # the segment with the junk elevation (~2e-5): still just two numbers per point
    junk = result.items[3]
    assert junk.coordinates[0] == (77.4080814, 18.4527625)  # rounded to 7 places


def test_parse_traverse_msl_multilinestring_becomes_five_segments_with_length_on_the_first() -> (
    None
):
    raw = fixture_json("curiosity", "traverse")["features"][0]
    assert raw["geometry"]["type"] == "MultiLineString"
    result = parse_traverse(fixture_bytes("curiosity", "traverse"))

    parts = result.items[:5]
    assert [s.seq for s in result.items] == list(range(10))
    assert [len(s.coordinates) for s in parts] == [7, 50, 278, 63, 50]
    assert [s.length_m for s in parts] == [935.64, None, None, None, None]
    assert [s.sol for s in parts] == [None] * 5
    for part, raw_part in zip(parts, raw["geometry"]["coordinates"], strict=True):
        assert part.coordinates == tuple((round(c[0], 7), round(c[1], 7)) for c in raw_part)
    assert parts[0].coordinates[1] == (137.4416472, -4.5894721)  # rounded to 7 places


def test_parse_traverse_msl_linestrings_have_no_sol_and_a_capitalised_length() -> None:
    result = parse_traverse(fixture_bytes("curiosity", "traverse"))

    rest = result.items[5:]
    assert [s.length_m for s in rest] == [26.55, 13.86, 13.86, 35.66, 30.95]
    assert [s.sol for s in rest] == [None] * 5
    assert result.max_sol is None
    assert result.dropped == 0


# --- synthetic traverse ------------------------------------------------------------------------


def test_edge_traverse_drops_short_parts_and_other_geometries_and_keeps_order() -> None:
    result = parse_traverse(synthetic_bytes("mmgis_traverse_edge.json"))

    # dropped: a one-point LineString, a Polygon, a one-point part of the MultiLineString
    assert result.dropped == 3
    assert [s.seq for s in result.items] == [0, 1, 2, 3]
    assert [(s.sol, s.length_m) for s in result.items] == [
        (60, 12.5),
        (60, None),
        (None, 3.25),
        (70, 7.5),
    ]
    assert result.max_sol == 70
    assert result.items[0].coordinates == ((10.0, 1.0), (10.1, 1.1))
    assert result.items[1].coordinates == ((10.3, 1.3), (10.4, 1.4), (10.5, 1.5))


def test_edge_traverse_longitude_above_180_is_shifted_and_rounded_to_seven_places() -> None:
    segment = parse_traverse(synthetic_bytes("mmgis_traverse_edge.json")).items[2]

    assert segment.coordinates[0] == pytest.approx((-169.8765432, 5.9876543), abs=1e-9)
    assert segment.coordinates[1] == pytest.approx((-169.5, 6.0), abs=1e-9)


# --- bad documents -----------------------------------------------------------------------------


BAD_DOCUMENTS = [
    pytest.param(b"", id="empty"),
    pytest.param(b"<html>Service Unavailable</html>", id="html"),
    pytest.param(b"[]", id="json-array"),
    pytest.param(b'"text"', id="json-string"),
    pytest.param(to_body({"type": "Feature", "properties": {}}), id="feature-not-collection"),
    pytest.param(to_body({"type": "GeometryCollection", "features": []}), id="other-type"),
    pytest.param(to_body({"features": []}), id="no-type"),
    pytest.param(b"\xff\xfe\x00", id="not-utf8"),
]


@pytest.mark.parametrize("body", BAD_DOCUMENTS)
@pytest.mark.parametrize("parser", [parse_waypoints, parse_traverse], ids=["waypoints", "traverse"])
def test_parse_not_a_feature_collection_raises_location_format_error(
    parser: Any, body: bytes
) -> None:
    with pytest.raises(LocationFormatError):
        parser(body)


def test_location_format_error_is_an_upstream_error() -> None:
    assert issubclass(LocationFormatError, UpstreamError)


# --- LocationAdapter ---------------------------------------------------------------------------


@pytest.fixture
async def adapter(fake_clock: FakeClock) -> Any:
    settings = make_settings(
        location_base_url=BASE_URL, location_max_bytes=1024, upstream_max_retries=0
    )
    client = UpstreamClient(settings, sleep=fake_clock.sleep, clock=fake_clock)
    try:
        yield LocationAdapter(client, settings)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("rover", "kind", "url"),
    [
        (
            "perseverance",
            "waypoints",
            "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json",
        ),
        (
            "perseverance",
            "traverse",
            "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json",
        ),
        (
            "curiosity",
            "waypoints",
            "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_waypoints.json",
        ),
        (
            "curiosity",
            "traverse",
            "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json/MSL_traverse.json",
        ),
    ],
    ids=["m20-waypoints", "m20-traverse", "msl-waypoints", "msl-traverse"],
)
def test_adapter_url_is_base_code_layers_json(
    adapter: Any, rover: str, kind: str, url: str
) -> None:
    assert adapter.url(rover, kind) == url == file_url(rover, kind)


async def test_adapter_fetch_returns_body_and_validators(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.get(file_url("curiosity", "waypoints")).respond(
        200,
        content=b'{"type":"FeatureCollection","features":[]}',
        headers={
            "ETag": '"abc"',
            "Last-Modified": "Fri, 13 Nov 2026 19:13:22 GMT",
        },
    )

    result = await adapter.fetch("curiosity", "waypoints")

    assert isinstance(result, ConditionalBody)
    assert result.not_modified is False
    assert result.body == b'{"type":"FeatureCollection","features":[]}'
    assert (result.etag, result.last_modified) == ('"abc"', "Fri, 13 Nov 2026 19:13:22 GMT")
    assert "if-none-match" not in route.calls.last.request.headers


async def test_adapter_fetch_sends_stored_validators_and_reports_304(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.get(file_url("perseverance", "traverse")).respond(304)

    result = await adapter.fetch(
        "perseverance",
        "traverse",
        etag='"abc"',
        last_modified="Wed, 28 Oct 2026 15:05:03 GMT",
    )

    request = route.calls.last.request
    assert request.headers["if-none-match"] == '"abc"'
    assert request.headers["if-modified-since"] == "Wed, 28 Oct 2026 15:05:03 GMT"
    assert result.not_modified is True and result.body is None


async def test_adapter_fetch_body_over_location_max_bytes_is_an_upstream_error(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.get(file_url("perseverance", "waypoints")).respond(
        200, content=b"x" * 5000
    )

    with pytest.raises(UpstreamError):
        await adapter.fetch("perseverance", "waypoints")  # location_max_bytes = 1024

    assert route.call_count == 1


async def test_adapter_fetch_redirect_to_another_host_is_refused(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(file_url("perseverance", "waypoints")).respond(
        302, headers={"Location": "https://evil.example.test/M20_waypoints.json"}
    )
    evil = upstream_router.get("https://evil.example.test/M20_waypoints.json").respond(
        200, content=b"{}"
    )

    with pytest.raises(UpstreamError):
        await adapter.fetch("perseverance", "waypoints")

    assert evil.call_count == 0


async def test_adapter_fetch_redirect_to_http_downgrade_is_refused(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    target = "http://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json"
    upstream_router.get(file_url("perseverance", "waypoints")).respond(
        302, headers={"Location": target}
    )
    hop = upstream_router.get(target).respond(200, content=b"{}")

    with pytest.raises(UpstreamError):
        await adapter.fetch("perseverance", "waypoints")

    assert hop.call_count == 0


async def test_adapter_fetch_500_is_an_upstream_error(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(file_url("curiosity", "traverse")).respond(500)

    with pytest.raises(UpstreamError):
        await adapter.fetch("curiosity", "traverse")


async def test_adapter_fetch_network_error_is_an_upstream_error(
    adapter: Any, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(file_url("curiosity", "traverse")).mock(
        side_effect=httpx.ReadTimeout("slow")
    )

    with pytest.raises(UpstreamError):
        await adapter.fetch("curiosity", "traverse")


def test_parse_accepts_a_body_produced_by_json_dumps_of_the_fixture() -> None:
    data = fixture_json("perseverance", "waypoints")

    assert len(parse_waypoints(json.dumps(data).encode()).items) == len(data["features"])
