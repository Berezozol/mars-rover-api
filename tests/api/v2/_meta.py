"""Dataset and reference model for the local-time / frame-size filters (stage 18B-2).

The `meta_world` fixture (tests/api/v2/conftest.py) commits one deterministic dataset over all
five rovers through `upsert_photos` (module scope, same wipe rules as `multi_world`). Expected
results come from this module only (filter, then sort by the documented keys); the application is
never consulted.

Dataset: every rover has sols 100 and 101, 14 photos per sol. Photo i of a sol has the local time
`TIMES[i]` (the list holds every boundary of the contract: 05:30 / 07:00 / 17:00 / 18:00 /
18:30, one second before and after; the last item is "no time") and the frame size
`SIZES[(i + sol) % 7]`. The `extra` of every rover has the shape of its source
(docs/DATA-SOURCES.md):
- curiosity: `extended.lmst` and, always, `extended.dimension` (the size must still be ignored);
- perseverance, ingenuity: `date_taken_mars` and `extended.dimension`;
- spirit, opportunity: `local_true_solar_time`, `line_samples`, `lines`.
Missing data is written the way the sources write it ("UNK", null). Fractions of a second alternate
(.000 / .999): the stored time is truncated, never rounded.
Two extra Perseverance photos on sol 102 have a width / height of 32768: `extra` still says so
(the response body shows it), but the stored columns stay NULL and the size filters skip them.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tests.conftest import build_record

ALL_ROVERS = ("curiosity", "ingenuity", "opportunity", "perseverance", "spirit")
SORTS = ("-sol", "sol", "-taken_at", "taken_at")
MER = ("spirit", "opportunity")
LMST_ROVERS = ("curiosity", "perseverance", "ingenuity")

CAMERAS = {
    "curiosity": ("FHAZ", "NAV_LEFT_B"),
    "perseverance": ("NAVCAM_LEFT", "MCZ_LEFT"),
    "spirit": ("NAVCAM", "PANCAM"),
    "opportunity": ("PANCAM", "NAVCAM"),
    "ingenuity": ("HELI_NAV", "HELI_RTE"),
}
TIMES: tuple[str | None, ...] = (
    "00:00:00",
    "05:29:59",
    "05:30:00",
    "06:59:59",
    "07:00:00",
    "12:00:00",
    "16:59:59",
    "17:00:00",
    "17:59:59",
    "18:00:00",
    "18:29:59",
    "18:30:00",
    "23:59:59",
    None,
)
SIZES: tuple[tuple[int, int] | None, ...] = (
    (1920, 1200),
    (1919, 1200),
    (1920, 1199),
    (640, 480),
    (32767, 32767),
    (1, 1),
    None,
)
SOLS = (100, 101)
DAY0 = date(2021, 3, 1)


def seconds_of(clock: str) -> int:
    """`HH:MM` or `HH:MM:SS` -> seconds since local midnight."""
    parts = [int(p) for p in clock.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)


@dataclass(frozen=True)
class MRow:
    rover: str
    source_id: str
    sol: int
    taken_at: datetime
    camera: str
    local_time: str | None  # HH:MM:SS as the source says it (truncated to seconds)
    size: tuple[int, int] | None  # what the size filters see (columns); None = no size
    declared: tuple[int, int] | None  # what `extra` says (the response body shows it)
    extra: dict[str, Any]
    id: int = 0

    @property
    def earth_date(self) -> date:
        return self.taken_at.date()

    @property
    def time_s(self) -> int | None:
        return None if self.local_time is None else seconds_of(self.local_time)


def _extra(
    rover: str,
    sol: int,
    clock: str | None,
    fraction: str,
    dimension: tuple[int, int] | None,
    dimension_text: str | None = None,
) -> dict[str, Any]:
    stamp = None if clock is None else f"Sol-{sol:05d}M{clock}{fraction}"
    if rover == "curiosity":
        # Curiosity has no frame size even when `dimension` is present.
        return {"extended": {"lmst": stamp or "UNK", "dimension": "(1920,1200)"}}
    if rover in ("perseverance", "ingenuity"):
        text = dimension_text or (
            "UNK" if dimension is None else f"({dimension[0]},{dimension[1]})"
        )
        return {"date_taken_mars": stamp or "UNK", "extended": {"dimension": text}}
    return {
        "local_true_solar_time": None if clock is None else f"{clock}{fraction}",
        "line_samples": None if dimension is None else dimension[0],
        "lines": None if dimension is None else dimension[1],
    }


def build_meta_rows() -> list[MRow]:
    rows: list[MRow] = []
    for rover in ALL_ROVERS:
        for sol in SOLS:
            for i, clock in enumerate(TIMES):
                size = SIZES[(i + sol) % 7]
                fraction = ".999" if i % 2 else ".000"
                taken_at = datetime(2021, 3, 1, 12, 0, 0, tzinfo=UTC) + timedelta(
                    days=sol - 100, minutes=i % 4
                )
                rows.append(
                    MRow(
                        rover=rover,
                        source_id=f"{rover}-{sol}-{i}",
                        sol=sol,
                        taken_at=taken_at,
                        camera=CAMERAS[rover][i % 2],
                        local_time=clock,
                        size=None if rover == "curiosity" else size,
                        declared=None if rover == "curiosity" else size,
                        extra=_extra(rover, sol, clock, fraction, size),
                    )
                )
    for tag, text in (("wide", "(32768,100)"), ("tall", "(100,32768)")):
        w, h = (int(v) for v in text.strip("()").split(","))
        rows.append(
            MRow(
                rover="perseverance",
                source_id=f"perseverance-102-{tag}",
                sol=102,
                taken_at=datetime(2021, 3, 3, 12, 0, 0, tzinfo=UTC),
                camera="NAVCAM_LEFT",
                local_time="12:00:00",
                size=None,
                declared=(w, h),
                extra=_extra("perseverance", 102, "12:00:00", ".000", None, text),
            )
        )
    return rows


def record_of(row: MRow) -> Any:
    return build_record(
        source_id=row.source_id,
        sol=row.sol,
        camera_code=row.camera,
        sample_type="full",
        taken_at=row.taken_at,
        earth_date=row.earth_date,
        img_full=f"https://images.example.test/{row.source_id}.png",
        credit="NASA/JPL-Caltech",
        extra=row.extra,
    )


# --- reference predicates ------------------------------------------------------------------


def in_window(row: MRow, lo: str | None, hi: str | None) -> bool:
    """Contract of local_time_min / local_time_max: lo <= t < hi, wrap past midnight if lo > hi."""
    t = row.time_s
    if t is None:
        return False
    a = None if lo is None else seconds_of(lo)
    b = None if hi is None else seconds_of(hi)
    if a is not None and b is not None and a > b:
        return t >= a or t < b
    return (a is None or t >= a) and (b is None or t < b)


def is_golden(row: MRow) -> bool:
    return in_window(row, "05:30", "07:00") or in_window(row, "17:00", "18:30")


def min_size(row: MRow, width: int | None = None, height: int | None = None) -> bool:
    if row.size is None:
        return False
    return (width is None or row.size[0] >= width) and (height is None or row.size[1] >= height)


_SORT_KEYS: dict[str, tuple[Callable[[MRow], tuple[Any, ...]], bool]] = {
    "-sol": (lambda r: (r.sol, r.taken_at, r.id), True),
    "sol": (lambda r: (r.sol, r.taken_at, r.id), False),
    "-taken_at": (lambda r: (r.taken_at, r.id), True),
    "taken_at": (lambda r: (r.taken_at, r.id), False),
}


@dataclass
class MetaWorld:
    rows: list[MRow] = field(default_factory=list)

    def select(
        self,
        *,
        rovers: Iterable[str] | None = None,
        sort: str = "-sol",
        where: Callable[[MRow], bool] | None = None,
    ) -> list[MRow]:
        chosen = set(ALL_ROVERS if rovers is None else rovers)
        key, reverse = _SORT_KEYS[sort]
        rows = [r for r in self.rows if r.rover in chosen and (where is None or where(r))]
        return sorted(rows, key=key, reverse=reverse)


def ids(rows: Iterable[MRow]) -> list[int]:
    return [r.id for r in rows]
