"""Frame size and local solar time read from a stored photo's ``extra``.

The shape of ``extra`` is set by the adapters, so it is also parsed here, in ``sources/``.
All functions are pure and total: anything missing or malformed gives ``None``.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

_DIMENSION_RE = re.compile(r"^\((\d{1,6}),(\d{1,6})\)$", re.ASCII)
_TIME_RE = re.compile(r"^(?:Sol-\d+M)?(\d{2}):(\d{2}):(\d{2})", re.ASCII)

_M2020 = frozenset({"perseverance", "ingenuity"})
_MSL = "curiosity"
_MER = frozenset({"spirit", "opportunity"})


@dataclass(frozen=True)
class LocalTime:
    """Local solar time of the capture, ``HH:MM:SS``; mean (lmst) or true (ltst)."""

    time: str
    kind: Literal["lmst", "ltst"]


def frame_size(rover_id: str, extra: Mapping[str, Any]) -> tuple[int, int] | None:
    """``(width, height)`` of the full frame in pixels, or None when unknown."""
    if rover_id in _M2020:
        return _parse_dimension(_mapping(extra.get("extended")).get("dimension"))
    if rover_id in _MER:
        return _positive_pair(extra.get("line_samples"), extra.get("lines"))
    return None


@dataclass(frozen=True)
class MastAngles:
    """Camera pointing, degrees: azimuth in [0, 360), elevation in [-90, 90]; 0.01-rounded."""

    azimuth: float
    elevation: float


_MAST_KEYS = {"curiosity": ("mast_az", "mast_el"), "perseverance": ("mastAz", "mastEl")}


def mast_angles(rover_id: str, extra: Mapping[str, Any]) -> MastAngles | None:
    """Camera pointing from ``extra['extended']``, or None unless both angles are usable.

    Azimuth is normalized to [0, 360): Perseverance wrote -180..180 in some periods.
    """
    keys = _MAST_KEYS.get(rover_id)
    if keys is None:
        return None
    extended = _mapping(extra.get("extended"))
    azimuth = _finite_number(extended.get(keys[0]))
    elevation = _finite_number(extended.get(keys[1]))
    if azimuth is None or elevation is None:
        return None
    if not (-360 <= azimuth <= 360 and -90 <= elevation <= 90):
        return None
    return MastAngles(_round_angle(azimuth % 360) % 360, _round_angle(elevation))


def _round_angle(value: float) -> float:
    return round(value, 2) + 0.0  # + 0.0 turns -0.0 into 0.0


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, str | int | float):
        return None
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def local_time(rover_id: str, extra: Mapping[str, Any]) -> LocalTime | None:
    """Local solar time of the capture, or None when the source gave none."""
    if rover_id in _M2020:
        return _make_time(extra.get("date_taken_mars"), "lmst")
    if rover_id == _MSL:
        return _make_time(_mapping(extra.get("extended")).get("lmst"), "lmst")
    if rover_id in _MER:
        return _make_time(extra.get("local_true_solar_time"), "ltst")
    return None


@dataclass(frozen=True)
class StoredMeta:
    """Values of the photos columns ``local_time_s``, ``width`` and ``height`` (filters only)."""

    local_time_s: int | None
    width: int | None
    height: int | None


# The ``extra`` paths that ``frame_size`` and ``local_time`` read; the backfill projects only these.
STORED_META_PATHS: tuple[tuple[str, ...], ...] = (
    ("extended", "dimension"),
    ("extended", "lmst"),
    ("date_taken_mars",),
    ("local_true_solar_time",),
    ("line_samples",),
    ("lines",),
)

# photos.width / height are smallint.
MAX_STORED_SIZE = 32767


def stored_meta(rover_id: str, extra: Mapping[str, Any]) -> StoredMeta:
    """Column values derived from ``extra``; width and height are both set or both None.

    ``local_time_s`` is the second of the local solar day (0-86399). A frame size outside
    1..32767 is not stored.
    """
    moment = local_time(rover_id, extra)
    seconds = None if moment is None else _seconds_of_day(moment.time)
    size = frame_size(rover_id, extra)
    if size is None or max(size) > MAX_STORED_SIZE:
        return StoredMeta(seconds, None, None)
    return StoredMeta(seconds, size[0], size[1])


def _seconds_of_day(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_dimension(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _DIMENSION_RE.match(value.strip())
    if match is None:
        return None
    return _positive_pair(int(match.group(1)), int(match.group(2)))


def _positive_pair(width: Any, height: Any) -> tuple[int, int] | None:
    if not (_is_positive_int(width) and _is_positive_int(height)):
        return None
    return int(width), int(height)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _make_time(value: Any, kind: Literal["lmst", "ltst"]) -> LocalTime | None:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if match is None:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return LocalTime(f"{hours:02d}:{minutes:02d}:{seconds:02d}", kind)
