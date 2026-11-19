"""Shared helpers for the stage-11 (rover location) tests.

Fixtures: ``tests/fixtures/upstream/mmgis/<code>_<kind>.json`` (trimmed real MMGIS files) and
``tests/fixtures/upstream/synthetic/mmgis_*_edge.json``. Database helpers write the three
location tables with plain SQL (columns per docs/proposals/stage-11-locations.md section 2),
so API tests do not depend on the repository functions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "upstream"
BASE_URL = "https://mars.nasa.gov/mmgis-maps/"
CODES = {"perseverance": "M20", "curiosity": "MSL"}
KINDS = ("waypoints", "traverse")
ROVER_KINDS = [(rover, kind) for rover in CODES for kind in KINDS]

WAYPOINT_KEYS = [
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
WAYPOINT_NULLABLE = WAYPOINT_KEYS[5:]
LOCATION_KEYS = ["sol", "site", "drive", "lat", "lon", "elev_geoid", "exact"]

Factory = async_sessionmaker[AsyncSession]


def file_url(rover: str, kind: str) -> str:
    code = CODES[rover]
    return f"{BASE_URL}{code}/Layers/json/{code}_{kind}.json"


def fixture_path(rover: str, kind: str) -> Path:
    return FIXTURES / "mmgis" / f"{CODES[rover].lower()}_{kind}.json"


def fixture_bytes(rover: str, kind: str) -> bytes:
    return fixture_path(rover, kind).read_bytes()


def fixture_json(rover: str, kind: str) -> dict[str, Any]:
    return json.loads(fixture_bytes(rover, kind))


def synthetic_bytes(name: str) -> bytes:
    return (FIXTURES / "synthetic" / name).read_bytes()


def to_body(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data).encode("utf-8")


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def etag_of(body: bytes) -> str:
    return f'"{sha256(body)[:32]}"'


def feature_collection(features: Sequence[Any], base: Mapping[str, Any] | None = None) -> bytes:
    data = dict(base or {"type": "FeatureCollection", "name": "test"})
    data["features"] = list(features)
    return to_body(data)


def ok_response(body: bytes, *, etag: str | None = None, modified: str | None = None) -> Any:
    headers = {
        "ETag": etag or etag_of(body),
        "Last-Modified": modified or "Wed, 28 Oct 2026 15:05:06 GMT",
        "Content-Type": "application/json",
    }
    return httpx.Response(200, content=body, headers=headers)


def mock_files(
    router: respx.MockRouter, bodies: Mapping[tuple[str, str], bytes] | None = None
) -> dict[tuple[str, str], respx.Route]:
    """One route per (rover, kind), answering 200 with the body and ETag/Last-Modified."""
    routes = {}
    for rover, kind in ROVER_KINDS:
        body = (bodies or {}).get((rover, kind)) or fixture_bytes(rover, kind)
        routes[(rover, kind)] = router.get(file_url(rover, kind)).mock(
            return_value=ok_response(body)
        )
    return routes


def total_calls(routes: Mapping[Any, respx.Route]) -> int:
    return sum(route.call_count for route in routes.values())


# --- database ----------------------------------------------------------------------------


async def insert_waypoints(factory: Factory, rover: str, rows: Iterable[Mapping[str, Any]]) -> None:
    async with factory() as session:
        for row in rows:
            values = {"rover_id": rover, **{key: None for key in WAYPOINT_NULLABLE}, **row}
            columns = ["rover_id", *WAYPOINT_KEYS]
            await session.execute(
                text(
                    f"INSERT INTO rover_waypoints ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + c for c in columns)})"
                ),
                values,
            )
        await session.commit()


def waypoint(sol: int, site: int, drive: int, lat: float, lon: float, **extra: Any) -> dict:
    return {"sol": sol, "site": site, "drive": drive, "lat": lat, "lon": lon, **extra}


async def insert_traverse(
    factory: Factory, rover: str, segments: Iterable[Mapping[str, Any]]
) -> None:
    """Segments: dicts with ``coordinates`` and optional ``sol``/``length_m``; seq is the index."""
    async with factory() as session:
        for seq, segment in enumerate(segments):
            await session.execute(
                text(
                    "INSERT INTO rover_traverse_segments "
                    "(rover_id, seq, sol, length_m, coordinates) "
                    "VALUES (:rover, :seq, :sol, :length_m, CAST(:coordinates AS jsonb))"
                ),
                {
                    "rover": rover,
                    "seq": segment.get("seq", seq),
                    "sol": segment.get("sol"),
                    "length_m": segment.get("length_m"),
                    "coordinates": json.dumps(segment["coordinates"]),
                },
            )
        await session.commit()


async def insert_dataset(
    factory: Factory,
    rover: str,
    kind: str,
    *,
    changed_at: datetime | None = None,
    succeeded_at: datetime | None = None,
    checked_at: datetime | None = None,
    items: int = 0,
    dropped: int = 0,
    max_sol: int | None = None,
    last_error: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_sha256: str | None = None,
    created_at: datetime | None = None,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO location_datasets (rover_id, kind, checked_at, succeeded_at, "
                "changed_at, etag, last_modified, content_sha256, items, dropped, max_sol, "
                "last_error, created_at) VALUES (:rover, :kind, :checked_at, :succeeded_at, "
                ":changed_at, "
                ":etag, :last_modified, :sha, :items, :dropped, :max_sol, :last_error, "
                "COALESCE(:created_at, now()))"
            ),
            {
                "rover": rover,
                "kind": kind,
                "checked_at": checked_at,
                "succeeded_at": succeeded_at,
                "changed_at": changed_at,
                "etag": etag,
                "last_modified": last_modified,
                "sha": content_sha256,
                "items": items,
                "dropped": dropped,
                "max_sol": max_sol,
                "last_error": last_error,
                "created_at": created_at,
            },
        )
        await session.commit()


async def rows(factory: Factory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]


async def dataset(factory: Factory, rover: str, kind: str) -> dict[str, Any] | None:
    found = await rows(
        factory,
        "SELECT * FROM location_datasets WHERE rover_id = :r AND kind = :k",
        r=rover,
        k=kind,
    )
    return found[0] if found else None


async def waypoint_rows(factory: Factory, rover: str) -> list[dict[str, Any]]:
    return await rows(
        factory,
        "SELECT * FROM rover_waypoints WHERE rover_id = :r ORDER BY site, drive",
        r=rover,
    )


async def traverse_rows(factory: Factory, rover: str) -> list[dict[str, Any]]:
    return await rows(
        factory,
        "SELECT * FROM rover_traverse_segments WHERE rover_id = :r ORDER BY seq",
        r=rover,
    )


async def count(factory: Factory, table: str, rover: str | None = None) -> int:
    sql = f"SELECT count(*) AS n FROM {table}"
    if rover is not None:
        sql += " WHERE rover_id = :r"
    return (await rows(factory, sql, r=rover))[0]["n"]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def iso_z(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
