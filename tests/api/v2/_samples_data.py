"""Seed helpers and expected JSON for the stage-13 samples endpoints (API v2).

Rows are written with plain SQL (tests/_samples.py) and photos through the repositories in the
order the sync uses; expected JSON is built from the contract (docs/API.md, Sample), not from the
implementation.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._locations import insert_waypoints, waypoint
from tests._samples import (
    EXPECTED,
    NUMBERS,
    SAMPLE_KEYS,
    SAMPLE_LOCATION_KEYS,
    SOURCE_MODIFIED,
    Factory,
    insert_dataset,
    insert_samples,
    utc,
)
from tests.conftest import build_record

CHANGED_AT = utc(2026, 11, 21, 3, 0, 12)
CHECKED_AT = utc(2026, 11, 22, 3, 0, 5)  # later than CHANGED_AT: `updated_at` is the change

_CAMERA = {"perseverance": "NAVCAM_LEFT", "curiosity": "FHAZ", "ingenuity": "HELI_NAV"}
_counter = iter(range(1, 10_000))


def photo(
    sol: int,
    site: int | None = None,
    drive: int | None = None,
    *,
    sample_type: str = "full",
    rover: str = "perseverance",
) -> Any:
    n = next(_counter)
    taken = datetime(2024, 1, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=n)
    return build_record(
        source_id=f"sample-test-{rover}-{sol}-{n}",
        sol=sol,
        camera_code=_CAMERA[rover],
        sample_type=sample_type,
        taken_at=taken,
        earth_date=taken.date(),
        site=site,
        drive=drive,
        img_full=f"https://images.example.test/{rover}-{sol}-{n}.png",
    )


async def seed_photos(factory: Factory, rover: str, records: list[Any]) -> None:
    async with factory() as session:
        await upsert_photos(session, rover, records)
        await recompute_sol_summaries(session, rover, {r.sol for r in records})
        await refresh_rover_stats(session, rover)
        await session.commit()


async def seed_dataset(factory: Factory, items: int, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "created_at": CHANGED_AT,
        "checked_at": CHECKED_AT,
        "succeeded_at": CHECKED_AT,
        "changed_at": CHANGED_AT,
        "source_modified_at": SOURCE_MODIFIED,
        "content_sha256": "0" * 64,
        "items": items,
    }
    values.update(overrides)
    await insert_dataset(factory, **values)


def sample_json(
    expected: dict[str, Any], total_photos: int | None, location: dict[str, Any] | None
) -> dict[str, Any]:
    """The Sample object of the contract: keys in contract order."""
    data = {**expected, "total_photos": total_photos, "location": location}
    return {key: data[key] for key in SAMPLE_KEYS}


def location_json(row: dict[str, Any], match: str) -> dict[str, Any]:
    data = {
        "sol": row["sol"],
        "site": row["site"],
        "drive": row["drive"],
        "lat": row["lat"],
        "lon": row["lon"],
        "elev_geoid": row.get("elev_geoid"),
        "match": match,
    }
    return {key: data[key] for key in SAMPLE_LOCATION_KEYS}


# --- world A: the seven reference rows ---------------------------------------------------------

STOP_1212 = waypoint(1212, 56, 0, 18.4447, 77.3951, elev_geoid=-2489.7)

# number -> (total_photos, location)
BASIC_EXPECTATIONS: dict[int, tuple[int | None, dict[str, Any] | None]] = {
    1: (3, None),  # sol 164: three photos (one subframe), no waypoint at or before the sol
    8: (0, None),  # sol 371: no photos, no waypoint
    17: (0, None),  # sol 634
    25: (2, location_json(STOP_1212, "exact")),  # sol 1215: both photos at (56, 0)
    26: (0, location_json(STOP_1212, "by_sol")),  # sol 1401: no photos, last stop before it
    29: (None, None),  # sol TBD
    30: (None, None),
}


def basic_expected(number: int) -> dict[str, Any]:
    total, location = BASIC_EXPECTATIONS[number]
    return sample_json(EXPECTED[number], total, location)


def basic_items() -> list[dict[str, Any]]:
    return [basic_expected(n) for n in NUMBERS]


async def seed_basic(factory: Factory) -> None:
    """Reference rows (inserted in scrambled order) + photos + one stop + dataset state."""
    rows = []
    for number in (26, 1, 30, 8, 25, 29, 17):
        row = dict(EXPECTED[number])
        if row["sealed_on"] is not None:
            row["sealed_on"] = date.fromisoformat(row["sealed_on"])
        rows.append(row)
    await insert_samples(factory, rows)
    await seed_photos(
        factory,
        "perseverance",
        [
            photo(164),
            photo(164),
            photo(164, sample_type="subframe"),
            photo(1215, 56, 0),
            photo(1215, 56, 0),
        ],
    )
    # Same sol, another rover: never counted for Perseverance samples.
    await seed_photos(factory, "curiosity", [photo(164, rover="curiosity")])
    await insert_waypoints(factory, "perseverance", [STOP_1212])
    await seed_dataset(factory, items=7)


# --- world B: the location branches ------------------------------------------------------------

W90 = waypoint(90, 9, 0, 18.0, 77.0, elev_geoid=-2000.0)
W99 = waypoint(99, 10, 50, 18.1, 77.1, elev_geoid=-2000.5)
W105 = waypoint(105, 10, 70, 18.15, 77.15, elev_geoid=-2000.75)
W120 = waypoint(120, 12, 0, 18.2, 77.2)  # no elevation
W130 = waypoint(130, 13, 20, 18.3, 77.3, elev_geoid=-2001.5)
# Three stops in one sol: the largest (site, drive) wins for `by_sol`.
W140_A = waypoint(140, 14, 10, 18.41, 77.41, elev_geoid=-2002.0)
W140_B = waypoint(140, 14, 30, 18.42, 77.42, elev_geoid=-2002.5)
W140_C = waypoint(140, 13, 90, 18.43, 77.43)
LOCATION_STOPS = [W140_C, W130, W90, W140_A, W120, W99, W140_B, W105]  # scrambled

# number -> (sol, photos as (site, drive) pairs, expected total_photos, expected location)
LOCATION_CASES: dict[int, dict[str, Any]] = {
    1: {"sol": 100, "pairs": [(10, 50), (10, 50)], "total": 2, "loc": (W99, "exact")},
    2: {"sol": 101, "pairs": [(10, 60)], "total": 1, "loc": (W99, "nearest_before")},
    3: {"sol": 125, "pairs": [(None, None)], "total": 1, "loc": (W120, "by_sol")},
    4: {"sol": 131, "pairs": [(12, 0), (13, 20)], "total": 2, "loc": (W130, "by_sol")},
    5: {"sol": 50, "pairs": [], "total": 0, "loc": None},
    6: {"sol": None, "pairs": [], "total": None, "loc": None},
    7: {"sol": 106, "pairs": [], "total": 0, "loc": (W105, "by_sol")},
    # Several stops in the last sol <= 145: the largest (site, drive) is (14, 30).
    8: {"sol": 145, "pairs": [], "total": 0, "loc": (W140_B, "by_sol")},
    # The only photo pair has no stop of its site (site 20): fall back to by_sol.
    9: {"sol": 150, "pairs": [(20, 5)], "total": 1, "loc": (W140_B, "by_sol")},
    # The pair's site has stops, but none with drive <= 5: fall back to by_sol too.
    10: {"sol": 151, "pairs": [(14, 5)], "total": 1, "loc": (W140_B, "by_sol")},
}


def location_expected(number: int, *, with_stops: bool = True) -> dict[str, Any]:
    case = LOCATION_CASES[number]
    loc = case["loc"] if with_stops else None
    location = None if loc is None else location_json(loc[0], loc[1])
    data = {
        "number": number,
        "name": f"Sample {number}",
        "feature_name": None,
        "sampling_location": None,
        "status": "on_rover",
        "current_location": "Perseverance Rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": None,
        "sealed_on": None,
        "sol": case["sol"],
        "height_cm": None,
        "height_note": None,
        "dossier_url": None,
    }
    return sample_json(data, case["total"], location)


async def seed_location_world(factory: Factory, *, with_stops: bool = True) -> None:
    await insert_samples(
        factory,
        [{"number": n, "sol": case["sol"]} for n, case in reversed(LOCATION_CASES.items())],
    )
    records = [
        photo(case["sol"], site, drive)
        for case in LOCATION_CASES.values()
        for site, drive in case["pairs"]
    ]
    await seed_photos(factory, "perseverance", records)
    if with_stops:
        await insert_waypoints(factory, "perseverance", LOCATION_STOPS)
    await seed_dataset(factory, items=len(LOCATION_CASES))
