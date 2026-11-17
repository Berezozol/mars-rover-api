"""Parsing of v1 query parameters (docs/LEGACY-API.md, "photos" and the deviations table).

Parameters arrive as raw strings so that bad values produce the exact legacy 400 texts.
"""

import re
from datetime import date

from app.core.errors import LegacyParamError
from app.core.params import parse_strict_int

__all__ = [
    "DEFAULT_PER_PAGE",
    "MAX_PER_PAGE",
    "LegacyParamError",
    "legacy_page_window",
    "parse_legacy_date",
    "parse_legacy_int",
]

DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 200

_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")


def parse_legacy_int(value: str, param: str) -> int:
    """Signed decimal integer; anything else raises ``LegacyParamError(param)``.

    Integers with more than 19 significant digits saturate to ±2**63 without int().
    """
    try:
        return parse_strict_int(value)
    except ValueError:
        raise LegacyParamError(param) from None


def parse_legacy_date(value: str) -> date | None:
    """``YYYY-M-D`` with optional leading zeros ("2015-6-3", "2015-06-03"); else None."""
    match = _DATE_RE.fullmatch(value)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def legacy_page_window(page: str | None, per_page: str | None) -> tuple[int, int] | None:
    """(limit, offset) for ``page``/``per_page``; None without ``page`` (no pagination).

    page < 1 → 1; per_page < 1 → 25; per_page > 200 → 200. ``per_page`` is ignored (not even
    validated) without ``page``.
    """
    if page is None:
        return None
    page_number = max(parse_legacy_int(page, "page"), 1)
    size = DEFAULT_PER_PAGE
    if per_page is not None:
        size = parse_legacy_int(per_page, "per_page")
        size = DEFAULT_PER_PAGE if size < 1 else min(size, MAX_PER_PAGE)
    return size, (page_number - 1) * size
