"""Mars 2020 adapter (Perseverance; Ingenuity subclasses it) for the raw images feed."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.sources.base import (
    FetchStats,
    PhotoRecord,
    SolStream,
    build_record,
    dedupe_by_source_id,
    validate_sol_range,
)
from app.sources.http import (
    IncompleteResponse,
    PageLimitExceeded,
    SolsFetchFailed,
    UpstreamClient,
    UpstreamError,
)
from app.sources.normalize import clean, compact, parse_datetime_utc, to_int

logger = logging.getLogger(__name__)

THUMBNAIL = "thumbnail"
PERSEVERANCE = "perseverance"
INGENUITY = "ingenuity"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lower(value: Any) -> str | None:
    value = clean(value)
    return value.lower() if isinstance(value, str) else None


def _upper(value: Any) -> str | None:
    value = clean(value)
    return value.upper() if isinstance(value, str) else None


def _without_position(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Ingenuity has no stops: its feed's ``site``/``drive`` (0 and "None", or helicopter
    counters) are not rover positions, so both are dropped silently."""
    return {**raw, "site": None, "drive": None}


def parse_m2020_image(raw: Mapping[str, Any], rover_id: str = PERSEVERANCE) -> PhotoRecord | None:
    """Map one feed image to a record; None for thumbnails and incomplete images."""
    if rover_id == INGENUITY:
        raw = _without_position(raw)
    imageid = clean(raw.get("imageid"))
    sample_type = _lower(raw.get("sample_type"))
    if sample_type == THUMBNAIL:
        logger.debug("Skipping %s thumbnail %s", rover_id, imageid)
        return None
    camera = _mapping(raw.get("camera"))
    files = _mapping(raw.get("image_files"))
    fields: dict[str, Any] = {
        "source_id": str(imageid) if imageid is not None else None,
        "sol": to_int(raw.get("sol"), field="sol"),
        "camera_code": _upper(camera.get("instrument")),
        "taken_at": parse_datetime_utc(raw.get("date_taken_utc"), field="date_taken_utc"),
        "sample_type": sample_type,
        "img_full": clean(files.get("full_res")),
        "received_at": parse_datetime_utc(raw.get("date_received"), field="date_received"),
        "img_small": clean(files.get("small")),
        "img_medium": clean(files.get("medium")),
        "img_large": clean(files.get("large")),
        "title": clean(raw.get("title")),
        "caption": clean(raw.get("caption")),
        "credit": clean(raw.get("credit")),
        "source_link": clean(raw.get("link")),
        "site": to_int(raw.get("site"), field="site"),
        "drive": to_int(raw.get("drive"), field="drive"),
        "extra": _extra(raw, camera),
    }
    return build_record(fields, label=f"{rover_id}:{imageid}")


def _extra(raw: Mapping[str, Any], camera: Mapping[str, Any]) -> dict[str, Any]:
    camera_rest = {key: value for key, value in camera.items() if key != "instrument"}
    return compact(
        {
            "extended": raw.get("extended"),
            "attitude": raw.get("attitude"),
            "camera": camera_rest,
            "date_taken_mars": clean(raw.get("date_taken_mars")),
        }
    )


class M2020Adapter:
    """Fetches Mars 2020 feed photos by sol range with the ``condition_*:sol`` filters.

    ``rover_id`` and ``category`` select the rover and its feed category; subclasses
    (Ingenuity) override them and ``parse``.
    """

    rover_id: str = PERSEVERANCE
    category: str = "mars2020"

    def __init__(self, client: UpstreamClient, settings: Settings) -> None:
        self._client = client
        self._url = settings.m2020_feed_url
        self._page_size = settings.m2020_page_size
        self._max_pages = settings.upstream_max_pages
        self._label = self.rover_id.capitalize()
        self._base_params: dict[str, str | int] = {
            "feed": "raw_images",
            "category": self.category,
            "feedtype": "json",
        }

    def parse(self, raw: Mapping[str, Any]) -> PhotoRecord | None:
        """Map one feed image to a record (hook for subclasses)."""
        return parse_m2020_image(raw, self.rover_id)

    async def latest_sol(self) -> int:
        data = await self._client.get_json(self._url, {**self._base_params, "latest": "true"})
        sol = to_int(data.get("latest_sol"), field="latest_sol") if isinstance(data, dict) else None
        if sol is None:
            raise UpstreamError(self._url, None, "latest_sol is missing")
        return sol

    async def source_total(self) -> int | None:
        """``total_results`` of an unbounded ``sol >= 0`` query with one image per page."""
        params: dict[str, str | int] = {
            **self._base_params,
            "order": "sol desc",
            "num": 1,
            "page": 0,
            "condition_2": "0:sol:gte",
        }
        data = await self._client.get_json(self._url, params)
        if not isinstance(data, dict):
            raise UpstreamError(self._url, None, "response is not an object")
        return to_int(data.get("total_results"), field="total_results")

    def fetch_sols(self, from_sol: int, to_sol: int) -> SolStream:
        """Stream sols of the range; ``stats.range_total`` is the range's ``total_results``.

        Per-sol ``source_total`` is known only for a single-sol range (the feed has no
        per-sol total).
        """
        stats = FetchStats()
        return SolStream(self._fetch_sols(from_sol, to_sol, stats), stats)

    async def _fetch_sols(
        self, from_sol: int, to_sol: int, stats: FetchStats
    ) -> AsyncGenerator[tuple[int, list[PhotoRecord]]]:
        """Group the range's images by sol.

        Paging a multi-sol range (``order=sol desc``) is unstable inside a sol: at a page
        boundary one image can repeat while another is skipped. A sol of a multi-sol range
        that shows a repeated image is therefore refetched alone and that result is used;
        if the range still delivered fewer unique images than ``total_results``, the sols
        next to the boundaries of pages that fell short are refetched too. A failed refetch
        raises ``SolsFetchFailed`` naming the sols left incomplete (possibly yielded already).
        """
        validate_sol_range(from_sol, to_sol)
        state = _RangeState(refetch=from_sol != to_sol)
        current_sol: int | None = None
        bucket: list[PhotoRecord] = []
        pages = state.pages if state.refetch else None
        async for raw_sol, record in self._iter_images(from_sol, to_sol, stats, pages):
            if raw_sol is None or not from_sol <= raw_sol <= to_sol:
                stats.unattributed_dropped += 1
                continue
            if raw_sol in stats.refetched_sols:
                # The sol's single-sol refetch is authoritative.
                continue
            if raw_sol != current_sol:
                if current_sol is not None and raw_sol > current_sol:
                    logger.warning(
                        "%s feed out of order: sol %d after sol %d",
                        self._label,
                        raw_sol,
                        current_sol,
                    )
                if current_sol is not None:
                    records = await self._finish_sol(current_sol, bucket, stats, state)
                    if records:
                        yield current_sol, records
                current_sol, bucket = raw_sol, []
            sol_stats = stats.sol(raw_sol)
            sol_stats.received += 1
            if record is None:
                sol_stats.dropped += 1
            else:
                bucket.append(record)
        if current_sol is not None:
            records = await self._finish_sol(current_sol, bucket, stats, state)
            if records:
                yield current_sol, records
        missing = _missing_images(stats, state) if state.refetch else 0
        if missing > 0:
            planned = _short_page_sols(state.pages, from_sol, to_sol) - stats.refetched_sols
            logger.warning(
                "%s sols %d..%d: range short by %d images; refetching %d sol(s) "
                "next to short pages: %s",
                self._label,
                from_sol,
                to_sol,
                missing,
                len(planned),
                sorted(planned, reverse=True),
            )
            remaining = sorted(planned, reverse=True)
            while remaining:
                sol = remaining[0]
                reason = f"range short by {missing} images"
                records = await self._refetch_sol(sol, stats, state, reason, remaining)
                remaining.pop(0)
                if records:
                    yield sol, records
        stats.finish(from_sol, to_sol)
        if from_sol == to_sol and stats.unattributed_dropped:
            # The range total also counts images of unknown sols: not this sol's total.
            stats.sol(from_sol).source_total = None

    async def _finish_sol(
        self, sol: int, bucket: list[PhotoRecord], stats: FetchStats, state: "_RangeState"
    ) -> list[PhotoRecord]:
        """Dedupe a finished sol, also against records already yielded for it; a sol of a
        multi-sol range with repeated images is refetched alone instead."""
        unique = dedupe_by_source_id(bucket, label=f"{self.rover_id} sol {sol}")
        duplicates = len(bucket) - len(unique)
        if duplicates and state.refetch:
            state.duplicates[sol] = state.duplicates.get(sol, 0) + duplicates
            reason = f"{duplicates} repeated images in the range fetch"
            return await self._refetch_sol(sol, stats, state, reason, [sol])
        state.duplicates[sol] = state.duplicates.get(sol, 0) + duplicates
        known = state.seen.setdefault(sol, set())
        fresh = [record for record in unique if record.source_id not in known]
        known.update(record.source_id for record in fresh)
        stats.sol(sol).dropped += len(bucket) - len(fresh)
        return fresh

    async def _refetch_sol(
        self,
        sol: int,
        stats: FetchStats,
        state: "_RangeState",
        reason: str,
        incomplete: list[int],
    ) -> list[PhotoRecord]:
        """Fetch one sol alone; its counts replace the sol's entry, its new records are
        returned (records already yielded for the sol are not repeated).

        On failure raises ``SolsFetchFailed`` for ``incomplete`` (this sol and the sols
        still waiting for their refetch).
        """
        sub = FetchStats()
        collected: list[PhotoRecord] = []
        try:
            async for _sol, records in self._fetch_sols(sol, sol, sub):
                collected.extend(records)
        except UpstreamError as exc:
            raise SolsFetchFailed(exc, incomplete) from exc
        previous = stats.sols.get(sol)
        stats.sols[sol] = sub.sol(sol)
        stats.refetched_sols.add(sol)
        stats.range_duplicates += state.duplicates.pop(sol, 0)
        known = state.seen.setdefault(sol, set())
        fresh = [record for record in collected if record.source_id not in known]
        known.update(record.source_id for record in fresh)
        logger.warning(
            "%s sol %d refetched alone (%s): %d images instead of %d",
            self._label,
            sol,
            reason,
            sub.sol(sol).received,
            previous.received if previous is not None else 0,
        )
        return fresh

    async def _iter_images(
        self,
        from_sol: int,
        to_sol: int,
        stats: FetchStats,
        pages: list["_Page"] | None = None,
    ) -> AsyncIterator[tuple[int | None, PhotoRecord | None]]:
        """Yield ``(raw sol, record or None)`` per raw image, page by page; stop when all
        ``total_results`` raw images arrived.

        ``pages`` collects each page's first and last sol and whether it delivered fewer
        new (not yet seen) images than expected. An empty page before the end raises
        ``IncompleteResponse``.
        """
        page = 0
        fetched = 0
        seen_ids: set[str] = set()
        while True:
            images, raw_count, total = await self._fetch_page(from_sol, to_sol, page)
            if page == 0:
                stats.range_total = total
                if from_sol == to_sol:
                    # A single-sol range: the range total is that sol's total.
                    stats.sol(from_sol).source_total = total
            fetched += raw_count
            if pages is not None and images:
                expected = min(self._page_size, total - page * self._page_size)
                new = _new_images(images, seen_ids)
                pages.append(_Page(_raw_sol(images[0]), _raw_sol(images[-1]), new < expected))
            for raw in images:
                yield self._parse_in_range(raw, from_sol, to_sol)
            stats.unattributed_dropped += raw_count - len(images)
            if fetched >= total:
                return
            if raw_count == 0:
                # Treated as a failure: the sol being collected may be incomplete.
                raise IncompleteResponse(
                    self._url,
                    None,
                    f"{self.rover_id} sols {from_sol}..{to_sol}: page {page} is empty with "
                    f"{fetched} of {total} images fetched",
                )
            if raw_count < self._page_size:
                logger.warning(
                    "%s sols %d..%d: page %d returned %d of %d images",
                    self._label,
                    from_sol,
                    to_sol,
                    page,
                    raw_count,
                    self._page_size,
                )
            if page + 1 >= self._max_pages:
                raise PageLimitExceeded(
                    self._url,
                    None,
                    f"{self.rover_id} sols {from_sol}..{to_sol}: page limit reached after "
                    f"{page + 1} pages ({fetched} of {total} images)",
                )
            page += 1

    async def _fetch_page(
        self, from_sol: int, to_sol: int, page: int
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        """Return ``(images, raw image count, total_results)`` of one feed page."""
        params: dict[str, str | int] = {
            **self._base_params,
            "order": "sol desc",
            "num": self._page_size,
            "page": page,
            "condition_2": f"{from_sol}:sol:gte",
            "condition_3": f"{to_sol}:sol:lte",
        }
        data = await self._client.get_json(self._url, params)
        if not isinstance(data, dict) or not isinstance(data.get("images"), list):
            raise UpstreamError(self._url, None, "response has no images list")
        total = to_int(data.get("total_results"), field="total_results") or 0
        raw_images = data["images"]
        images = [item for item in raw_images if isinstance(item, Mapping)]
        return images, len(raw_images), total

    def _parse_in_range(
        self, raw: Mapping[str, Any], from_sol: int, to_sol: int
    ) -> tuple[int | None, PhotoRecord | None]:
        """The image's sol (None if unknown) and its record (None if rejected)."""
        record = self.parse(raw)
        if record is None:
            return _raw_sol(raw), None
        if not from_sol <= record.sol <= to_sol:
            logger.warning(
                "Skipping %s image %s: sol %d outside %d..%d",
                self._label,
                record.source_id,
                record.sol,
                from_sol,
                to_sol,
            )
        return record.sol, record


@dataclass
class _RangeState:
    """Bookkeeping of one ``fetch_sols`` call."""

    # Refetch sols with repeated images alone (multi-sol ranges only).
    refetch: bool
    # source_ids already yielded per sol.
    seen: dict[int, set[str]] = field(default_factory=dict)
    # Repeated images per sol that were dropped without a refetch.
    duplicates: dict[int, int] = field(default_factory=dict)
    # Pages of the range fetch (multi-sol ranges only).
    pages: list["_Page"] = field(default_factory=list)


@dataclass(frozen=True)
class _Page:
    first_sol: int | None
    last_sol: int | None
    # Fewer new (not yet seen in this fetch) images than the page should hold.
    short: bool


def _new_images(images: list[Mapping[str, Any]], seen_ids: set[str]) -> int:
    """Images of a page not seen earlier in the fetch (an image without id counts as new)."""
    new = 0
    for raw in images:
        imageid = clean(raw.get("imageid"))
        key = str(imageid) if imageid is not None else None
        if key is None or key not in seen_ids:
            new += 1
        if key is not None:
            seen_ids.add(key)
    return new


def _short_page_sols(pages: list[_Page], from_sol: int, to_sol: int) -> set[int]:
    """Sols on both sides of every boundary of a page that fell short."""
    sols: set[int | None] = set()
    for index, page in enumerate(pages):
        if not page.short:
            continue
        sols.update((page.first_sol, page.last_sol))
        if index > 0:
            sols.add(pages[index - 1].last_sol)
        if index + 1 < len(pages):
            sols.add(pages[index + 1].first_sol)
    return {sol for sol in sols if sol is not None and from_sol <= sol <= to_sol}


def _missing_images(stats: FetchStats, state: _RangeState) -> int:
    """Images the range declared but did not deliver as unique ones (any sol)."""
    if stats.range_total is None:
        return 0
    received = sum(item.received for item in stats.sols.values())
    unique = received - sum(state.duplicates.values())
    return stats.range_total - stats.unattributed_dropped - unique


def _raw_sol(raw: Mapping[str, Any]) -> int | None:
    """The sol of a rejected image, without logging (parsing already warned)."""
    value = clean(raw.get("sol"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None
