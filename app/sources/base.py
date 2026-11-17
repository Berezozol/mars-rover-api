"""Source-independent photo record produced by the upstream adapters."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class PhotoRecord:
    """One photo as parsed from a source; the rover is passed separately.

    ``taken_at`` is tz-aware; ``earth_date`` is its UTC date; ``sample_type`` is lower case.
    """

    source_id: str
    sol: int
    camera_code: str
    taken_at: datetime
    earth_date: date
    sample_type: str
    img_full: str
    received_at: datetime | None = None
    img_small: str | None = None
    img_medium: str | None = None
    img_large: str | None = None
    title: str | None = None
    caption: str | None = None
    credit: str | None = None
    source_link: str | None = None
    site: int | None = None
    drive: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
