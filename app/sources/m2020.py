"""Perseverance (Mars 2020) adapter for the mars.nasa.gov raw images RSS/JSON feed."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
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
    UpstreamClient,
    UpstreamError,
)
from app.sources.normalize import clean, compact, parse_datetime_utc, to_int

logger = logging.getLogger(__name__)

THUMBNAIL = "thumbnail"
BASE_PARAMS: dict[str, str | int] = {
    "feed": "raw_images",
    "category": "mars2020",
    "feedtype": "json",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lower(value: Any) -> str | None:
    value = clean(value)
    return value.lower() if isinstance(value, str) else None


def _upper(value: Any) -> str | None:
    value = clean(value)
    return value.upper() if isinstance(value, str) else None


def parse_m2020_image(raw: Mapping[str, Any]) -> PhotoRecord | None:
    """Map one feed image to a record; None for thumbnails and incomplete images."""
    imageid = clean(raw.get("imageid"))
    sample_type = _lower(raw.get("sample_type"))
    if sample_type == THUMBNAIL:
        logger.debug("Skipping Perseverance thumbnail %s", imageid)
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
    return build_record(fields, label=f"perseverance:{imageid}")


def _extra(raw: Mapping[str, Any], camera: Mapping[str, Any]) -> dict[str, Any]:
    camera_rest = {key: value for key, value in camera.items() if key != "instrument"}
    return compact(
        {"extended": raw.get("extended"), "attitude": raw.get("attitude"), "camera": camera_rest}
    )


class M2020Adapter:
    """Fetches Perseverance photos by sol range with the ``condition_*:sol`` filters."""

    rover_id = "perseverance"

    def __init__(self, client: UpstreamClient, settings: Settings) -> None:
        self._client = client
        self._url = settings.m2020_feed_url
        self._page_size = settings.m2020_page_size
        self._max_pages = settings.upstream_max_pages

    async def latest_sol(self) -> int:
        data = await self._client.get_json(self._url, {**BASE_PARAMS, "latest": "true"})
        sol = to_int(data.get("latest_sol"), field="latest_sol") if isinstance(data, dict) else None
        if sol is None:
            raise UpstreamError(self._url, None, "latest_sol is missing")
        return sol

    async def source_total(self) -> int | None:
        """``total_results`` of an unbounded ``sol >= 0`` query with one image per page."""
        params: dict[str, str | int] = {
            **BASE_PARAMS,
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
        validate_sol_range(from_sol, to_sol)
        current_sol: int | None = None
        bucket: list[PhotoRecord] = []
        seen: dict[int, set[str]] = {}
        async for raw_sol, record in self._iter_images(from_sol, to_sol, stats):
            if raw_sol is None or not from_sol <= raw_sol <= to_sol:
                stats.unattributed_dropped += 1
                continue
            if raw_sol != current_sol:
                if current_sol is not None and raw_sol > current_sol:
                    logger.warning(
                        "Perseverance feed out of order: sol %d after sol %d",
                        raw_sol,
                        current_sol,
                    )
                if current_sol is not None:
                    records = self._finish_sol(current_sol, bucket, stats, seen)
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
            records = self._finish_sol(current_sol, bucket, stats, seen)
            if records:
                yield current_sol, records
        stats.finish(from_sol, to_sol)
        if from_sol == to_sol and stats.unattributed_dropped:
            # The range total also counts images of unknown sols: not this sol's total.
            stats.sol(from_sol).source_total = None

    @staticmethod
    def _finish_sol(
        sol: int, bucket: list[PhotoRecord], stats: FetchStats, seen: dict[int, set[str]]
    ) -> list[PhotoRecord]:
        """Dedupe a finished sol, also against records already yielded for it."""
        unique = dedupe_by_source_id(bucket, label=f"perseverance sol {sol}")
        known = seen.setdefault(sol, set())
        fresh = [record for record in unique if record.source_id not in known]
        known.update(record.source_id for record in fresh)
        stats.sol(sol).dropped += len(bucket) - len(fresh)
        return fresh

    async def _iter_images(
        self, from_sol: int, to_sol: int, stats: FetchStats
    ) -> AsyncIterator[tuple[int | None, PhotoRecord | None]]:
        """Yield ``(raw sol, record or None)`` per raw image, page by page; stop when all
        ``total_results`` raw images arrived.

        An empty page before that raises ``IncompleteResponse``.
        """
        page = 0
        fetched = 0
        while True:
            images, raw_count, total = await self._fetch_page(from_sol, to_sol, page)
            if page == 0:
                stats.range_total = total
                if from_sol == to_sol:
                    # A single-sol range: the range total is that sol's total.
                    stats.sol(from_sol).source_total = total
            fetched += raw_count
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
                    f"perseverance sols {from_sol}..{to_sol}: page {page} is empty with "
                    f"{fetched} of {total} images fetched",
                )
            if raw_count < self._page_size:
                logger.warning(
                    "Perseverance sols %d..%d: page %d returned %d of %d images",
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
                    f"perseverance sols {from_sol}..{to_sol}: page limit reached after "
                    f"{page + 1} pages ({fetched} of {total} images)",
                )
            page += 1

    async def _fetch_page(
        self, from_sol: int, to_sol: int, page: int
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        """Return ``(images, raw image count, total_results)`` of one feed page."""
        params: dict[str, str | int] = {
            **BASE_PARAMS,
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

    @staticmethod
    def _parse_in_range(
        raw: Mapping[str, Any], from_sol: int, to_sol: int
    ) -> tuple[int | None, PhotoRecord | None]:
        """The image's sol (None if unknown) and its record (None if rejected)."""
        record = parse_m2020_image(raw)
        if record is None:
            return _raw_sol(raw), None
        if not from_sol <= record.sol <= to_sol:
            logger.warning(
                "Skipping Perseverance image %s: sol %d outside %d..%d",
                record.source_id,
                record.sol,
                from_sol,
                to_sol,
            )
        return record.sol, record


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
