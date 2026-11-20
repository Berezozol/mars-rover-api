"""GET /api/v2/rovers/{rover}/feed.xml: content of the RSS feed (docs/API.md, stage 16).

Covers the RSS envelope, `group=sol` batches (titles, guids, descriptions), `group=photo`
entries, filters, ordering, the 30-day window, credits, absolute URLs and `Last-Modified`.
Dataset: `_feed_data.seed_world` (committed with `clean_db`; one per test).
"""

import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
import pytest

from tests.api.v2._feed_data import (
    ATOM,
    CACHE_CONTROL,
    HOST,
    MEDIA,
    ORIGIN,
    RSS_TYPE,
    T_A,
    T_B,
    T_C,
    T_CUR,
    T_D,
    Factory,
    card_ids,
    channel_of,
    child_text,
    description_html,
    epoch_us,
    get_feed,
    http_date,
    items_of,
    parse,
    photo_ids,
    query_of,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("world")]

PHOTO_ORDER = ["A4", "A3", "A2", "A1", "B2", "B1", "C2", "C1", "D1"]


def guid_prefix(sol: int, feed_at, rover: str = "perseverance") -> str:
    return f"sol-index:{rover}:sol-{sol}:{epoch_us(feed_at)}"


# --- envelope ---------------------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "PERSEVERANCE"])
async def test_feed_returns_rss_with_the_contract_headers(
    client: httpx.AsyncClient, rover: str
) -> None:
    response = await get_feed(client, rover)

    assert response.status_code == 200
    assert response.headers["content-type"] == RSS_TYPE
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert response.headers["etag"]
    assert response.content.startswith(b"<?xml")


async def test_feed_root_is_rss_2_with_atom_and_media_namespaces(
    client: httpx.AsyncClient,
) -> None:
    response = await get_feed(client)

    root = parse(response)
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"
    assert b'xmlns:atom="http://www.w3.org/2005/Atom"' in response.content
    assert b'xmlns:media="http://search.yahoo.com/mrss/"' in response.content
    assert [child.tag for child in root] == ["channel"]


async def test_feed_channel_elements_without_filters(client: httpx.AsyncClient) -> None:
    channel = channel_of(parse(await get_feed(client)))

    assert child_text(channel, "title") == "Perseverance — new raw images — Sol Index"
    assert child_text(channel, "link") == f"{ORIGIN}/rovers/perseverance"
    self_link = channel.find(f"{ATOM}link")
    assert self_link is not None
    assert self_link.attrib["rel"] == "self"
    assert self_link.attrib["type"] == "application/rss+xml"
    assert self_link.attrib["href"] == f"{ORIGIN}/api/v2/rovers/perseverance/feed.xml"
    assert child_text(channel, "description") == (
        "Unofficial feed. Images: NASA/JPL-Caltech and partners."
    )
    assert child_text(channel, "language") == "en"
    assert child_text(channel, "ttl") == "60"
    assert child_text(channel, "lastBuildDate") == http_date(T_A)


async def test_feed_channel_of_curiosity_names_the_rover(client: httpx.AsyncClient) -> None:
    channel = channel_of(parse(await get_feed(client, "curiosity")))

    assert child_text(channel, "title") == "Curiosity — new raw images — Sol Index"
    assert child_text(channel, "link") == f"{ORIGIN}/rovers/curiosity"
    assert child_text(channel, "lastBuildDate") == http_date(T_CUR)


async def test_feed_channel_title_link_and_self_use_the_normalized_camera(
    client: httpx.AsyncClient,
) -> None:
    channel = channel_of(parse(await get_feed(client, camera="navcam_left", group="sol")))

    assert child_text(channel, "title") == "Perseverance — new raw images (NAVCAM_LEFT) — Sol Index"
    assert child_text(channel, "link") == f"{ORIGIN}/rovers/perseverance?camera=NAVCAM_LEFT"
    self_href = channel.find(f"{ATOM}link").attrib["href"]  # type: ignore[union-attr]
    assert self_href == f"{ORIGIN}/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT"


async def test_feed_channel_links_carry_sample_type_and_group_in_the_self_link(
    client: httpx.AsyncClient,
) -> None:
    channel = channel_of(parse(await get_feed(client, sample_type="full", group="photo")))

    assert query_of(child_text(channel, "link")) == {"sample_type": ["full"]}
    self_href = channel.find(f"{ATOM}link").attrib["href"]  # type: ignore[union-attr]
    assert urlsplit(self_href).path == "/api/v2/rovers/perseverance/feed.xml"
    assert query_of(self_href) == {"sample_type": ["full"], "group": ["photo"]}


async def test_feed_self_link_omits_default_parameters(client: httpx.AsyncClient) -> None:
    response = await get_feed(client, group="sol", extra="ignored")

    self_href = channel_of(parse(response)).find(f"{ATOM}link").attrib["href"]  # type: ignore[union-attr]
    assert self_href == f"{ORIGIN}/api/v2/rovers/perseverance/feed.xml"


# --- group=sol ----------------------------------------------------------------------------------


async def test_feed_group_sol_is_the_default_and_makes_one_item_per_batch(
    client: httpx.AsyncClient,
) -> None:
    default = await get_feed(client)
    explicit = await get_feed(client, group="sol")

    items = items_of(default)
    assert [child_text(i, "title") for i in items[:3]] == [
        "Perseverance Sol 2032: 4 new images, 2 cameras",
        "Perseverance Sol 2031: 2 new images, 2 cameras",
        "Perseverance Sol 2032: 2 new images, 1 camera",
    ]
    assert re.fullmatch(
        r"Perseverance Sol 1990: 1 new images?, 1 cameras?", child_text(items[3], "title")
    )
    assert len(items) == 4
    assert explicit.content == default.content


async def test_feed_group_sol_guids_are_stable_and_have_no_host(
    client: httpx.AsyncClient,
) -> None:
    first = items_of(await get_feed(client))
    other_host = items_of(await get_feed(client, headers={"Host": "other.example.net"}))

    expected = [
        guid_prefix(2032, T_A),
        guid_prefix(2031, T_B),
        guid_prefix(2032, T_C),
        guid_prefix(1990, T_D),
    ]
    guids = [child_text(i, "guid") for i in first]
    assert guids == expected
    assert [child_text(i, "guid") for i in other_host] == expected
    for item in first:
        assert item.find("guid").attrib["isPermaLink"] == "false"  # type: ignore[union-attr]
    assert len(set(guids)) == len(guids)  # sol 2032 has two batches, two guids


async def test_feed_group_sol_pub_dates_are_rfc_822_gmt_of_feed_at(
    client: httpx.AsyncClient,
) -> None:
    items = items_of(await get_feed(client))

    assert [child_text(i, "pubDate") for i in items] == [
        http_date(T_A),
        http_date(T_B),
        http_date(T_C),
        http_date(T_D),
    ]
    assert re.fullmatch(
        r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT",
        child_text(items[0], "pubDate"),
    )


async def test_feed_group_sol_item_links_to_the_sol_page(client: httpx.AsyncClient) -> None:
    items = items_of(await get_feed(client))

    link = child_text(items[0], "link")
    assert urlsplit(link).scheme + "://" + urlsplit(link).netloc == ORIGIN
    assert urlsplit(link).path == "/rovers/perseverance"
    assert query_of(link) == {"sol": ["2032"]}


async def test_feed_group_sol_description_lists_cameras_thumbnails_sol_link_and_credits(
    client: httpx.AsyncClient, world: dict[str, int], names: dict[str, str]
) -> None:
    item = items_of(await get_feed(client))[0]  # batch A

    html = description_html(item)
    for code, count in (("NAVCAM_LEFT", 2), ("NAVCAM_RIGHT", 2)):
        pattern = rf"(?:{re.escape(code)}|{re.escape(names[code])})\D{{0,12}}{count}\b"
        assert re.search(pattern, html.text), (code, html.text)
    assert card_ids(html) == [world[s] for s in ("A4", "A3", "A2", "A1")]
    assert all(href.startswith(ORIGIN) for href in html.hrefs)
    assert html.srcs and all(src.startswith("https://images.example.test/") for src in html.srcs)
    sol_links = [h for h in html.hrefs if urlsplit(h).path == "/rovers/perseverance"]
    assert any(query_of(h).get("sol") == ["2032"] for h in sol_links)
    assert "Credit:" in html.text
    assert "NASA/JPL-Caltech/LANL" in html.text
    assert "NASA/JPL-Caltech/MSSS" in html.text
    thumbnail = item.find(f"{MEDIA}thumbnail")
    assert thumbnail is not None
    assert thumbnail.attrib["url"] == "https://images.example.test/A4_320.jpg"


async def test_feed_group_sol_description_uses_the_default_credit_when_source_gave_none(
    client: httpx.AsyncClient,
) -> None:
    item = items_of(await get_feed(client))[3]  # batch D: a single photo without credit

    assert "Credit: NASA/JPL-Caltech" in description_html(item).text


async def test_feed_group_sol_description_has_at_most_six_thumbnails(
    client: httpx.AsyncClient, big_batch: list[str]
) -> None:
    response = await get_feed(client, camera="NAVCAM_LEFT", limit=1)

    item = items_of(response)[0]
    assert re.search(r"\b8 new images", child_text(item, "title"))
    html = description_html(item)
    assert len(card_ids(html)) == 6
    assert len(html.srcs) <= 6


async def test_feed_group_sol_thumbnails_are_the_first_photos_of_the_batch(
    client: httpx.AsyncClient, clean_db: Factory, big_batch: list[str]
) -> None:
    ids = await photo_ids(clean_db)

    item = items_of(await get_feed(client, camera="NAVCAM_LEFT", limit=1))[0]

    assert card_ids(description_html(item)) == [ids[s] for s in big_batch[:6]]
    thumbnail = item.find(f"{MEDIA}thumbnail")
    assert thumbnail is not None
    assert thumbnail.attrib["url"] == f"https://images.example.test/{big_batch[0]}_320.jpg"


# --- group=photo --------------------------------------------------------------------------------


async def test_feed_group_photo_makes_one_item_per_photo_in_feed_order(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    assert [child_text(i, "link") for i in items] == [
        f"{ORIGIN}/photos/{world[s]}" for s in PHOTO_ORDER
    ]
    assert [child_text(i, "pubDate") for i in items] == [
        http_date(t) for t in (T_A,) * 4 + (T_B,) * 2 + (T_C,) * 2 + (T_D,)
    ]


async def test_feed_group_photo_guid_is_the_card_url_and_a_permalink(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    for item, source_id in zip(items, PHOTO_ORDER, strict=True):
        guid = item.find("guid")
        assert guid is not None
        assert guid.attrib["isPermaLink"] == "true"
        assert guid.text == f"{ORIGIN}/photos/{world[source_id]}"


async def test_feed_group_photo_title_is_the_photo_title_or_rover_sol_and_camera(
    client: httpx.AsyncClient, names: dict[str, str]
) -> None:
    items = items_of(await get_feed(client, group="photo"))
    titles = {
        source_id: child_text(i, "title") for i, source_id in zip(items, PHOTO_ORDER, strict=True)
    }

    assert titles["A1"] == "Title of A1"
    assert titles["A2"] == f"Perseverance Sol 2032: {names['NAVCAM_LEFT']}"


async def test_feed_group_photo_description_has_preview_caption_and_links(
    client: httpx.AsyncClient, world: dict[str, int], names: dict[str, str]
) -> None:
    items = items_of(await get_feed(client, group="photo"))
    item = items[PHOTO_ORDER.index("A2")]

    html = description_html(item)
    assert "Caption of A2" in html.text
    assert "2032" in html.text
    assert names["NAVCAM_LEFT"] in html.text or "NAVCAM_LEFT" in html.text
    assert "10:05" in html.text  # taken_at, UTC
    assert "Full image (NASA)" in html.text
    assert "Raw image page" in html.text
    assert "https://images.example.test/A2.png" in html.hrefs
    assert "https://mars.nasa.gov/raw/A2" in html.hrefs
    assert any(src.startswith("https://images.example.test/A2") for src in html.srcs)
    assert "Credit: NASA/JPL-Caltech/MSSS" in html.text


async def test_feed_group_photo_media_elements_describe_the_image(
    client: httpx.AsyncClient,
) -> None:
    items = items_of(await get_feed(client, group="photo"))
    with_size = items[PHOTO_ORDER.index("A1")]
    without_size = items[PHOTO_ORDER.index("A2")]

    content = with_size.find(f"{MEDIA}content")
    assert content is not None
    assert content.attrib["url"] == "https://images.example.test/A1.png"
    assert content.attrib["medium"] == "image"
    assert content.attrib["width"] == "1648"
    assert content.attrib["height"] == "1200"
    thumbnail = with_size.find(f"{MEDIA}thumbnail")
    assert thumbnail is not None
    assert thumbnail.attrib["url"] == "https://images.example.test/A1_320.jpg"
    assert child_text(with_size, f"{MEDIA}credit") == "NASA/JPL-Caltech/LANL"
    unsized = without_size.find(f"{MEDIA}content")
    assert unsized is not None
    assert "width" not in unsized.attrib and "height" not in unsized.attrib


async def test_feed_group_photo_without_credit_uses_the_default_in_every_item(
    client: httpx.AsyncClient,
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    for item in items:
        credit = child_text(item, f"{MEDIA}credit")
        assert credit
        assert f"Credit: {credit}" in description_html(item).text
    no_credit = items[PHOTO_ORDER.index("A3")]
    assert child_text(no_credit, f"{MEDIA}credit") == "NASA/JPL-Caltech"
    assert "Credit: NASA/JPL-Caltech" in description_html(no_credit).text


# --- ordering, window, "new" semantics ----------------------------------------------------------


async def test_feed_orders_batches_newest_first_and_photos_by_sol_taken_at_id(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    # A4 and A3 share taken_at: the later id (A4) comes first; B/C by taken_at DESC.
    assert world["A4"] > world["A3"]
    assert [child_text(i, "link") for i in items[:4]] == [
        f"{ORIGIN}/photos/{world[s]}" for s in ("A4", "A3", "A2", "A1")
    ]
    pub = [parsedate_to_datetime(child_text(i, "pubDate")) for i in items]
    assert pub == sorted(pub, reverse=True)


async def test_feed_window_excludes_old_and_non_feed_photos_but_keeps_late_old_sols(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, group="photo"))

    links = {child_text(i, "link") for i in items}
    assert f"{ORIGIN}/photos/{world['D1']}" in links  # sol 1990 stored 20 days ago: inside
    assert f"{ORIGIN}/photos/{world['E1']}" not in links  # stored 31 days ago: outside
    assert f"{ORIGIN}/photos/{world['F1']}" not in links  # feed_at NULL (backfill/import)
    assert len(items) == 9


async def test_feed_does_not_mix_rovers(client: httpx.AsyncClient, world: dict[str, int]) -> None:
    items = items_of(await get_feed(client, "curiosity", group="photo"))

    assert [child_text(i, "link") for i in items] == [
        f"{ORIGIN}/photos/{world['K2']}",
        f"{ORIGIN}/photos/{world['K1']}",
    ]


@pytest.mark.parametrize(
    ("params", "expected_titles"),
    [
        pytest.param(
            {"camera": "navcam_left"},
            [
                "Perseverance Sol 2032: 2 new images, 1 camera",
                "Perseverance Sol 2031: 1 new image, 1 camera",
                "Perseverance Sol 2032: 2 new images, 1 camera",
            ],
            id="camera-lowercase-counts-only-matching-photos",
        ),
        pytest.param(
            {"sample_type": "subframe"},
            ["Perseverance Sol 2032: 1 new image, 1 camera"],
            id="sample-type",
        ),
        pytest.param(
            {"camera": "SUPERCAM_RMI"},
            ["Perseverance Sol 1990: 1 new image, 1 camera"],
            id="camera-with-only-an-old-sol",
        ),
    ],
)
async def test_feed_filters_restrict_batches_and_their_counts(
    client: httpx.AsyncClient, params: dict[str, str], expected_titles: list[str]
) -> None:
    items = items_of(await get_feed(client, **params))

    assert [singular(child_text(i, "title")) for i in items] == [
        singular(t) for t in expected_titles
    ]


def singular(title: str) -> str:
    """Accept both "1 new image" and "1 new images" where the contract says "image(s)"."""
    return re.sub(r"(image|camera)s", r"", title)


async def test_feed_plural_titles_for_filtered_batches_are_exact(
    client: httpx.AsyncClient,
) -> None:
    items = items_of(await get_feed(client, camera="NAVCAM_LEFT"))

    assert child_text(items[0], "title") == "Perseverance Sol 2032: 2 new images, 1 camera"
    assert child_text(items[2], "title") == "Perseverance Sol 2032: 2 new images, 1 camera"


async def test_feed_filter_suffixes_extend_the_group_guid(client: httpx.AsyncClient) -> None:
    by_camera = items_of(await get_feed(client, camera="navcam_left"))
    by_type = items_of(await get_feed(client, sample_type="subframe"))
    both = items_of(await get_feed(client, camera="NAVCAM_LEFT", sample_type="subframe"))

    assert child_text(by_camera[0], "guid") == guid_prefix(2032, T_A) + ":camera-NAVCAM_LEFT"
    assert child_text(by_type[0], "guid") == guid_prefix(2032, T_A) + ":type-subframe"
    both_guid = child_text(both[0], "guid")
    assert both_guid.startswith(guid_prefix(2032, T_A))
    assert ":camera-NAVCAM_LEFT" in both_guid and ":type-subframe" in both_guid


async def test_feed_filtered_item_links_keep_the_filters(client: httpx.AsyncClient) -> None:
    items = items_of(await get_feed(client, camera="NAVCAM_LEFT", sample_type="full"))

    assert query_of(child_text(items[0], "link")) == {
        "sol": ["2032"],
        "camera": ["NAVCAM_LEFT"],
        "sample_type": ["full"],
    }


async def test_feed_group_photo_filters_apply_to_photos(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, group="photo", camera="MCZ_LEFT"))

    assert [child_text(i, "link") for i in items] == [f"{ORIGIN}/photos/{world['B2']}"]


# --- Last-Modified and lastBuildDate ------------------------------------------------------------


async def test_feed_last_modified_is_the_newest_item_time_in_whole_seconds(
    client: httpx.AsyncClient,
) -> None:
    response = await get_feed(client)

    assert T_A.microsecond == 123
    assert response.headers["last-modified"] == http_date(T_A)
    assert child_text(channel_of(parse(response)), "lastBuildDate") == http_date(T_A)


@pytest.mark.parametrize(
    ("params", "newest"),
    [
        pytest.param({"camera": "MCZ_LEFT"}, T_B, id="camera"),
        pytest.param({"camera": "SUPERCAM_RMI"}, T_D, id="old-batch-only"),
        pytest.param({"sample_type": "subframe", "group": "photo"}, T_A, id="type-and-group"),
    ],
)
async def test_feed_last_modified_follows_the_filtered_items(
    client: httpx.AsyncClient, params: dict[str, str], newest
) -> None:
    response = await get_feed(client, **params)

    assert response.headers["last-modified"] == http_date(newest)
    assert child_text(channel_of(parse(response)), "lastBuildDate") == http_date(newest)


async def test_feed_last_modified_ignores_items_cut_by_the_limit(
    client: httpx.AsyncClient,
) -> None:
    response = await get_feed(client, limit=1)

    assert len(items_of(response)) == 1
    assert response.headers["last-modified"] == http_date(T_A)


# --- absolute URLs ------------------------------------------------------------------------------


@pytest.mark.parametrize("host", [HOST, "feed.example.org:8443", "mars.example.net"])
@pytest.mark.parametrize("group", ["sol", "photo"])
async def test_feed_links_are_absolute_and_use_the_request_host(
    client: httpx.AsyncClient, host: str, group: str
) -> None:
    channel = channel_of(parse(await get_feed(client, group=group, headers={"Host": host})))

    origin = f"http://{host}"
    assert child_text(channel, "link").startswith(origin + "/rovers/perseverance")
    self_href = channel.find(f"{ATOM}link").attrib["href"]  # type: ignore[union-attr]
    assert self_href.startswith(origin + "/api/v2/rovers/perseverance/feed.xml")
    assert channel.findall("item")
    for item in channel.findall("item"):
        assert child_text(item, "link").startswith(origin + "/")
        html = description_html(item)
        assert html.hrefs
        assert all(h.startswith(("http://", "https://")) for h in html.hrefs), html.hrefs
        app_links = [h for h in html.hrefs if "/photos/" in h or "/rovers/" in h]
        assert app_links
        assert all(h.startswith(origin + "/") for h in app_links), app_links


async def test_feed_is_deterministic_between_requests(client: httpx.AsyncClient) -> None:
    first = await get_feed(client, group="photo")
    second = await get_feed(client, group="photo")

    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["last-modified"] == second.headers["last-modified"]


async def test_feed_group_photo_orders_by_sol_taken_at_and_id_not_by_insertion(
    client: httpx.AsyncClient, clean_db: Factory, order_batch: list[str]
) -> None:
    ids = await photo_ids(clean_db)
    assert ids["X1"] < ids["X2"] < ids["X3"] < ids["X4"]  # ids do not follow the expected order

    items = items_of(await get_feed(client, group="photo"))

    assert [child_text(i, "link") for i in items[:4]] == [
        f"{ORIGIN}/photos/{ids[s]}" for s in order_batch
    ]
