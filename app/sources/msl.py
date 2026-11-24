"""Curiosity (MSL) adapter for the mars.nasa.gov raw_image_items API."""

import logging
import re
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.sources.base import (
    FetchStats,
    PhotoRecord,
    SolStats,
    SolStream,
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


ONLY_THUMBNAILS = "thumbnail::sample_type::eq"
THUMBNAIL_PAGE_SIZE = 1000
DEFAULT_MAX_PAGES = 200

# MSSS ids (Mastcam, MAHLI, MARDI): 4 digits of the sol, camera code (M + letter), 16 digits of
# the shot, a product letter with a 2-character version, then ``_D`` and 3 more characters.
# The sol is 4 digits and the key is exactly 22 characters; sol 10000 is decades away and would
# need a new key rule, so such ids are not matched (None) rather than guessed.
_MSSS_ID = re.compile(r"^(\d{4}M[A-Z]\d{16})([A-Z])[0-9A-Z]{2}_D[A-Z0-9]{3}$")
_FRAME_PRODUCTS = frozenset("CDERS")
_THUMBNAIL_PRODUCTS = frozenset("ITU")


@dataclass(frozen=True)
class Thumbnail:
    """One thumbnail item of the feed: its sol, matching key and URL."""

    key: str
    url: str
    sol: int
    imageid: str


def thumbnail_key(imageid: str) -> str | None:
    """Key shared by an MSSS frame and its thumbnail: the first 22 characters of the id.

    Frames (product letters C, D, E, R, S) and thumbnails (I, T, U) of one shot give the same
    key; ``None`` for any other id (Navcam, Hazcam, ChemCam and unknown formats).
    """
    match = _MSSS_ID.match(imageid) if isinstance(imageid, str) else None
    if match is None or match.group(2) not in _FRAME_PRODUCTS | _THUMBNAIL_PRODUCTS:
        return None
    return match.group(1)


def parse_thumbnail(raw: Mapping[str, Any]) -> Thumbnail | None:
    """A thumbnail of an MSSS camera; ``None`` for other items and for incomplete ones."""
    imageid = clean(raw.get("imageid"))
    if not isinstance(imageid, str):
        return None
    match = _MSSS_ID.match(imageid)
    if match is None or match.group(2) not in _THUMBNAIL_PRODUCTS:
        return None
    url = clean(raw.get("https_url")) or clean(raw.get("url"))
    sol = to_int(raw.get("sol"), field="sol")
    if not isinstance(url, str) or sol is None:
        return None
    return Thumbnail(key=match.group(1), url=url, sol=sol, imageid=imageid)


async def fetch_thumbnails(
    client: UpstreamClient,
    sols: Sequence[int],
    *,
    url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[Thumbnail]:
    """MSSS thumbnails of ``sols`` (one feed query for all of them, every page of it).

    One thumbnail per key (the first one seen). Raises ``UpstreamError`` on a failed request
    or when pages remain after ``max_pages``.
    """
    if not sols:
        return []
    wanted = ",".join(str(sol) for sol in sorted(set(sols)))
    found: dict[str, Thumbnail] = {}
    for page in range(max_pages):
        params: dict[str, str | int] = {
            "order": MslAdapter.ORDER,
            "per_page": THUMBNAIL_PAGE_SIZE,
            "page": page,
            "condition_1": MISSION_CONDITION,
            "condition_2": f"{wanted}:sol:in",
            "extended": ONLY_THUMBNAILS,
        }
        data = await client.get_json(url, params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamError(url, None, "response has no items list")
        for raw in data["items"]:
            thumbnail = parse_thumbnail(raw) if isinstance(raw, Mapping) else None
            if thumbnail is not None:
                found.setdefault(thumbnail.key, thumbnail)
        if data.get("more") is not True:
            return list(found.values())
    raise PageLimitExceeded(url, None, f"thumbnails: page limit reached after {max_pages} pages")


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

    async def fetch_thumbnails(self, sols: Sequence[int]) -> list[Thumbnail]:
        """MSSS thumbnails of ``sols``, one feed query (plus pages) for all of them."""
        return await fetch_thumbnails(self._client, sols, url=self._url, max_pages=self._max_pages)

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

    async def source_total(self) -> int | None:
        """``total`` of the mission query without thumbnails, one item per page."""
        params: dict[str, str | int] = {
            "order": self.ORDER,
            "per_page": 1,
            "page": 0,
            "condition_1": MISSION_CONDITION,
            "extended": NO_THUMBNAILS,
        }
        data = await self._client.get_json(self._url, params)
        if not isinstance(data, dict):
            raise UpstreamError(self._url, None, "response is not an object")
        return to_int(data.get("total"), field="total")

    def fetch_sols(self, from_sol: int, to_sol: int) -> SolStream:
        """Stream sols of the range; per-sol ``source_total`` is the feed's ``total``
        (thumbnails excluded) and ``stats.range_total`` their sum once the range is done."""
        stats = FetchStats()
        return SolStream(self._fetch_sols(from_sol, to_sol, stats), stats)

    async def _fetch_sols(
        self, from_sol: int, to_sol: int, stats: FetchStats
    ) -> AsyncGenerator[tuple[int, list[PhotoRecord]]]:
        validate_sol_range(from_sol, to_sol)
        for sol in range(to_sol, from_sol - 1, -1):
            records, sol_stats = await self._fetch_sol(sol)
            stats.sols[sol] = sol_stats
            if records:
                yield sol, records
        totals = [stats.sols[sol].source_total for sol in range(from_sol, to_sol + 1)]
        stats.range_total = sum(t for t in totals if t is not None) if None not in totals else None
        stats.complete = True

    async def _fetch_sol(self, sol: int) -> tuple[list[PhotoRecord], SolStats]:
        records: list[PhotoRecord] = []
        sol_stats = SolStats()
        page = 0
        while True:
            items, raw_count, more, total = await self._fetch_page(sol, page)
            if page == 0:
                sol_stats.source_total = total
            sol_stats.received += raw_count
            records.extend(self._parse_items(items, sol))
            if not more:
                unique = dedupe_by_source_id(records, label=f"curiosity sol {sol}")
                sol_stats.dropped = sol_stats.received - len(unique)
                return unique, sol_stats
            if page + 1 >= self._max_pages:
                raise PageLimitExceeded(
                    self._url,
                    None,
                    f"curiosity sol {sol}: page limit reached after {page + 1} pages, "
                    "more pages remain",
                )
            page += 1

    async def _fetch_page(
        self, sol: int, page: int
    ) -> tuple[list[Mapping[str, Any]], int, bool, int | None]:
        """Return ``(items, raw item count, more, total)`` of one page."""
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
        raw_items = data["items"]
        items = [item for item in raw_items if isinstance(item, Mapping)]
        total = to_int(data.get("total"), field="total")
        return items, len(raw_items), data.get("more") is True, total

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
