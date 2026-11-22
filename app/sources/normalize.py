"""Common normalization rules for upstream values: "UNK"/null -> None, numeric strings, UTC."""

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

UNKNOWN_MARKER = "UNK"


def clean(value: Any) -> Any:
    """Strip strings; map None, empty/whitespace strings and "UNK" to None.

    The "UNK" check runs after stripping and is case-sensitive (" UNK " -> None, "unk" stays).
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == UNKNOWN_MARKER:
            return None
        return stripped
    return value


def to_int(value: Any, *, field: str) -> int | None:
    """Convert an int, numeric string or integral float to int; otherwise None with a warning."""
    value = clean(value)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    _unparsable(field, value)
    return None


def parse_datetime_utc(value: Any, *, field: str) -> datetime | None:
    """Parse ISO 8601 into an aware UTC datetime; naive values are treated as UTC."""
    value = clean(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            _unparsable(field, value)
            return None
    else:
        _unparsable(field, value)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def compact(value: Any) -> Any:
    """Recursively drop dict keys whose value is empty (see ``clean``) or becomes empty."""
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            compacted = compact(item)
            if compacted is None or (isinstance(compacted, dict | list) and not compacted):
                continue
            result[key] = compacted
        return result
    if isinstance(value, list):
        return [compact(item) for item in value]
    return clean(value)


def _unparsable(field: str, value: Any) -> None:
    logger.warning("Cannot parse field %s from value %r; using None", field, value)
