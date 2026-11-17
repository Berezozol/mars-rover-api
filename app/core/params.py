"""Parameter parsing shared by API v1 and v2."""

import re

__all__ = ["parse_strict_int"]

# No overlapping quantifiers: fullmatch stays linear on long non-matching input.
_INT_RE = re.compile(r"-?[0-9]+")
# More significant digits than int64 can hold: saturate instead of calling int(), which
# refuses strings over 4300 digits. Callers treat such values as out of range.
_MAX_DIGITS = 19
_SATURATED = 2**63


def parse_strict_int(value: str) -> int:
    """Signed decimal integer (no "+", spaces, "_" or fractions); else ``ValueError``.

    Integers with more than 19 significant digits saturate to ±2**63 without int().
    """
    if _INT_RE.fullmatch(value) is None:
        raise ValueError("must be an integer")
    negative = value.startswith("-")
    digits = value.lstrip("-").lstrip("0") or "0"
    number = _SATURATED if len(digits) > _MAX_DIGITS else int(digits)
    return -number if negative else number
