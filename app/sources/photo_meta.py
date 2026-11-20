"""Frame size and local solar time read from a stored photo's ``extra``.

The shape of ``extra`` is set by the adapters, so it is also parsed here, in ``sources/``.
Both functions are pure and total: anything missing or malformed gives ``None``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

_DIMENSION_RE = re.compile(r"^\((\d{1,6}),(\d{1,6})\)$", re.ASCII)
_TIME_RE = re.compile(r"^(?:Sol-\d+M)?(\d{2}):(\d{2}):(\d{2})", re.ASCII)

_M2020 = "perseverance"
_MSL = "curiosity"
_MER = frozenset({"spirit", "opportunity"})


@dataclass(frozen=True)
class LocalTime:
    """Local solar time of the capture, ``HH:MM:SS``; mean (lmst) or true (ltst)."""

    time: str
    kind: Literal["lmst", "ltst"]


def frame_size(rover_id: str, extra: Mapping[str, Any]) -> tuple[int, int] | None:
    """``(width, height)`` of the full frame in pixels, or None when unknown."""
    if rover_id == _M2020:
        return _parse_dimension(_mapping(extra.get("extended")).get("dimension"))
    if rover_id in _MER:
        return _positive_pair(extra.get("line_samples"), extra.get("lines"))
    return None


def local_time(rover_id: str, extra: Mapping[str, Any]) -> LocalTime | None:
    """Local solar time of the capture, or None when the source gave none."""
    if rover_id == _M2020:
        return _make_time(extra.get("date_taken_mars"), "lmst")
    if rover_id == _MSL:
        return _make_time(_mapping(extra.get("extended")).get("lmst"), "lmst")
    if rover_id in _MER:
        return _make_time(extra.get("local_true_solar_time"), "ltst")
    return None


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
