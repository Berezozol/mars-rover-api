"""MMGIS rover position files: waypoints (stops) and traverse (route lines), stage 11.

The files are GeoJSON FeatureCollections (docs/DATA-SOURCES.md, "Положение (MMGIS)").
Only this module knows their format: it downloads them with a conditional request and
normalizes them to ``WaypointRecord`` / ``TraverseSegment`` using an explicit whitelist
of properties; the elevation of the coordinates is dropped (it is partly garbage).
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.core.errors import NoSourceForRover
from app.sources.http import ConditionalBody, UpstreamClient, UpstreamError
from app.sources.normalize import clean, to_int

# rover slug -> MMGIS mission code in the file names.
LOCATION_MISSIONS: dict[str, str] = {"perseverance": "M20", "curiosity": "MSL"}

Kind = Literal["waypoints", "traverse"]
KINDS: tuple[Kind, ...] = ("waypoints", "traverse")

# The source's "no value" marker in numeric columns.
_SENTINEL = -9999.0
COORDINATE_DIGITS = 7
_MAX_INT4 = 2**31 - 1

_WAYPOINT_OPTIONAL = (
    "elev_geoid",
    "easting",
    "northing",
    "roll",
    "pitch",
    "yaw",
    "tilt",
    "dist_m",
    "dist_total_m",
)


class LocationFormatError(UpstreamError):
    """The file is not a GeoJSON FeatureCollection (or not JSON at all)."""

    def __init__(self, message: str, url: str = "") -> None:
        super().__init__(url, None, message)


@dataclass(frozen=True)
class WaypointRecord:
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    elev_geoid: float | None = None
    easting: float | None = None
    northing: float | None = None
    roll: float | None = None
    pitch: float | None = None
    yaw: float | None = None
    tilt: float | None = None
    dist_m: float | None = None
    dist_total_m: float | None = None


@dataclass(frozen=True)
class TraverseSegment:
    seq: int
    sol: int | None
    length_m: float | None
    # ((lon, lat), ...), at least two points.
    coordinates: tuple[tuple[float, float], ...]


@dataclass
class ParsedDataset[T]:
    items: list[T]
    dropped: int
    # Highest sol among the items; None when none has a sol.
    max_sol: int | None


# --- parsing --------------------------------------------------------------------------------


def _features(body: bytes) -> list[Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise LocationFormatError("not valid JSON") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise LocationFormatError("root is not a FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list):
        raise LocationFormatError("FeatureCollection has no features list")
    return features


def _number(value: Any) -> float | None:
    """A finite float from a number or numeric string; None for the empty/"UNK"/-9999 values."""
    value = clean(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number == _SENTINEL:
        return None
    return number


def _integer(value: Any, field: str) -> int | None:
    """An int that fits the database column (0..2**31-1); anything else is None."""
    number = to_int(value, field=field)
    if number is None or not 0 <= number <= _MAX_INT4:
        return None
    return number


def _longitude(lon: float) -> float:
    """Normalize to -180..180 (the files use 0..180 east, so this is only a guard)."""
    if lon > 180:
        return lon - 360
    if lon < -180:
        return lon + 360
    return lon


def _properties(feature: Any, geometry_types: tuple[str, ...]) -> tuple[dict[str, Any], Any] | None:
    """``(properties, geometry)`` of a feature with an accepted geometry type, else None."""
    if not isinstance(feature, dict):
        return None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in geometry_types:
        return None
    props = feature.get("properties")
    return (props if isinstance(props, dict) else {}), geometry


def _waypoint(feature: Any) -> WaypointRecord | None:
    parsed = _properties(feature, ("Point",))
    if parsed is None:
        return None
    props, _ = parsed
    sol = _integer(props.get("sol"), "sol")
    site = _integer(props.get("site"), "site")
    drive = _integer(props.get("drive"), "drive")
    lat, lon = _number(props.get("lat")), _number(props.get("lon"))
    if None in (sol, site, drive, lat, lon):
        return None
    assert sol is not None and site is not None and drive is not None  # for the type checker
    assert lat is not None and lon is not None
    if not -90 <= lat <= 90:
        return None
    optional = {name: _number(props.get(name)) for name in _WAYPOINT_OPTIONAL}
    return WaypointRecord(sol=sol, site=site, drive=drive, lat=lat, lon=_longitude(lon), **optional)


def parse_waypoints(body: bytes) -> ParsedDataset[WaypointRecord]:
    """Whitelisted fields only; a repeated (site, drive) keeps the higher sol (else the last)."""
    features = _features(body)
    unique: dict[tuple[int, int], WaypointRecord] = {}
    dropped = 0
    for feature in features:
        record = _waypoint(feature)
        if record is None:
            dropped += 1
            continue
        key = (record.site, record.drive)
        previous = unique.get(key)
        if previous is not None:
            dropped += 1
            if record.sol < previous.sol:
                continue
        unique[key] = record
    items = list(unique.values())
    return ParsedDataset(items, dropped, max((r.sol for r in items), default=None))


def _line(points: Any) -> tuple[tuple[float, float], ...] | None:
    """``((lon, lat), ...)`` from a coordinate list, z dropped; None if invalid or < 2 points."""
    if not isinstance(points, list) or len(points) < 2:
        return None
    line: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            return None
        lon, lat = _number(point[0]), _number(point[1])
        if lon is None or lat is None or not -90 <= lat <= 90:
            return None
        line.append((round(_longitude(lon), COORDINATE_DIGITS), round(lat, COORDINATE_DIGITS)))
    return tuple(line)


def _length(props: dict[str, Any]) -> float | None:
    value = _number(props.get("length"))
    return value if value is not None else _number(props.get("Length"))


def parse_traverse(body: bytes) -> ParsedDataset[TraverseSegment]:
    """LineStrings as they are, a MultiLineString split into one segment per part.

    A split feature keeps its sol in every part; its length goes to the first kept part.
    """
    features = _features(body)
    segments: list[TraverseSegment] = []
    dropped = 0
    for feature in features:
        parsed = _properties(feature, ("LineString", "MultiLineString"))
        if parsed is None:
            dropped += 1
            continue
        props, geometry = parsed
        sol = _integer(props.get("sol"), "sol")
        length = _length(props)
        coordinates = geometry.get("coordinates")
        parts = coordinates if geometry["type"] == "MultiLineString" else [coordinates]
        if not isinstance(parts, list):
            dropped += 1
            continue
        for part in parts:
            line = _line(part)
            if line is None:
                dropped += 1
                continue
            segments.append(TraverseSegment(len(segments), sol, length, line))
            length = None
    sols = [s.sol for s in segments if s.sol is not None]
    return ParsedDataset(segments, dropped, max(sols, default=None))


# --- download -------------------------------------------------------------------------------


class LocationAdapter:
    """Downloads the MMGIS files of one rover with a conditional request."""

    def __init__(self, client: UpstreamClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def url(self, rover_id: str, kind: Kind) -> str:
        code = LOCATION_MISSIONS.get(rover_id.lower())
        if code is None:
            raise NoSourceForRover(rover_id)
        base = self._settings.location_base_url.rstrip("/")
        return f"{base}/{code}/Layers/json/{code}_{kind}.json"

    async def fetch(
        self,
        rover_id: str,
        kind: Kind,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> ConditionalBody:
        """The file, or ``not_modified`` if the validators still match; only the configured
        host is accepted (also after redirects), the body is capped at LOCATION_MAX_BYTES.
        """
        return await self._client.get_conditional(
            self.url(rover_id, kind),
            max_bytes=self._settings.location_max_bytes,
            allowed_origin=self._settings.location_base_url,
            etag=etag,
            last_modified=last_modified,
        )
