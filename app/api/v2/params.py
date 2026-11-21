"""Typed v2 parameters with strict parsing (docs/API.md, "Общие правила v2").

Pydantic's lax mode would accept "1.0", "+1", "1_000" as integers and a Unix timestamp as a
date, and refuses digit strings over 4300 characters with its own error. The validators below
accept only plain decimal integers and ``YYYY-MM-DD``; the OpenAPI types stay integer/date.
Bounds are checked by ``Query(ge=..., le=...)`` and turn into 422 ``validation_error``.
"""

import re
from datetime import date
from typing import Annotated

from fastapi import Depends, Path, Query
from pydantic import BeforeValidator

from app.core.params import parse_strict_int
from app.services.v2 import SAMPLE_TYPES, PageRequest, PhotoSort

MAX_SOL = 2**31 - 1
MAX_FRAME_SIZE = 32_767
MAX_PAGE = 1_000_000
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 25

# Fixed-width pattern: no quantifier overlap, linear on any input.
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _strict_int(value: object) -> object:
    """Signed decimal string → int; more than 19 digits saturate to ±2**63 (out of range)."""
    if not isinstance(value, str):
        return value
    return parse_strict_int(value)


def _iso_date(value: object) -> object:
    if not isinstance(value, str):
        return value
    if _ISO_DATE_RE.fullmatch(value) is None:
        raise ValueError("must be a date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be a valid calendar date") from None


_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
_TIME_RE = re.compile(_TIME_PATTERN)


def _hhmm(value: object) -> object:
    if isinstance(value, str) and _TIME_RE.fullmatch(value) is None:
        raise ValueError("must be a time in HH:MM format, 00:00-23:59")
    return value


_DECIMAL_RE = re.compile(r"[0-9]{1,10}(?:\.[0-9]{1,10})?")

# Same language as the strict check of ``app.services.v2.parse_near`` (shown in OpenAPI only:
# validation stays there so the error keeps its message and field).
_NEAR_PATTERN = r"^[+-]?[0-9]{1,3}(\.[0-9]{1,15})?,[+-]?[0-9]{1,3}(\.[0-9]{1,15})?$"


def _plain_decimal(value: object) -> object:
    """Unsigned plain decimal ("1", "0.5") → float; no exponent, "nan", "inf" or "_"."""
    if not isinstance(value, str):
        return value
    if _DECIMAL_RE.fullmatch(value) is None:
        raise ValueError("must be a decimal number, e.g. 1 or 0.5")
    return float(value)


StrictInt = BeforeValidator(_strict_int)

SolParam = Annotated[int | None, Query(ge=0, le=MAX_SOL), StrictInt]
EarthDateParam = Annotated[
    date | None,
    Query(description="Real capture date (UTC), YYYY-MM-DD; not combinable with sol."),
    BeforeValidator(_iso_date),
]
CameraParam = Annotated[
    str | None, Query(description="Exact camera code, case-insensitive (see /cameras).")
]
# Stage 18B: ranges, sort, several cameras and rovers (docs/API.md, GET /photos).
SolMinParam = Annotated[
    int | None,
    Query(ge=0, le=MAX_SOL, description="First sol, inclusive; not combinable with sol."),
    StrictInt,
]
SolMaxParam = Annotated[
    int | None,
    Query(ge=0, le=MAX_SOL, description="Last sol, inclusive; not combinable with sol."),
    StrictInt,
]
DateMinParam = Annotated[
    date | None,
    Query(description="First real capture date (UTC), YYYY-MM-DD, inclusive."),
    BeforeValidator(_iso_date),
]
DateMaxParam = Annotated[
    date | None,
    Query(description="Last real capture date (UTC), YYYY-MM-DD, inclusive."),
    BeforeValidator(_iso_date),
]
SiteMinParam = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="First stop, inclusive."), StrictInt
]
SiteMaxParam = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="Last stop, inclusive."), StrictInt
]
CameraListParam = Annotated[
    str | None,
    Query(description="Up to 10 camera codes separated by commas, case-insensitive."),
]
SortParam = Annotated[
    PhotoSort,
    Query(
        description=(
            "-sol: sol DESC, taken_at DESC, id DESC (default); sol: ascending; "
            "-taken_at: taken_at DESC, id DESC; taken_at: ascending."
        )
    ),
]
RoversParam = Annotated[
    str | None,
    Query(description="Rover slugs separated by commas, case-insensitive; default: all rovers."),
]
# Stage 18B-2: local solar time window, golden hour, minimum frame size.
LocalTimeParam = Annotated[
    str | None,
    Query(
        json_schema_extra={"pattern": _TIME_PATTERN},
        description=(
            "Local solar time HH:MM (00:00-23:59); the window is min <= t < max, "
            "over midnight when min > max."
        ),
    ),
    BeforeValidator(_hhmm),
]
MinSizeParam = Annotated[
    int | None,
    Query(ge=1, le=MAX_FRAME_SIZE, description="Full frame size in pixels, at least."),
    StrictInt,
]
# Stage 18B-3: photos taken near a point (curiosity / perseverance only).
NearParam = Annotated[
    str | None,
    Query(
        json_schema_extra={"pattern": _NEAR_PATTERN, "maxLength": 64},
        description=(
            "LAT,LON in degrees (lat -90..90, lon -180..180, east positive): photos taken at "
            "stops within radius_km of the point; one rover (curiosity or perseverance)."
        ),
        examples=["18.4327,77.4414"],
    ),
]
RadiusKmParam = Annotated[
    float | None,
    Query(
        ge=0.01,
        le=50,
        description=(
            "Radius in km for near, 0.01-50 (default 1); not without near. A plain decimal "
            "such as 1 or 0.5: no sign, exponent, nan or inf (422 otherwise)."
        ),
    ),
    BeforeValidator(_plain_decimal),
]
SampleTypeParam = Annotated[
    str | None,
    Query(description=f"One of {', '.join(SAMPLE_TYPES)} (case-insensitive); default: all."),
]
# A stop of the rover: ``site`` and an inclusive ``drive`` range (docs/API.md, /photos).
SiteParam = Annotated[int | None, Query(ge=0, le=MAX_SOL, description="Stop number."), StrictInt]
DriveParam = Annotated[int | None, Query(ge=0, le=MAX_SOL), StrictInt]
FlightNumberPath = Annotated[int, Path(description="Flight number, from 1."), StrictInt]
SampleNumberPath = Annotated[int, Path(description="Sample number, from 1."), StrictInt]
PhotoIdPath = Annotated[int, Path(), StrictInt]


def _strict_bool(value: object) -> object:
    """``true``/``false``/``1``/``0`` (case-insensitive); anything else is rejected."""
    if not isinstance(value, str):
        return value
    lowered = value.lower()
    if lowered in ("true", "1"):
        return True
    if lowered in ("false", "0"):
        return False
    raise ValueError("must be true, false, 1 or 0")


LatestParam = Annotated[
    bool,
    Query(description="true: only the last stop; not combinable with sol, from_sol, to_sol."),
    BeforeValidator(_strict_bool),
]


GoldenHourParam = Annotated[
    bool,
    Query(description="true: local time 05:30-07:00 or 17:00-18:30; not with local_time_*."),
    BeforeValidator(_strict_bool),
]


def page_request(
    page: Annotated[int, Query(ge=1, le=MAX_PAGE), StrictInt] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PER_PAGE), StrictInt] = DEFAULT_PER_PAGE,
) -> PageRequest:
    return PageRequest(page=page, per_page=per_page)


Pagination = Annotated[PageRequest, Depends(page_request)]
