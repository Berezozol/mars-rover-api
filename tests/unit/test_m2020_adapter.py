"""Contract tests for app.sources.m2020: parse_m2020_image and M2020Adapter.

Inputs are recorded upstream fixtures (tests/fixtures/upstream/m2020/) and synthetic quirk
fixtures (tests/fixtures/upstream/synthetic/m2020_quirks.json). No real network.
"""

import copy
import logging
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
import respx

from app.sources import http, m2020
from tests.unit._upstream import (
    M2020_URL,
    FakeClock,
    by_id,
    load_fixture,
    make_settings,
    paged_responder,
    query,
)

pytestmark = pytest.mark.unit

RECORDED = [
    "m2020/list_page0.json",
    "m2020/sol_2029.json",
    "m2020/sol_2029_page1.json",
    "m2020/range_1000_page0.json",
    "m2020/range_1000_page3.json",
]
QUIRKS = "synthetic/m2020_quirks.json"
NAVCAM_ID = "NLF_1000_0755730345_897ECM_N0474404NCAM00501_01_295J"


def _recorded_images() -> list[Any]:
    params = []
    for name in RECORDED:
        for image in load_fixture(name)["images"]:
            params.append(pytest.param(image, id=f"{name.split('/')[1]}:{image['imageid'][:24]}"))
    return params


def _quirk(imageid: str) -> dict[str, Any]:
    return by_id(load_fixture(QUIRKS)["images"], "imageid", imageid)


def _contains_empty(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_empty(v) for v in value.values()) or not value
    if isinstance(value, list):
        return any(_contains_empty(v) for v in value)
    return value is None or value == "UNK" or (isinstance(value, str) and not value.strip())


@pytest.fixture
async def make_adapter(fake_clock: FakeClock) -> AsyncIterator[Callable[..., Any]]:
    clients: list[Any] = []

    def factory(**overrides: Any) -> Any:
        settings = make_settings(**overrides)
        client = http.UpstreamClient(settings, sleep=fake_clock.sleep, clock=fake_clock)
        clients.append(client)
        return m2020.M2020Adapter(client, settings)

    yield factory
    for client in clients:
        await client.aclose()


async def _collect(agen: Any) -> list[tuple[int, list[Any]]]:
    return [(sol, records) async for sol, records in agen]


def _page(images: list[dict[str, Any]], total: int, page: int | str = 0, per_page: int = 100):
    return {
        "images": images,
        "per_page": str(per_page),
        "total_results": total,
        "type": "mars2020-images-list-1.1",
        "page": page,
        "mission": "mars2020",
        "total_images": 1055868,
    }


def _image(sol: int, imageid: str, **changes: Any) -> dict[str, Any]:
    """A real recorded image re-labelled with another sol and id."""
    image = copy.deepcopy(load_fixture("m2020/range_1000_page0.json")["images"][0])
    image.update({"sol": sol, "imageid": imageid}, **changes)
    return image


# --- parse_m2020_image: recorded data ----------------------------------------------------


def test_parse_m2020_image_recorded_navcam_maps_every_field() -> None:
    raw = by_id(load_fixture("m2020/range_1000_page0.json")["images"], "imageid", NAVCAM_ID)
    files = "https://mars.nasa.gov/mars2020-raw-images/pub/ods/surface/sol/01000/ids/edr/browse/ncam/NLF_1000_0755730345_897ECM_N0474404NCAM00501_01_295J01"

    record = m2020.parse_m2020_image(raw)

    assert record is not None
    assert record.source_id == NAVCAM_ID
    assert record.sol == 1000
    assert record.camera_code == "NAVCAM_LEFT"
    assert record.taken_at == datetime(2023, 12, 13, 9, 18, 57, 463000, tzinfo=UTC)
    assert record.earth_date == date(2023, 12, 13)
    assert record.received_at == datetime(2023, 12, 14, 2, 22, 19, tzinfo=UTC)
    assert record.sample_type == "full"
    assert record.img_full == files + ".png"
    assert record.img_small == files + "_320.jpg"
    assert record.img_medium == files + "_800.jpg"
    assert record.img_large == files + "_1200.jpg"
    assert record.title == "Mars Perseverance Sol 1000: Left Navigation Camera (Navcam)"
    assert record.caption == raw["caption"].strip()
    assert record.credit == "NASA/JPL-Caltech"
    assert record.source_link == (
        "https://mars.nasa.gov/mars2020/multimedia/raw-images/" + NAVCAM_ID
    )
    assert record.site == 47
    assert record.drive == 4404
    assert record.extra == {
        "extended": raw["extended"],
        "attitude": "(0.540374,-0.0557616,-0.0466456,-0.838279)",
        "camera": {
            "camera_vector": raw["camera"]["camera_vector"],
            "camera_model_component_list": raw["camera"]["camera_model_component_list"],
            "camera_position": "(1.02554,0.563899,-2.05004)",
            "camera_model_type": "CAHVORE",
        },
    }


def test_parse_m2020_image_drive_string_zero_becomes_int_zero() -> None:
    raw = load_fixture("m2020/sol_2029.json")["images"][0]
    assert raw["drive"] == "0"  # fixture precondition

    record = m2020.parse_m2020_image(raw)

    assert record.drive == 0
    assert record.site == 93
    assert record.taken_at == datetime(2026, 11, 6, 12, 16, 25, 848000, tzinfo=UTC)


@pytest.mark.parametrize("raw", _recorded_images())
def test_parse_m2020_image_every_recorded_image_is_normalized(raw: dict[str, Any]) -> None:
    record = m2020.parse_m2020_image(raw)

    assert record is not None
    assert record.source_id == raw["imageid"]
    assert record.sol == raw["sol"]
    assert record.camera_code == raw["camera"]["instrument"]
    assert record.taken_at.tzinfo is not None
    assert record.taken_at.utcoffset().total_seconds() == 0
    assert record.earth_date == record.taken_at.date()
    assert record.sample_type == "full"
    assert record.img_full == raw["image_files"]["full_res"]
    assert isinstance(record.drive, int)
    assert record.credit == raw["credit"]
    assert "instrument" not in record.extra.get("camera", {})
    assert not _contains_empty(record.extra), record.extra


# --- parse_m2020_image: quirks -----------------------------------------------------------


def test_parse_m2020_image_quirks_are_normalized() -> None:
    record = m2020.parse_m2020_image(_quirk("SYN_M20_QUIRKS"))

    assert record is not None
    assert record.sol == 1000  # "1000"
    assert record.camera_code == "NAVCAM_LEFT"  # "navcam_left"
    assert record.taken_at == datetime(2023, 12, 13, 23, 59, 59, 900000, tzinfo=UTC)  # naive
    assert record.earth_date == date(2023, 12, 13)
    assert record.received_at == datetime(2023, 12, 14, 2, 22, 19, tzinfo=UTC)  # +02:00
    assert record.title == "Synthetic quirky image"  # stripped
    assert record.caption is None  # ""
    assert record.site == 47  # "47"
    assert record.drive is None  # "UNK"
    assert record.extra == {
        "extended": {"sclk": "755730347.366", "subframeRect": "(1,1,5120,3840)"},
        "camera": {"camera_model_type": "CAHVORE"},
    }


def test_parse_m2020_image_unknown_preview_sizes_become_none() -> None:
    record = m2020.parse_m2020_image(_quirk("SYN_M20_SMALL_SIZES_UNK"))

    assert record is not None
    assert (record.img_small, record.img_medium, record.img_large) == (None, None, None)
    assert record.img_full.endswith(".png")


def test_parse_m2020_image_thumbnail_returns_none_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    assert m2020.parse_m2020_image(_quirk("SYN_M20_THUMBNAIL")) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "imageid",
    [
        "SYN_M20_NO_FULL_RES",
        "SYN_M20_NO_INSTRUMENT",
        "SYN_M20_UNK_INSTRUMENT",
        "SYN_M20_BAD_DATE",
        "SYN_M20_NULL_SOL",
        "SYN_M20_NO_SAMPLE_TYPE",
    ],
    ids=lambda i: i.removeprefix("SYN_M20_").lower(),
)
def test_parse_m2020_image_missing_required_returns_none_and_warns(
    caplog: pytest.LogCaptureFixture, imageid: str
) -> None:
    caplog.set_level(logging.WARNING)

    assert m2020.parse_m2020_image(_quirk(imageid)) is None
    assert any(imageid in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_parse_m2020_image_missing_imageid_returns_none() -> None:
    raw = _quirk("SYN_M20_QUIRKS")
    del raw["imageid"]
    assert m2020.parse_m2020_image(raw) is None


def test_parse_m2020_image_bad_drive_keeps_record_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    record = m2020.parse_m2020_image(_quirk("SYN_M20_BAD_DRIVE"))

    assert record is not None
    assert record.drive is None
    assert any("drive" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


# --- M2020Adapter.latest_sol -------------------------------------------------------------


async def test_latest_sol_reads_latest_feed(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    route = upstream_router.get(M2020_URL).respond(200, json=load_fixture("m2020/latest.json"))
    adapter = make_adapter()

    assert adapter.rover_id == "perseverance"
    assert await adapter.latest_sol() == 2031
    assert query(route.calls.last.request) == {
        "feed": "raw_images",
        "category": "mars2020",
        "feedtype": "json",
        "latest": "true",
    }


# --- M2020Adapter.fetch_sols -------------------------------------------------------------


def _range_pages(sizes: list[int]) -> dict[int, Any]:
    """Pages of sol 1000 whose image counts are ``sizes``; ``total_results`` is their sum.

    Real recorded images come first (page 0 and page 3 fixtures, trimmed to 4-5 images);
    each page is padded with clones of them under new unique ids.
    """
    recorded = {
        0: load_fixture("m2020/range_1000_page0.json"),
        3: load_fixture("m2020/range_1000_page3.json"),
    }
    total = sum(sizes)
    pages = {}
    for k, size in enumerate(sizes):
        source = recorded.get(k, recorded[0])
        originals = source["images"] if k in recorded else []
        images = copy.deepcopy(originals[:size])
        n = 0
        while len(images) < size:
            clone = copy.deepcopy(source["images"][n % len(source["images"])])
            clone["imageid"] = f"{clone['imageid']}_P{k}_{n}"
            images.append(clone)
            n += 1
        pages[k] = {**source, "page": k if k == 0 else str(k), "images": images}
        pages[k]["total_results"] = total
    return pages


async def test_fetch_sols_single_sol_walks_all_pages_with_range_filter(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    pages = _range_pages([100, 100, 100, 85])  # live: sol 1000 -> total_results 385
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))
    adapter = make_adapter(m2020_page_size=100)

    result = await _collect(adapter.fetch_sols(1000, 1000))

    sent = [query(call.request) for call in route.calls]
    assert [q["page"] for q in sent] == ["0", "1", "2", "3"]
    for q in sent:
        assert q == {
            "feed": "raw_images",
            "category": "mars2020",
            "feedtype": "json",
            "order": "sol desc",
            "num": "100",
            "page": q["page"],
            "condition_2": "1000:sol:gte",
            "condition_3": "1000:sol:lte",
        }
    assert [sol for sol, _ in result] == [1000]
    records = result[0][1]
    expected_ids = [img["imageid"] for k in range(4) for img in pages[k]["images"]]
    assert [r.source_id for r in records] == expected_ids
    assert len(records) == 385
    assert records[0].source_id == NAVCAM_ID
    assert all(r.sol == 1000 for r in records)
    # Only the last page is short: nothing to warn about.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    ("sizes", "expected_pages"),
    [
        pytest.param([100, 100, 100, 85], ["0", "1", "2", "3"], id="385-four-pages"),
        pytest.param([100, 100, 100, 1], ["0", "1", "2", "3"], id="301-four-pages"),
        pytest.param([100, 100, 100], ["0", "1", "2"], id="300-exactly-three-pages"),
        pytest.param([100], ["0"], id="100-one-page"),
        pytest.param([42], ["0"], id="42-one-page"),
    ],
)
async def test_fetch_sols_stops_when_received_count_reaches_total_results(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    sizes: list[int],
    expected_pages: list[str],
) -> None:
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(_range_pages(sizes)))

    result = await _collect(make_adapter(m2020_page_size=100).fetch_sols(1000, 1000))

    assert [query(c.request)["page"] for c in route.calls] == expected_pages
    assert len(result[0][1]) == sum(sizes)


async def test_fetch_sols_feed_returns_fewer_than_num_keeps_paging_and_warns(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """num=100 is configured but the feed serves 50 per page: stop by count, not page*num."""
    caplog.set_level(logging.WARNING)
    pages = _range_pages([50, 50, 20])
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))

    result = await _collect(make_adapter(m2020_page_size=100).fetch_sols(1000, 1000))

    assert [query(c.request)["page"] for c in route.calls] == ["0", "1", "2"]
    expected_ids = [img["imageid"] for k in range(3) for img in pages[k]["images"]]
    assert [r.source_id for r in result[0][1]] == expected_ids
    assert len(expected_ids) == 120
    assert [r for r in caplog.records if r.levelno == logging.WARNING], "short non-last page"


async def test_fetch_sols_empty_result_yields_nothing(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    route = upstream_router.get(M2020_URL).respond(200, json=_page([], 0))

    assert await _collect(make_adapter().fetch_sols(2025, 2030)) == []
    assert route.call_count == 1


async def test_fetch_sols_empty_page_before_total_stops_yields_collected_and_warns(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    pages = {
        0: _page([_image(1000, "A"), _image(1000, "B")], total=1234, per_page=2),
        1: _page([], total=1234, page="1", per_page=2),
    }
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))

    result = await _collect(make_adapter(m2020_page_size=2).fetch_sols(1000, 1000))

    assert route.call_count == 2
    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [(1000, ["A", "B"])]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    def mentions(message: str, number: int) -> bool:
        return re.search(rf"(?<!\d){number}(?!\d)", message) is not None

    # Range 1000..1000, empty page 1, fetched 2 of total 1234.
    assert any(
        mentions(m, 1000) and mentions(m, 1) and mentions(m, 2) and mentions(m, 1234)
        for m in warnings
    ), warnings


async def test_fetch_sols_groups_sol_spanning_pages_and_yields_descending(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        0: _page([_image(1002, "A"), _image(1001, "B")], total=4, per_page=2),
        1: _page([_image(1001, "C"), _image(999, "D")], total=4, page="1", per_page=2),
    }
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))
    adapter = make_adapter(m2020_page_size=2)

    result = await _collect(adapter.fetch_sols(999, 1002))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [
        (1002, ["A"]),
        (1001, ["B", "C"]),
        (999, ["D"]),
    ]
    sent = query(route.calls[0].request)
    assert (sent["condition_2"], sent["condition_3"], sent["num"]) == (
        "999:sol:gte",
        "1002:sol:lte",
        "2",
    )


async def test_fetch_sols_yields_finished_sol_before_requesting_next_page(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        0: _page([_image(1002, "A"), _image(1001, "B")], total=4, per_page=2),
        1: _page([_image(1001, "C"), _image(999, "D")], total=4, page="1", per_page=2),
    }
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))
    agen = make_adapter(m2020_page_size=2).fetch_sols(999, 1002)

    sol, records = await agen.__anext__()

    assert (sol, [r.source_id for r in records]) == (1002, ["A"])
    assert route.call_count == 1
    await agen.aclose()


async def test_fetch_sols_drops_thumbnails_and_invalid_and_skips_sols_left_empty(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    images = [
        _image(1001, "GOOD"),
        _image(1001, "THUMB", sample_type="Thumbnail"),
        _image(1001, "NO_INSTRUMENT", camera={"instrument": "UNK"}),
        _image(1000, "ONLY_THUMB", sample_type="Thumbnail"),
    ]
    upstream_router.get(M2020_URL).respond(200, json=_page(images, total=4))

    result = await _collect(make_adapter().fetch_sols(1000, 1001))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [(1001, ["GOOD"])]


async def test_fetch_sols_out_of_range_images_skipped_with_warning(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    images = [_image(1005, "TOO_NEW"), _image(1001, "IN_RANGE"), _image(990, "TOO_OLD")]
    upstream_router.get(M2020_URL).respond(200, json=_page(images, total=3))

    result = await _collect(make_adapter().fetch_sols(1000, 1002))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [(1001, ["IN_RANGE"])]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) >= 2


async def test_fetch_sols_max_pages_reached_raises_and_keeps_completed_sols(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        0: _page([_image(1002, "A"), _image(1001, "B")], total=10_000, per_page=2),
        1: _page([_image(1001, "C"), _image(1000, "D")], total=10_000, page="1", per_page=2),
        2: _page([_image(1000, "E"), _image(1000, "F")], total=10_000, page="2", per_page=2),
    }
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))
    adapter = make_adapter(m2020_page_size=2, upstream_max_pages=3)
    yielded: list[tuple[int, list[str]]] = []

    with pytest.raises(http.PageLimitExceeded) as exc_info:
        async for sol, records in adapter.fetch_sols(999, 1002):
            yielded.append((sol, [r.source_id for r in records]))

    assert route.call_count == 3
    # Sol 1000 may continue on the next (unfetched) page, so it must not be yielded.
    assert yielded == [(1002, ["A"]), (1001, ["B", "C"])]
    assert isinstance(exc_info.value, http.UpstreamError)


async def test_fetch_sols_max_pages_exactly_enough_does_not_raise(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        0: _page([_image(1001, "A"), _image(1001, "B")], total=4, per_page=2),
        1: _page([_image(1000, "C"), _image(1000, "D")], total=4, page="1", per_page=2),
    }
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))
    adapter = make_adapter(m2020_page_size=2, upstream_max_pages=2)

    result = await _collect(adapter.fetch_sols(1000, 1001))

    assert route.call_count == 2
    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [
        (1001, ["A", "B"]),
        (1000, ["C", "D"]),
    ]


def test_page_limit_exceeded_is_an_upstream_error() -> None:
    assert issubclass(http.PageLimitExceeded, http.UpstreamError)


async def test_fetch_sols_duplicate_imageid_in_sol_yielded_once_with_warning(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    first = _image(1001, "DUP_M20_ID", title="first")
    second = _image(1001, "DUP_M20_ID", title="second")
    pages = {
        0: _page([first, _image(1001, "B")], total=4, per_page=2),
        1: _page([second, _image(1000, "C")], total=4, page="1", per_page=2),
    }
    upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))

    result = await _collect(make_adapter(m2020_page_size=2).fetch_sols(1000, 1001))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [
        (1001, ["DUP_M20_ID", "B"]),
        (1000, ["C"]),
    ]
    assert result[0][1][0].title == "first"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("DUP_M20_ID" in m for m in warnings), warnings


@pytest.mark.parametrize(
    ("from_sol", "to_sol"),
    [pytest.param(1001, 1000, id="from-greater-than-to"), pytest.param(-1, 5, id="negative")],
)
async def test_fetch_sols_invalid_range_raises_value_error(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    from_sol: int,
    to_sol: int,
) -> None:
    route = upstream_router.get(M2020_URL).respond(200, json=_page([], 0))

    with pytest.raises(ValueError):
        await _collect(make_adapter().fetch_sols(from_sol, to_sol))
    assert route.call_count == 0


async def test_fetch_sols_upstream_failure_raises_upstream_error(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    upstream_router.get(M2020_URL).respond(404)

    with pytest.raises(http.UpstreamError):
        await _collect(make_adapter().fetch_sols(1000, 1000))
