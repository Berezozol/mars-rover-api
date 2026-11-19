"""Seed data and expected JSON for the stage-11 location endpoints (API v2).

Rows are written with plain SQL (tests/_locations.py); expected JSON is built from the contract
(docs/API.md, Waypoint / GET .../location / GET .../traverse), not from the implementation.
"""

from typing import Any

from tests._locations import (
    WAYPOINT_KEYS,
    Factory,
    insert_dataset,
    insert_traverse,
    insert_waypoints,
    iso_z,
    utc,
    waypoint,
)

WAYPOINTS_CHANGED = utc(2026, 10, 28, 15, 5, 6)
TRAVERSE_CHANGED = utc(2026, 10, 28, 15, 5, 3)
CURIOSITY_CHANGED = utc(2026, 11, 13, 19, 13, 22)

# Inserted in this (scrambled) order; the API sorts by sol, site, drive.
PERSEVERANCE_WAYPOINTS = [
    waypoint(30, 5, 100, 18.6, 77.6, elev_geoid=-2400.5),
    waypoint(10, 3, 50, 18.3, 77.3),
    waypoint(
        5,
        2,
        0,
        18.1,
        77.1,
        elev_geoid=-2500.0,
        easting=4086431.2,
        northing=1092178.4,
        roll=1.2,
        pitch=-3.4,
        yaw=142.0,
        tilt=3.6,
        dist_m=112.4,
        dist_total_m=33874.9,
    ),
    waypoint(10, 4, 10, 18.4, 77.4),
    waypoint(20, 5, 0, 18.5, 77.5, elev_geoid=-2450.25),
    waypoint(10, 3, 0, 18.2, 77.2),
    waypoint(10, 2, 900, 18.15, 77.15),
]
# (sol, site, drive) in the order the API must return them.
PERSEVERANCE_ORDER = [
    (5, 2, 0),
    (10, 2, 900),
    (10, 3, 0),
    (10, 3, 50),
    (10, 4, 10),
    (20, 5, 0),
    (30, 5, 100),
]
CURIOSITY_WAYPOINTS = [
    waypoint(7, 1, 0, -4.5, 137.4, elev_geoid=-4500.0),
    waypoint(8, 1, 70, -4.6, 137.5),
]

PERSEVERANCE_TRAVERSE = [
    {"sol": 5, "length_m": 23.6, "coordinates": [[77.4509, 18.4447], [77.4511, 18.4449]]},
    {
        "sol": 10,
        "length_m": None,
        "coordinates": [[77.4511, 18.4449], [77.4513, 18.4451], [77.4516, 18.4452]],
    },
    {"sol": None, "length_m": 5.5, "coordinates": [[77.4516, 18.4452], [77.4517, 18.4453]]},
]
CURIOSITY_TRAVERSE = [
    {"sol": None, "length_m": 935.64, "coordinates": [[137.4416, -4.5894], [137.4418, -4.5893]]},
    {"sol": None, "length_m": None, "coordinates": [[137.4418, -4.5893], [137.4421, -4.5894]]},
]


def waypoint_json(row: dict[str, Any]) -> dict[str, Any]:
    """Waypoint object of the contract: all 14 keys in order, missing optional ones null."""
    return {key: row.get(key) for key in WAYPOINT_KEYS}


def expected_waypoints(rows: list[dict[str, Any]], order: list[tuple[int, int, int]]) -> list:
    by_key = {(r["sol"], r["site"], r["drive"]): r for r in rows}
    return [waypoint_json(by_key[key]) for key in order]


def location_response(
    rover: str, items: list[Any], *, updated_at: str | None, source_max_sol: int | None
) -> dict[str, Any]:
    return {
        "rover": rover,
        "available": True,
        "updated_at": updated_at,
        "source_max_sol": source_max_sol,
        "items": items,
    }


def traverse_response(
    rover: str,
    segments: list[dict[str, Any]],
    *,
    updated_at: str | None,
    source_max_sol: int | None,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "rover": rover,
        "available": available,
        "updated_at": updated_at,
        "source_max_sol": source_max_sol,
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": segment["coordinates"]},
                "properties": {"sol": segment.get("sol"), "length_m": segment.get("length_m")},
            }
            for segment in segments
        ],
    }


async def seed_locations(factory: Factory) -> None:
    """Perseverance and Curiosity: waypoints, traverse and the state of the four files."""
    await insert_waypoints(factory, "perseverance", PERSEVERANCE_WAYPOINTS)
    await insert_waypoints(factory, "curiosity", CURIOSITY_WAYPOINTS)
    # Reversed insertion: the API must order by seq, not by insertion.
    await insert_traverse(
        factory,
        "perseverance",
        [
            {**segment, "seq": seq}
            for seq, segment in reversed(list(enumerate(PERSEVERANCE_TRAVERSE)))
        ],
    )
    await insert_traverse(factory, "curiosity", CURIOSITY_TRAVERSE)
    for rover, kind, changed, items, max_sol in [
        ("perseverance", "waypoints", WAYPOINTS_CHANGED, 7, 30),
        ("perseverance", "traverse", TRAVERSE_CHANGED, 3, 10),
        ("curiosity", "waypoints", CURIOSITY_CHANGED, 2, 8),
        ("curiosity", "traverse", CURIOSITY_CHANGED, 2, None),
    ]:
        await insert_dataset(
            factory,
            rover,
            kind,
            checked_at=changed,
            succeeded_at=changed,
            changed_at=changed,
            items=items,
            max_sol=max_sol,
        )


PERSEVERANCE_UPDATED = iso_z(WAYPOINTS_CHANGED)
PERSEVERANCE_TRAVERSE_UPDATED = iso_z(TRAVERSE_CHANGED)
CURIOSITY_UPDATED = iso_z(CURIOSITY_CHANGED)
