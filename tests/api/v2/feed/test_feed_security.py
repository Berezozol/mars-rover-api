"""Feed XML safety (docs/proposals/stage-16-rss.md ADR-2): hostile data stays data.

Photo titles, captions and credits come from the NASA feeds and may contain markup, XML
special characters and characters forbidden in XML 1.0; URLs may use dangerous schemes; the
Host header is client-controlled. Whatever is stored, the feed must be well-formed XML
(`xml.etree` parses it), markup must stay text, only http/https links may be emitted and
CDATA must not be used.
"""

import re

import httpx
import pytest

from tests.api.v2._feed_data import (
    ANCHOR,
    MEDIA,
    Factory,
    channel_of,
    child_text,
    description_html,
    get_feed,
    items_of,
    parse,
    record,
    seed_batch,
)

pytestmark = pytest.mark.api

CONTROL = "\x0b\x0c\x1f￾"
HOSTILE_TITLE = f"</title><script>alert(1)</script> & \"q\" 'a' <b>bold</b>{CONTROL}"
HOSTILE_CAPTION = f"</description><script>x()</script> ]]> <img src=x onerror=alert(2)>{CONTROL}"
HOSTILE_CREDIT = f"NASA <i>& Co</i> ]]>{CONTROL}"
CLEAN_TITLE = HOSTILE_TITLE.replace(CONTROL, "")
CLEAN_CAPTION = HOSTILE_CAPTION.replace(CONTROL, "")
CLEAN_CREDIT = HOSTILE_CREDIT.replace(CONTROL, "")


@pytest.fixture
async def hostile(clean_db: Factory) -> None:
    await seed_batch(
        clean_db,
        "perseverance",
        [
            record(
                "H1",
                10,
                "NAVCAM_LEFT",
                ANCHOR,
                title=HOSTILE_TITLE,
                caption=HOSTILE_CAPTION,
                credit=HOSTILE_CREDIT,
            ),
            record(
                "H2",
                10,
                "NAVCAM_LEFT",
                ANCHOR,
                img_small="javascript:alert(1)",
                img_medium="ftp://files.example.test/x.jpg",
                img_large="//evil.example.test/x.jpg",
                img_full="data:text/html,<script>alert(3)</script>",
                source_link="javascript:alert(4)",
            ),
        ],
        ANCHOR,
    )


def assert_no_active_content(body: bytes) -> None:
    lowered = body.lower()
    assert b"<script" not in lowered
    assert b"<![cdata[" not in lowered
    assert b"javascript:" not in lowered
    assert b"data:text" not in lowered
    assert b"ftp://" not in lowered
    for char in CONTROL:
        assert char.encode() not in body
    assert b"\x0b" not in body and b"\x0c" not in body and b"\x1f" not in body


@pytest.mark.parametrize("group", ["sol", "photo"])
async def test_feed_with_hostile_data_is_well_formed_xml_without_active_content(
    client: httpx.AsyncClient, hostile: None, group: str
) -> None:
    response = await get_feed(client, group=group)

    root = parse(response)  # raises ParseError on malformed XML
    assert_no_active_content(response.content)
    names = {element.tag for element in root.iter()}
    assert "script" not in names and "b" not in names and "i" not in names
    assert len(channel_of(root).findall("item")) == (1 if group == "sol" else 2)


async def test_feed_group_photo_title_is_plain_text_with_control_characters_removed(
    client: httpx.AsyncClient, hostile: None
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    titles = [child_text(i, "title") for i in items]
    assert CLEAN_TITLE in titles


async def test_feed_group_photo_description_shows_markup_as_text(
    client: httpx.AsyncClient, hostile: None
) -> None:
    items = items_of(await get_feed(client, group="photo"))
    item = next(i for i in items if child_text(i, "title") == CLEAN_TITLE)

    raw = child_text(item, "description")
    html = description_html(item)
    assert "<script" not in raw.lower()
    assert "&lt;script" in raw
    assert not [t for t, _ in html.tags if t in {"script", "b", "i"}]
    assert not [a for _, attrs in html.tags for a in attrs if a.startswith("on")]
    assert CLEAN_CAPTION in html.text
    assert f"Credit: {CLEAN_CREDIT}" in html.text
    assert child_text(item, f"{MEDIA}credit") == CLEAN_CREDIT


async def test_feed_group_sol_description_shows_credit_as_text(
    client: httpx.AsyncClient, hostile: None
) -> None:
    [item] = items_of(await get_feed(client))

    html = description_html(item)
    assert not [t for t, _ in html.tags if t in {"script", "b", "i"}]
    assert CLEAN_CREDIT in html.text


@pytest.mark.parametrize("group", ["sol", "photo"])
async def test_feed_keeps_the_item_but_drops_links_with_non_http_schemes(
    client: httpx.AsyncClient, hostile: None, group: str
) -> None:
    response = await get_feed(client, group=group)

    root = parse(response)
    urls = [e.attrib["url"] for e in root.iter() if "url" in e.attrib]
    hrefs = [e.attrib["href"] for e in root.iter() if "href" in e.attrib]
    for item in channel_of(root).findall("item"):
        html = description_html(item)
        urls += html.srcs
        hrefs += html.hrefs
    assert all(u.startswith(("http://", "https://")) for u in urls + hrefs), (urls, hrefs)
    # A protocol-relative URL is not an absolute http(s) link either.
    assert b"//evil.example.test" not in response.content


async def test_feed_group_photo_item_without_usable_urls_still_has_title_link_and_guid(
    client: httpx.AsyncClient, hostile: None
) -> None:
    items = items_of(await get_feed(client, group="photo"))
    plain = next(i for i in items if child_text(i, "title") != CLEAN_TITLE)

    assert child_text(plain, "title")
    assert child_text(plain, "link").startswith("http://")
    assert child_text(plain, "guid") == child_text(plain, "link")
    assert plain.find(f"{MEDIA}content") is None or plain.find(f"{MEDIA}content").attrib[  # type: ignore[union-attr]
        "url"
    ].startswith(("http://", "https://"))


@pytest.mark.parametrize(
    "host",
    [
        pytest.param('evil.example.test"><x', id="quote-and-tag"),
        pytest.param("evil.example.test&amp;<y>", id="entity-and-tag"),
        pytest.param("evil.example.test'onload='z()", id="single-quote"),
    ],
)
@pytest.mark.parametrize("group", ["sol", "photo"])
async def test_feed_hostile_host_header_cannot_inject_markup(
    client: httpx.AsyncClient, hostile: None, host: str, group: str
) -> None:
    response = await get_feed(client, group=group, headers={"Host": host})

    assert response.status_code < 500
    if response.status_code != 200:
        return  # the request was refused: nothing was rendered
    root = parse(response)
    assert {e.tag for e in root.iter()} & {"x", "y", "z"} == set()
    for item in channel_of(root).findall("item"):
        html = description_html(item)
        assert {t for t, _ in html.tags} & {"x", "y", "z"} == set()
        assert not [a for _, attrs in html.tags for a in attrs if a.startswith("on")]
    assert re.search(rb"<[xyz][ >/]", response.content) is None
