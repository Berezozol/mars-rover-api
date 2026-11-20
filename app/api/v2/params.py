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
from app.services.v2 import SAMPLE_TYPES, PageRequest

MAX_SOL = 2**31 - 1
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


def page_request(
    page: Annotated[int, Query(ge=1, le=MAX_PAGE), StrictInt] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PER_PAGE), StrictInt] = DEFAULT_PER_PAGE,
) -> PageRequest:
    return PageRequest(page=page, per_page=per_page)


Pagination = Annotated[PageRequest, Depends(page_request)]
