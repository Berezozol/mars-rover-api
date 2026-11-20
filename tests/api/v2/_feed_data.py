"""Seed data and parsing helpers for the RSS feed tests (docs/API.md `feed.xml`, stage 16).

Time is fixed per test process: `ANCHOR` (whole seconds, a few minutes in the past when the
module is imported) is the base of every `feed_at`; the 30-day window is relative to the real
clock of the server, so the seed keeps big margins (20 days inside, 31 days outside).

Dataset `seed_world` (perseverance, rows get `feed_at = created_at` set explicitly):
- batch A: sol 2032, T_A = ANCHOR - 2h + 123us: A1 NAVCAM_LEFT full (frame size known),
  A2 NAVCAM_LEFT subframe (no title, caption), A3 NAVCAM_RIGHT full (no credit),
  A4 NAVCAM_RIGHT full (same taken_at as A3, later id);
- batch B: sol 2031, ANCHOR - 8h: B1 NAVCAM_LEFT, B2 MCZ_LEFT;
- batch C: sol 2032 again (redelivery), ANCHOR - 1d: C1, C2 NAVCAM_LEFT;
- batch D: sol 1990 (late photo of an old sol), ANCHOR - 20d: D1 SUPERCAM_RMI, no credit;
- E: sol 2000, ANCHOR - 31d (outside the window); F: sol 2029, feed_at NULL (backfill);
- curiosity: sol 4000, two FHAZ photos, ANCHOR - 3h.
"""

import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

Factory = async_sessionmaker[AsyncSession]

V2 = "/api/v2"
ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
RSS_TYPE = "application/rss+xml; charset=utf-8"
CACHE_CONTROL = "public, max-age=900"
HOST = "feed.example.org"
ORIGIN = f"http://{HOST}"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

ANCHOR = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
T_A = ANCHOR - timedelta(hours=2) + timedelta(microseconds=123)
T_B = ANCHOR - timedelta(hours=8)
T_C = ANCHOR - timedelta(days=1)
T_D = ANCHOR - timedelta(days=20)
T_E = ANCHOR - timedelta(days=31)
T_CUR = ANCHOR - timedelta(hours=3)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def http_date(value: datetime) -> str:
    """RFC 822 / HTTP date in GMT, whole seconds, English names (locale independent)."""
    value = value.astimezone(UTC)
    return (
        f"{DAYS[value.weekday()]}, {value.day:02d} {MONTHS[value.month - 1]} {value.year} "
        f"{value:%H:%M:%S} GMT"
    )


def epoch_us(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1)


def img(source_id: str, suffix: str) -> str:
    return f"https://images.example.test/{source_id}{suffix}"


def record(source_id: str, sol: int, camera: str, taken_at: datetime, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "source_id": source_id,
        "sol": sol,
        "camera_code": camera,
        "taken_at": taken_at,
        "earth_date": taken_at.date(),
        "sample_type": "full",
        "img_small": img(source_id, "_320.jpg"),
        "img_medium": img(source_id, "_800.jpg"),
        "img_large": img(source_id, "_1200.jpg"),
        "img_full": img(source_id, ".png"),
        "title": f"Title of {source_id}",
        "caption": f"Caption of {source_id}",
        "credit": "NASA/JPL-Caltech",
        "source_link": f"https://mars.nasa.gov/raw/{source_id}",
    }
    values.update(overrides)
    return build_record(**values)


def day(h: int, m: int = 0) -> datetime:
    return datetime(2026, 11, 20, h, m, tzinfo=UTC)


async def seed_batch(
    factory: Factory,
    rover: str,
    records: list[Any],
    feed_at: datetime | None,
    *,
    mark_new: bool = True,
) -> None:
    """Insert records (one by one: ids follow list order), then pin feed_at == created_at."""
    async with factory() as session:
        for rec in records:
            await upsert_photos(session, rover, [rec], mark_new=mark_new)
        ids = [rec.source_id for rec in records]
        if feed_at is None:
            await session.execute(
                text(
                    "UPDATE photos SET feed_at = NULL WHERE rover_id = :r AND source_id = ANY(:i)"
                ),
                {"r": rover, "i": ids},
            )
        else:
            await session.execute(
                text(
                    "UPDATE photos SET feed_at = :t, created_at = :t "
                    "WHERE rover_id = :r AND source_id = ANY(:i)"
                ),
                {"t": feed_at, "r": rover, "i": ids},
            )
        await recompute_sol_summaries(session, rover, {rec.sol for rec in records})
        await refresh_rover_stats(session, rover)
        await session.commit()


async def photo_ids(factory: Factory) -> dict[str, int]:
    async with factory() as session:
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        return {source_id: photo_id for source_id, photo_id in rows}


async def camera_names(factory: Factory, rover: str) -> dict[str, str]:
    async with factory() as session:
        rows = await session.execute(
            text("SELECT code, name FROM cameras WHERE rover_id = :r"), {"r": rover}
        )
        return {code: name for code, name in rows}


async def seed_world(factory: Factory) -> dict[str, int]:
    await seed_batch(
        factory,
        "perseverance",
        [
            record(
                "A1",
                2032,
                "NAVCAM_LEFT",
                day(10, 0),
                credit="NASA/JPL-Caltech/LANL",
                extra={"extended": {"dimension": "(1648,1200)"}},
            ),
            record(
                "A2",
                2032,
                "NAVCAM_LEFT",
                day(10, 5),
                sample_type="subframe",
                title=None,
                credit="NASA/JPL-Caltech/MSSS",
            ),
            record("A3", 2032, "NAVCAM_RIGHT", day(10, 10), credit=None),
            record("A4", 2032, "NAVCAM_RIGHT", day(10, 10)),
        ],
        T_A,
    )
    await seed_batch(
        factory,
        "perseverance",
        [
            record("B1", 2031, "NAVCAM_LEFT", day(9, 0) - timedelta(days=1)),
            record("B2", 2031, "MCZ_LEFT", day(9, 30) - timedelta(days=1)),
        ],
        T_B,
    )
    await seed_batch(
        factory,
        "perseverance",
        [
            record("C1", 2032, "NAVCAM_LEFT", day(11, 0)),
            record("C2", 2032, "NAVCAM_LEFT", day(11, 1)),
        ],
        T_C,
    )
    await seed_batch(
        factory,
        "perseverance",
        [record("D1", 1990, "SUPERCAM_RMI", datetime(2026, 9, 1, 8, 0, tzinfo=UTC), credit=None)],
        T_D,
    )
    await seed_batch(factory, "perseverance", [record("E1", 2000, "NAVCAM_LEFT", day(8))], T_E)
    await seed_batch(
        factory, "perseverance", [record("F1", 2029, "NAVCAM_LEFT", day(7))], None, mark_new=False
    )
    await seed_batch(
        factory,
        "curiosity",
        [record("K1", 4000, "FHAZ", day(6, 0)), record("K2", 4000, "FHAZ", day(6, 1))],
        T_CUR,
    )
    return await photo_ids(factory)


# --- requests and parsing ---------------------------------------------------------------------


def feed_url(rover: str = "perseverance") -> str:
    return f"{V2}/rovers/{rover}/feed.xml"


async def get_feed(
    client: httpx.AsyncClient,
    rover: str = "perseverance",
    *,
    headers: dict[str, str] | None = None,
    **params: str | int,
) -> httpx.Response:
    all_headers = {"Host": HOST, **(headers or {})}
    return await client.get(feed_url(rover), params=params or None, headers=all_headers)


def parse(response: httpx.Response) -> ET.Element:
    assert response.status_code == 200, response.text
    return ET.fromstring(response.content)


def channel_of(root: ET.Element) -> ET.Element:
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    return channel


def items_of(response: httpx.Response) -> list[ET.Element]:
    return channel_of(parse(response)).findall("item")


def child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    assert child is not None, f"<{tag}> missing"
    return child.text or ""


def query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


class Html(HTMLParser):
    """Tags (with attributes) and visible text of an item description."""

    def __init__(self, source: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.chunks: list[str] = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.chunks).split())

    def attr_values(self, tag: str, attr: str) -> list[str]:
        return [a[attr] for t, a in self.tags if t == tag and a.get(attr)]  # type: ignore[misc]

    @property
    def hrefs(self) -> list[str]:
        return self.attr_values("a", "href")

    @property
    def srcs(self) -> list[str]:
        return self.attr_values("img", "src")


def description_html(item: ET.Element) -> Html:
    return Html(child_text(item, "description"))


def card_ids(html: Html) -> list[int]:
    """Photo ids of the `/photos/{id}` links, in document order, duplicates removed."""
    found: list[int] = []
    for href in html.hrefs:
        match = re.fullmatch(r".*/photos/(\d+)(?:[?#].*)?", href)
        if match and int(match.group(1)) not in found:
            found.append(int(match.group(1)))
    return found
