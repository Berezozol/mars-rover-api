"""Source adapter protocol and the source-independent photo record it produces."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, Self

logger = logging.getLogger(__name__)


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


@dataclass
class SolStats:
    """What the source returned for one sol in one fetch.

    ``received``: raw records the source returned for the sol (thumbnails included);
    ``dropped``: received but not delivered (thumbnails, failed normalization, duplicates,
    wrong sol); ``source_total``: the per-sol total declared by the source, when the feed
    exposes one, else None.
    """

    received: int = 0
    dropped: int = 0
    source_total: int | None = None


@dataclass
class FetchStats:
    """Statistics of one ``fetch_sols(from_sol, to_sol)`` call, filled while iterating.

    ``sols`` gets an entry for every sol whose fetch is finished: delivered sols, sols whose
    records were all dropped and, once the whole range is fetched (``complete``), sols the
    source has nothing for (``received == 0``). ``range_total`` is the total the source
    declared for the whole range, when known. ``unattributed_dropped`` counts records whose
    sol is missing or outside the range.
    """

    sols: dict[int, SolStats] = field(default_factory=dict)
    range_total: int | None = None
    complete: bool = False
    unattributed_dropped: int = 0

    def sol(self, sol: int) -> SolStats:
        return self.sols.setdefault(sol, SolStats())

    def finish(self, from_sol: int, to_sol: int, *, source_total: int | None = None) -> None:
        """Mark the range fetched; sols not seen get an empty entry."""
        for sol in range(from_sol, to_sol + 1):
            self.sols.setdefault(sol, SolStats(source_total=source_total))
        self.complete = True


class SolStream:
    """Async iterator of ``(sol, records)`` that also carries the fetch statistics."""

    def __init__(
        self, iterator: AsyncGenerator[tuple[int, list["PhotoRecord"]]], stats: FetchStats
    ) -> None:
        self._iterator = iterator
        self.stats = stats

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> tuple[int, list["PhotoRecord"]]:
        return await self._iterator.__anext__()

    async def aclose(self) -> None:
        await self._iterator.aclose()


class SourceAdapter(Protocol):
    """Upstream adapter of one rover."""

    rover_id: str

    async def latest_sol(self) -> int:
        """Latest sol known to the source."""
        ...

    async def source_total(self) -> int | None:
        """Total number of records the source declares for the rover (one request)."""
        ...

    def fetch_sols(
        self, from_sol: int, to_sol: int
    ) -> AsyncIterator[tuple[int, list[PhotoRecord]]]:
        """Yield ``(sol, records)`` in descending sol order, only for sols with records.

        Records of one yielded batch have unique ``source_id`` values.

        A sol is normally yielded once; if the upstream returns sols out of order the
        same sol may be yielded again with additional records - consumers must merge
        (upsert), not replace.

        Raises ``ValueError`` (on first iteration) if the range is negative or reversed.
        Raises ``PageLimitExceeded`` when ``upstream_max_pages`` is reached and more pages
        remain, and ``IncompleteResponse`` when the upstream returns fewer items than it
        announced; in both cases the incomplete sol is not yielded, sols yielded before
        stay valid.

        Real adapters return a ``SolStream`` whose ``stats`` (``FetchStats``) report per sol
        how many records the source returned, how many were dropped and the source's own
        total; consumers must treat a plain async iterator (no ``stats``) as "unknown".
        """
        ...


REQUIRED_FIELDS = ("source_id", "sol", "camera_code", "taken_at", "sample_type", "img_full")


def build_record(fields: dict[str, Any], *, label: str) -> PhotoRecord | None:
    """Create a record from parsed fields; None with a warning if a required one is missing."""
    missing = [name for name in REQUIRED_FIELDS if fields.get(name) is None]
    if missing:
        logger.warning("Skipping upstream image %s: missing %s", label, ", ".join(missing))
        return None
    return PhotoRecord(earth_date=fields["taken_at"].date(), **fields)


def dedupe_by_source_id(records: list[PhotoRecord], *, label: str) -> list[PhotoRecord]:
    """Keep the first record of each ``source_id``; warn about every dropped duplicate."""
    seen: set[str] = set()
    unique: list[PhotoRecord] = []
    for record in records:
        if record.source_id in seen:
            logger.warning("Dropping duplicate upstream image %s in %s", record.source_id, label)
            continue
        seen.add(record.source_id)
        unique.append(record)
    return unique


def validate_sol_range(from_sol: int, to_sol: int) -> None:
    """Raise ``ValueError`` for a negative or reversed sol range."""
    if from_sol < 0 or to_sol < 0:
        raise ValueError(f"Sols must be non-negative, got {from_sol}..{to_sol}")
    if from_sol > to_sol:
        raise ValueError(f"from_sol {from_sol} is greater than to_sol {to_sol}")
