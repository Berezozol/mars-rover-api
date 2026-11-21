"""RSS feed of new photos: ``GET /api/v2/rovers/{rover}/feed.xml`` (docs/API.md, stage 16).

The body is built with ``xml.etree`` (it escapes text and attributes). Every text from the
database goes through ``xml_safe`` first: XML 1.0 forbids some control characters, which etree
would write as they are. The HTML of ``description`` is assembled with ``html.escape`` and put
in the element as text (no CDATA). Links taken from the database must be http(s) URLs. The body
holds nothing that depends on the time of the request, so its ETag is stable.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.home import request_origin
from app.api.v2.params import CameraParam, SampleTypeParam, StrictInt
from app.api.v2.schemas import ErrorResponse
from app.db.session import get_session
from app.services import v2
from app.services.feeds import (
    DEFAULT_CREDIT,
    FEED_DEFAULT_LIMIT,
    FEED_MAX_LIMIT,
    Feed,
    FeedBatch,
    FeedQuery,
    build_feed,
)

FEED_MEDIA_TYPE = "application/rss+xml; charset=utf-8"

ATOM_NS = "http://www.w3.org/2005/Atom"
MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("media", MEDIA_NS)

CHANNEL_DESCRIPTION = "Unofficial feed. Images: NASA/JPL-Caltech and partners."
SITE_NAME = "Sol Index"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Characters outside the XML 1.0 Char production (lone surrogates included).
_INVALID_XML_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

router = APIRouter()


def xml_safe(text: str) -> str:
    """Drop the characters XML 1.0 cannot carry; everything else stays as it is."""
    return _INVALID_XML_RE.sub("", text)


def http_date(moment: datetime) -> str:
    """RFC 822 date in GMT (also a valid HTTP-date), second precision."""
    return format_datetime(moment.astimezone(UTC), usegmt=True)


def _http_url(value: str | None) -> str | None:
    """The URL when it is an absolute http(s) one, else None."""
    if not value:
        return None
    value = xml_safe(value).strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme in ("http", "https") and parts.netloc else None


def _esc(value: object) -> str:
    return html.escape(xml_safe(str(value)), quote=True)


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, {k: xml_safe(v) for k, v in attrs.items()})
    if text is not None:
        element.text = xml_safe(text)
    return element


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _filters_query(feed: Feed, **extra: object) -> str:
    """Query string of interface links: extra values first, then the feed filters."""
    params = {k: str(v) for k, v in extra.items()}
    if feed.camera:
        params["camera"] = feed.camera
    if feed.sample_type:
        params["sample_type"] = feed.sample_type
    return f"?{urlencode(params)}" if params else ""


def _rover_name(feed: Feed) -> str:
    return feed.rover_name or feed.rover.capitalize()


def _channel_title(feed: Feed) -> str:
    filters = ", ".join(part for part in (feed.camera, feed.sample_type) if part)
    suffix = f" ({filters})" if filters else ""
    return f"{_rover_name(feed)} — new raw images{suffix} — {SITE_NAME}"


def _micros(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(microseconds=1)


def _batch_guid(feed: Feed, batch: FeedBatch) -> str:
    # No host in it: the guid must not change with the domain the feed is read from.
    guid = f"sol-index:{feed.rover}:sol-{batch.sol}:{_micros(batch.feed_at)}"
    if feed.camera:
        guid += f":camera-{feed.camera}"
    if feed.sample_type:
        guid += f":type-{feed.sample_type}"
    return guid


def _thumb_url(photo: Any) -> str | None:
    for candidate in (photo.img_small, photo.img_medium, photo.img_full):
        if (url := _http_url(candidate)) is not None:
            return url
    return None


def _credit(photo: Any) -> str:
    return xml_safe(photo.credit or "").strip() or DEFAULT_CREDIT


def _batch_item(channel: ET.Element, feed: Feed, batch: FeedBatch, origin: str) -> None:
    rover = _rover_name(feed)
    item = _sub(channel, "item")
    title = (
        f"{rover} Sol {batch.sol}: {_plural(batch.total, 'new image')}, "
        f"{_plural(len(batch.cameras), 'camera')}"
    )
    sol_link = f"{origin}/rovers/{feed.rover}{_filters_query(feed, sol=batch.sol)}"
    _sub(item, "title", title)
    _sub(item, "link", sol_link)
    _sub(item, "guid", _batch_guid(feed, batch), isPermaLink="false")
    _sub(item, "pubDate", http_date(batch.feed_at))
    _sub(item, "description", _batch_description(batch, origin, sol_link))
    thumbs = [url for p in batch.thumbnails if (url := _thumb_url(p)) is not None]
    if thumbs:
        _sub(item, f"{{{MEDIA_NS}}}thumbnail", url=thumbs[0])


def _batch_description(batch: FeedBatch, origin: str, sol_link: str) -> str:
    cameras = ", ".join(f"{_esc(code)} ({count})" for code, count in batch.cameras)
    parts = [f"<p>Cameras: {cameras}</p>"]
    images = []
    for photo in batch.thumbnails:
        if (url := _thumb_url(photo)) is not None:
            card = f"{origin}/photos/{photo.id}"
            images.append(f'<a href="{_esc(card)}"><img src="{_esc(url)}" alt=""/></a>')
    if images:
        parts.append(f"<p>{' '.join(images)}</p>")
    parts.append(f'<p><a href="{_esc(sol_link)}">Open sol {batch.sol}</a></p>')
    parts.append(f"<p>Credit: {_esc('; '.join(batch.credits))}</p>")
    return "".join(parts)


def _photo_item(channel: ET.Element, feed: Feed, photo: Any, origin: str) -> None:
    card = f"{origin}/photos/{photo.id}"
    title = photo.title or f"{_rover_name(feed)} Sol {photo.sol}: {photo.camera.name}"
    credit = _credit(photo)
    item = _sub(channel, "item")
    _sub(item, "title", title)
    _sub(item, "link", card)
    _sub(item, "guid", card, isPermaLink="true")
    if photo.feed_at is not None:
        _sub(item, "pubDate", http_date(photo.feed_at))
    _sub(item, "description", _photo_description(photo, card, credit))
    full = _http_url(photo.img_full)
    if full is not None:
        attrs = {"url": full, "medium": "image"}
        meta = v2.photo_meta(photo)
        if meta.width and meta.height:
            attrs.update(width=str(meta.width), height=str(meta.height))
        _sub(item, f"{{{MEDIA_NS}}}content", **attrs)
    if (thumb := _thumb_url(photo)) is not None:
        _sub(item, f"{{{MEDIA_NS}}}thumbnail", url=thumb)
    _sub(item, f"{{{MEDIA_NS}}}credit", credit)


def _photo_description(photo: Any, card: str, credit: str) -> str:
    parts = []
    if (thumb := _thumb_url(photo)) is not None:
        parts.append(f'<p><a href="{_esc(card)}"><img src="{_esc(thumb)}" alt=""/></a></p>')
    if photo.caption:
        parts.append(f"<p>{_esc(photo.caption)}</p>")
    taken = photo.taken_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"<p>Sol {photo.sol} · {_esc(photo.camera.name)} · taken {taken} UTC</p>")
    links = []
    if (full := _http_url(photo.img_full)) is not None:
        links.append(f'<a href="{_esc(full)}">Full image (NASA)</a>')
    if (raw := _http_url(photo.source_link)) is not None:
        links.append(f'<a href="{_esc(raw)}">Raw image page</a>')
    if links:
        parts.append(f"<p>{' · '.join(links)}</p>")
    parts.append(f"<p>Credit: {_esc(credit)}</p>")
    return "".join(parts)


def render_rss(feed: Feed, origin: str, self_url: str) -> bytes:
    """RSS 2.0 document (UTF-8 bytes with an XML declaration) for the feed."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = _sub(rss, "channel")
    _sub(channel, "title", _channel_title(feed))
    _sub(channel, "link", f"{origin}/rovers/{feed.rover}{_filters_query(feed)}")
    _sub(
        channel,
        f"{{{ATOM_NS}}}link",
        rel="self",
        type="application/rss+xml",
        href=self_url,
    )
    _sub(channel, "description", CHANNEL_DESCRIPTION)
    _sub(channel, "language", "en")
    _sub(channel, "ttl", "60")
    if feed.last_modified is not None:
        _sub(channel, "lastBuildDate", http_date(feed.last_modified))
    for entry in feed.items:
        if isinstance(entry, FeedBatch):
            _batch_item(channel, feed, entry, origin)
        else:
            _photo_item(channel, feed, entry, origin)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def feed_self_url(origin: str, feed: Feed, limit: int | None) -> str:
    """Address of the feed with normalized parameters; defaults are left out."""
    params: dict[str, str] = {}
    if feed.camera:
        params["camera"] = feed.camera
    if feed.sample_type:
        params["sample_type"] = feed.sample_type
    if feed.group != "sol":
        params["group"] = feed.group
    if limit is not None and limit != FEED_DEFAULT_LIMIT[feed.group]:
        params["limit"] = str(limit)
    query = f"?{urlencode(params)}" if params else ""
    return f"{origin}/api/v2/rovers/{feed.rover}/feed.xml{query}"


_FEED_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "RSS 2.0 feed", "content": {"application/rss+xml": {}}},
    404: {"model": ErrorResponse, "description": "rover_not_found / not_found (archive rover)"},
    422: {"model": ErrorResponse, "description": "validation_error"},
    503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
}


@router.get(
    "/rovers/{rover}/feed.xml",
    response_class=Response,
    responses=_FEED_RESPONSES,
    summary="RSS feed of new photos",
)
async def get_feed(
    request: Request,
    rover: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    camera: CameraParam = None,
    sample_type: SampleTypeParam = None,
    group: Annotated[Literal["sol", "photo"], Query(description="sol (default) or photo.")] = "sol",
    limit: Annotated[
        int | None,
        Query(ge=1, le=FEED_MAX_LIMIT, description="Entries: default 20 (sol) or 50 (photo)."),
        StrictInt,
    ] = None,
) -> Response:
    query = FeedQuery(
        rover,
        camera,
        sample_type,
        group,
        limit,
        request.app.state.settings.feed_window_days,
    )
    feed = await build_feed(session, query)
    origin = request_origin(request)
    body = render_rss(feed, origin, feed_self_url(origin, feed, limit))
    headers = {}
    if feed.last_modified is not None:
        headers["Last-Modified"] = http_date(feed.last_modified)
    return Response(body, media_type=FEED_MEDIA_TYPE, headers=headers)
