"""Perseverance (Mars 2020) adapter for the mars.nasa.gov raw images RSS/JSON feed."""

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.core.config import Settings
from app.sources.base import (
    PhotoRecord,
    build_record,
    dedupe_by_source_id,
    validate_sol_range,
)
from app.sources.http import PageLimitExceeded, UpstreamClient, UpstreamError
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

    async def fetch_sols(
        self, from_sol: int, to_sol: int
    ) -> AsyncIterator[tuple[int, list[PhotoRecord]]]:
        validate_sol_range(from_sol, to_sol)
        current_sol: int | None = None
        bucket: list[PhotoRecord] = []
        async for record in self._iter_records(from_sol, to_sol):
            if record.sol != current_sol:
                if current_sol is not None and record.sol > current_sol:
                    logger.warning(
                        "Perseverance feed out of order: sol %d after sol %d",
                        record.sol,
                        current_sol,
                    )
                if current_sol is not None and bucket:
                    yield current_sol, self._dedupe(current_sol, bucket)
                current_sol, bucket = record.sol, []
            bucket.append(record)
        if current_sol is not None and bucket:
            yield current_sol, self._dedupe(current_sol, bucket)

    @staticmethod
    def _dedupe(sol: int, records: list[PhotoRecord]) -> list[PhotoRecord]:
        return dedupe_by_source_id(records, label=f"perseverance sol {sol}")

    async def _iter_records(self, from_sol: int, to_sol: int) -> AsyncIterator[PhotoRecord]:
        """Yield records page by page; stop when all ``total_results`` raw images arrived."""
        page = 0
        fetched = 0
        while True:
            images, raw_count, total = await self._fetch_page(from_sol, to_sol, page)
            fetched += raw_count
            for raw in images:
                record = self._parse_in_range(raw, from_sol, to_sol)
                if record is not None:
                    yield record
            if fetched >= total:
                return
            if raw_count == 0:
                # The feed total can shift while paging; keep what was collected.
                logger.warning(
                    "Perseverance sols %d..%d: page %d is empty with %d of %d images fetched",
                    from_sol,
                    to_sol,
                    page,
                    fetched,
                    total,
                )
                return
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
    def _parse_in_range(raw: Mapping[str, Any], from_sol: int, to_sol: int) -> PhotoRecord | None:
        record = parse_m2020_image(raw)
        if record is not None and not from_sol <= record.sol <= to_sol:
            logger.warning(
                "Skipping Perseverance image %s: sol %d outside %d..%d",
                record.source_id,
                record.sol,
                from_sol,
                to_sol,
            )
            return None
        return record
