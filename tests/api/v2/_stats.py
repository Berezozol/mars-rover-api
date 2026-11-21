"""Reference model and datasets for stage 18C (`/photos/stats`, `/photos/batch`).

The model (`expected_stats`) implements docs/API.md "GET /api/v2/photos/stats" independently of
the application: it takes the plain rows that were seeded, applies the filters and groups them.
Dates in stats are SOL dates: the earliest `earth_date` of the whole sol, over all its photos
(`sol_summaries.earth_date`), not the date of each photo.

`PERIODS` is a small dataset that spans several months and years and a sol that crosses UTC
midnight at the end of a month (used with `clean_db`).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

ROVER_ORDER = ["curiosity", "ingenuity", "opportunity", "perseverance", "spirit"]  # /rovers order

GROUP_KEYS = [
    "rover",
    "camera",
    "period",
    "total_photos",
    "sols_with_photos",
    "first_sol",
    "last_sol",
    "first_date",
    "last_date",
]
TOP_KEYS = ["group_by", "total_photos", "sols_with_photos", "first_date", "last_date", "groups"]


@dataclass(frozen=True)
class SRow:
    rover: str
    sol: int
    camera: str
    sample_type: str
    earth_date: date


def expected_stats(
    rows: Iterable[SRow],
    camera_order: dict[str, list[str]],
    *,
    rovers: Sequence[str] | None = None,
    cameras: Sequence[str] | None = None,
    sol_min: int | None = None,
    sol_max: int | None = None,
    date_min: date | None = None,
    date_max: date | None = None,
    full_only: bool = False,
    group_by: str = "rover",
) -> dict[str, Any]:
    rows = list(rows)
    sol_date: dict[tuple[str, int], date] = {}
    for row in rows:
        key = (row.rover, row.sol)
        sol_date[key] = min(sol_date.get(key, row.earth_date), row.earth_date)

    def selected(row: SRow) -> bool:
        day = sol_date[(row.rover, row.sol)]
        return (
            (rovers is None or row.rover in rovers)
            and (cameras is None or row.camera in cameras)
            and (not full_only or row.sample_type == "full")
            and (sol_min is None or row.sol >= sol_min)
            and (sol_max is None or row.sol <= sol_max)
            and (date_min is None or day >= date_min)
            and (date_max is None or day <= date_max)
        )

    chosen = [row for row in rows if selected(row)]

    def group_key(row: SRow) -> tuple[str, str | None, str | None]:
        day = sol_date[(row.rover, row.sol)]
        if group_by == "camera":
            return (row.rover, row.camera, None)
        if group_by == "year":
            return (row.rover, None, f"{day:%Y}")
        if group_by == "month":
            return (row.rover, None, f"{day:%Y-%m}")
        return (row.rover, None, None)

    buckets: dict[tuple[str, str | None, str | None], list[SRow]] = defaultdict(list)
    for row in chosen:
        buckets[group_key(row)].append(row)

    def order(key: tuple[str, str | None, str | None]) -> tuple[int, int, str]:
        rover, camera, period = key
        cam = camera_order[rover].index(camera) if camera is not None else 0
        return (ROVER_ORDER.index(rover), cam, period or "")

    groups: list[dict[str, Any]] = []
    for key in sorted(buckets, key=order):
        members = buckets[key]
        sols = sorted({m.sol for m in members})
        dates = [sol_date[(key[0], sol)] for sol in sols]
        groups.append(
            {
                "rover": key[0],
                "camera": key[1],
                "period": key[2],
                "total_photos": len(members),
                "sols_with_photos": len(sols),
                "first_sol": sols[0],
                "last_sol": sols[-1],
                "first_date": min(dates).isoformat(),
                "last_date": max(dates).isoformat(),
            }
        )
    all_sols = {(r.rover, r.sol) for r in chosen}
    all_dates = [sol_date[key] for key in all_sols]
    return {
        "group_by": group_by,
        "total_photos": len(chosen),
        "sols_with_photos": len(all_sols),
        "first_date": min(all_dates).isoformat() if all_dates else None,
        "last_date": max(all_dates).isoformat() if all_dates else None,
        "groups": groups,
    }


# --- PERIODS dataset ---------------------------------------------------------------------------


def at(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# (rover, sol, camera, sample_type, first taken_at, number of photos one minute apart)
PERIODS_SPEC: list[tuple[str, int, str, str, datetime, int]] = [
    # Sol 1000 starts on 2023-12-31 and crosses UTC midnight: its sol date is 2023-12-31.
    ("perseverance", 1000, "NAVCAM_LEFT", "full", at(2023, 12, 31, 22, 0), 2),
    ("perseverance", 1000, "MCZ_LEFT", "subframe", at(2024, 1, 1, 1, 0), 1),
    ("perseverance", 1001, "NAVCAM_LEFT", "full", at(2024, 1, 1), 3),
    ("perseverance", 1002, "NAVCAM_LEFT", "full", at(2024, 1, 15), 1),
    ("perseverance", 1002, "MCZ_LEFT", "full", at(2024, 1, 15, 13), 2),
    # Only subframes: not counted when `sample_type=full`.
    ("perseverance", 1003, "NAVCAM_LEFT", "subframe", at(2024, 2, 10), 2),
    ("perseverance", 1004, "MCZ_LEFT", "full", at(2025, 1, 5), 1),
    ("curiosity", 4000, "FHAZ", "full", at(2024, 1, 31), 2),
    ("curiosity", 4001, "NAVCAM", "full", at(2024, 2, 1), 1),
]


def periods_records() -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for rover, sol, camera, sample_type, start, count in PERIODS_SPEC:
        for n in range(count):
            taken_at = start + timedelta(minutes=n)
            out.append(
                (
                    rover,
                    build_record(
                        sol=sol,
                        camera_code=camera,
                        sample_type=sample_type,
                        taken_at=taken_at,
                        earth_date=taken_at.date(),
                    ),
                )
            )
    return out


def periods_rows() -> list[SRow]:
    return [
        SRow(rover, r.sol, r.camera_code, r.sample_type, r.earth_date)
        for rover, r in periods_records()
    ]


async def seed_records(factory: async_sessionmaker[AsyncSession], pairs: Any) -> None:
    """Commit (rover, record) pairs in the order the sync does."""
    by_rover: dict[str, list[Any]] = defaultdict(list)
    for rover, record in pairs:
        by_rover[rover].append(record)
    for rover, records in by_rover.items():
        async with factory() as session:
            await upsert_photos(session, rover, records)
            await recompute_sol_summaries(session, rover, {r.sol for r in records})
            await refresh_rover_stats(session, rover)
            await session.commit()


async def photo_ids(factory: async_sessionmaker[AsyncSession]) -> list[int]:
    async with factory() as session:
        return list((await session.scalars(text("SELECT id FROM photos ORDER BY id"))).all())
