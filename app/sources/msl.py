"""Curiosity (MSL) adapter for the mars.nasa.gov raw_image_items API."""

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
SITE_ORIGIN = "https://mars.nasa.gov"
MISSION_CONDITION = "msl:mission"
NO_THUMBNAILS = "thumbnail::sample_type::noteq"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_link(value: Any) -> str | None:
    link = clean(value)
    if isinstance(link, str) and link.startswith("/"):
        return SITE_ORIGIN + link
    return link


def _text_case(value: Any, *, upper: bool) -> str | None:
    value = clean(value)
    if not isinstance(value, str):
        return None
    return value.upper() if upper else value.lower()


def parse_msl_item(raw: Mapping[str, Any]) -> PhotoRecord | None:
    """Map one feed item to a record; None for thumbnails and incomplete items."""
    item_id = clean(raw.get("id"))
    extended = _mapping(raw.get("extended"))
    sample_type = _text_case(extended.get("sample_type"), upper=False)
    if raw.get("is_thumbnail") is True or sample_type == THUMBNAIL:
        logger.debug("Skipping Curiosity thumbnail %s", item_id)
        return None
    fields: dict[str, Any] = {
        "source_id": str(item_id) if item_id is not None else None,
        "sol": to_int(raw.get("sol"), field="sol"),
        "camera_code": _text_case(raw.get("instrument"), upper=True),
        "taken_at": parse_datetime_utc(raw.get("date_taken"), field="date_taken"),
        "sample_type": sample_type,
        "img_full": clean(raw.get("https_url")) or clean(raw.get("url")),
        "received_at": parse_datetime_utc(raw.get("date_received"), field="date_received"),
        "title": clean(raw.get("title")),
        "caption": clean(raw.get("description")),
        "credit": clean(raw.get("image_credit")),
        "source_link": _source_link(raw.get("link")),
        "site": to_int(raw.get("site"), field="site"),
        "drive": to_int(raw.get("drive"), field="drive"),
        "extra": _extra(raw, extended),
    }
    return build_record(fields, label=f"curiosity:{item_id} ({clean(raw.get('imageid'))})")


def _extra(raw: Mapping[str, Any], extended: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "imageid": raw.get("imageid"),
        "extended": {key: value for key, value in extended.items() if key != "sample_type"},
        "attitude": raw.get("attitude"),
        "xyz": raw.get("xyz"),
    }
    extra.update({key: value for key, value in raw.items() if key.startswith("camera_")})
    return compact(extra)


class MslAdapter:
    """Fetches Curiosity photos sol by sol, following every page of a sol."""

    rover_id = "curiosity"
    ORDER = "sol desc,instrument_sort asc,sample_type_sort asc, date_taken desc"

    def __init__(self, client: UpstreamClient, settings: Settings) -> None:
        self._client = client
        self._url = settings.msl_feed_url
        self._page_size = settings.msl_page_size
        self._max_pages = settings.upstream_max_pages

    async def latest_sol(self) -> int:
        params: dict[str, str | int] = {
            "order": self.ORDER,
            "per_page": 1,
            "page": 0,
            "condition_1": MISSION_CONDITION,
        }
        data = await self._client.get_json(self._url, params)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], Mapping):
            raise UpstreamError(self._url, None, "no items in latest sol response")
        sol = to_int(items[0].get("sol"), field="sol")
        if sol is None:
            raise UpstreamError(self._url, None, "latest item has no sol")
        return sol

    async def fetch_sols(
        self, from_sol: int, to_sol: int
    ) -> AsyncIterator[tuple[int, list[PhotoRecord]]]:
        validate_sol_range(from_sol, to_sol)
        for sol in range(to_sol, from_sol - 1, -1):
            records = await self._fetch_sol(sol)
            if records:
                yield sol, records

    async def _fetch_sol(self, sol: int) -> list[PhotoRecord]:
        records: list[PhotoRecord] = []
        page = 0
        while True:
            items, more = await self._fetch_page(sol, page)
            records.extend(self._parse_items(items, sol))
            if not more:
                return dedupe_by_source_id(records, label=f"curiosity sol {sol}")
            if page + 1 >= self._max_pages:
                raise PageLimitExceeded(
                    self._url,
                    None,
                    f"curiosity sol {sol}: page limit reached after {page + 1} pages, "
                    "more pages remain",
                )
            page += 1

    async def _fetch_page(self, sol: int, page: int) -> tuple[list[Mapping[str, Any]], bool]:
        params: dict[str, str | int] = {
            "order": self.ORDER,
            "per_page": self._page_size,
            "page": page,
            "condition_1": MISSION_CONDITION,
            "condition_2": f"{sol}:sol:in",
            "extended": NO_THUMBNAILS,
        }
        data = await self._client.get_json(self._url, params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamError(self._url, None, "response has no items list")
        items = [item for item in data["items"] if isinstance(item, Mapping)]
        return items, data.get("more") is True

    @staticmethod
    def _parse_items(items: list[Mapping[str, Any]], sol: int) -> list[PhotoRecord]:
        records: list[PhotoRecord] = []
        for raw in items:
            record = parse_msl_item(raw)
            if record is None:
                continue
            if record.sol != sol:
                logger.warning(
                    "Skipping Curiosity image %s: sol %d, requested %d",
                    record.source_id,
                    record.sol,
                    sol,
                )
                continue
            records.append(record)
        return records
