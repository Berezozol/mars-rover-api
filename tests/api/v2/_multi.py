"""Dataset and reference model for the cross-rover list (stage 18B-1).

The `multi_world` fixture (tests/api/v2/conftest.py) commits one deterministic dataset over all
five rovers (module scope, same
wipe-before/after rules as `v2_world`; a module using it must not use `clean_db`). The
reference model (`Multi.select`) implements the contract of docs/API.md independently of the
application: filter, then sort, then compare with what the API returns.

Dataset (every rover: sols 100-104, six photos per sol, n = 0..5):
- taken_at = 2021-03-01 + (sol - 100) days + one of 00:00:00, 12:00:00, 23:59:59 (n % 3), so
  photos n and n+3 of one rover share a timestamp, and every rover shares timestamps with every
  other rover (cross-rover ties are resolved by id only);
- camera = the rover's first camera for n < 3, the second one otherwise;
- sample_type cycles full / subframe / downsampled with (n + sol) % 3;
- curiosity and perseverance: site = sol - 90 and drive = 10 * n for even n, NULL otherwise;
- two curiosity photos on sol 105 whose `earth_date` differs from the UTC date of `taken_at`
  (docs/proposals/stage-18b-filters.md D7): the date filter must go by `earth_date`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from tests.conftest import build_record

ALL_ROVERS = ("curiosity", "ingenuity", "opportunity", "perseverance", "spirit")
SORTS = ("-sol", "sol", "-taken_at", "taken_at")

CAMERAS = {
    "curiosity": ("FHAZ", "NAV_LEFT_B"),
    "perseverance": ("NAVCAM_LEFT", "MCZ_LEFT"),
    "spirit": ("NAVCAM", "PANCAM"),
    "opportunity": ("PANCAM", "NAVCAM"),
    "ingenuity": ("HELI_NAV", "HELI_RTE"),
}
SAMPLE_TYPES = ("full", "subframe", "downsampled")
SITE_ROVERS = ("curiosity", "perseverance")
FIRST_SOL, LAST_SOL = 100, 104
DAY0 = date(2021, 3, 1)
CLOCKS = (time(0, 0, 0), time(12, 0, 0), time(23, 59, 59))


@dataclass(frozen=True)
class Row:
    rover: str
    source_id: str
    sol: int
    taken_at: datetime
    earth_date: date
    camera: str
    sample_type: str
    site: int | None
    drive: int | None
    id: int = 0


def build_rows() -> list[Row]:
    rows: list[Row] = []
    for rover in ALL_ROVERS:
        for sol in range(FIRST_SOL, LAST_SOL + 1):
            for n in range(6):
                day = DAY0 + timedelta(days=sol - FIRST_SOL)
                taken_at = datetime.combine(day, CLOCKS[n % 3], tzinfo=UTC)
                has_site = rover in SITE_ROVERS and n % 2 == 0
                rows.append(
                    Row(
                        rover=rover,
                        source_id=f"{rover}-{sol}-{n}",
                        sol=sol,
                        taken_at=taken_at,
                        earth_date=taken_at.date(),
                        camera=CAMERAS[rover][n // 3],
                        sample_type=SAMPLE_TYPES[(n + sol) % 3],
                        site=sol - 90 if has_site else None,
                        drive=10 * n if has_site else None,
                    )
                )
    rows.append(
        Row(
            "curiosity",
            "curiosity-trap-behind",
            105,
            datetime(2021, 3, 7, 0, 30, tzinfo=UTC),
            date(2021, 3, 6),  # earth_date one day before the UTC date of taken_at
            "FHAZ",
            "full",
            None,
            None,
        )
    )
    rows.append(
        Row(
            "curiosity",
            "curiosity-trap-ahead",
            105,
            datetime(2021, 3, 6, 23, 30, tzinfo=UTC),
            date(2021, 3, 7),  # earth_date one day after the UTC date of taken_at
            "FHAZ",
            "full",
            None,
            None,
        )
    )
    return rows


def record_of(row: Row) -> Any:
    return build_record(
        source_id=row.source_id,
        sol=row.sol,
        camera_code=row.camera,
        sample_type=row.sample_type,
        taken_at=row.taken_at,
        earth_date=row.earth_date,
        site=row.site,
        drive=row.drive,
        img_full=f"https://images.example.test/{row.source_id}.png",
        credit="NASA/JPL-Caltech",
        extra={},
    )


_SORT_KEYS: dict[str, tuple[Callable[[Row], tuple[Any, ...]], bool]] = {
    "-sol": (lambda r: (r.sol, r.taken_at, r.id), True),
    "sol": (lambda r: (r.sol, r.taken_at, r.id), False),
    "-taken_at": (lambda r: (r.taken_at, r.id), True),
    "taken_at": (lambda r: (r.taken_at, r.id), False),
}


@dataclass
class Multi:
    rows: list[Row] = field(default_factory=list)
    cameras: dict[tuple[str, str], str] = field(default_factory=dict)  # (rover, code) -> name

    def select(
        self,
        *,
        rovers: Iterable[str] | None = None,
        sort: str = "-sol",
        where: Callable[[Row], bool] | None = None,
    ) -> list[Row]:
        chosen = set(ALL_ROVERS if rovers is None else rovers)
        key, reverse = _SORT_KEYS[sort]
        rows = [r for r in self.rows if r.rover in chosen and (where is None or where(r))]
        return sorted(rows, key=key, reverse=reverse)

    def by_id(self, photo_id: int) -> Row:
        return next(r for r in self.rows if r.id == photo_id)


def ids(rows: Iterable[Row]) -> list[int]:
    return [r.id for r in rows]
