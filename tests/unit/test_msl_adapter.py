"""Contract tests for app.sources.msl: parse_msl_item and MslAdapter.

Inputs are recorded upstream fixtures (tests/fixtures/upstream/msl/) and synthetic quirk
fixtures (tests/fixtures/upstream/synthetic/msl_quirks.json). No real network.

The sol 4327 pages were recorded WITHOUT the `extended=thumbnail::sample_type::noteq` filter
(508 items, 3 pages; with the filter it is 280 items, 2 pages). That is fine for pagination
tests: the adapter must follow `more` whatever the page count, and drop thumbnails itself.
"""

import copy
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import respx

from app.sources import http, msl
from tests.unit._upstream import (
    MSL_ORDER,
    MSL_URL,
    FakeClock,
    by_id,
    load_fixture,
    make_settings,
    paged_responder,
    query,
)

pytestmark = pytest.mark.unit

SOL_PAGES = ["msl/sol_4327_page0.json", "msl/sol_4327_page1.json", "msl/sol_4327_page2.json"]
QUIRKS = "synthetic/msl_quirks.json"


def _recorded_items() -> list[Any]:
    params = []
    for name in [*SOL_PAGES, "msl/latest.json"]:
        for item in load_fixture(name)["items"]:
            params.append(pytest.param(item, id=f"{name.split('/')[1]}:{item['id']}"))
    return params


def _recorded(item_id: int) -> dict[str, Any]:
    items = [i for name in SOL_PAGES for i in load_fixture(name)["items"]]
    return by_id(items, "id", item_id)


def _quirk(item_id: int) -> dict[str, Any]:
    return by_id(load_fixture(QUIRKS)["items"], "id", item_id)


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
        return msl.MslAdapter(client, settings)

    yield factory
    for client in clients:
        await client.aclose()


async def _collect(agen: Any) -> list[tuple[int, list[Any]]]:
    return [(sol, records) async for sol, records in agen]


def _response(items: list[dict[str, Any]], *, more: bool, page: int = 0, total: int = 0):
    return {"items": items, "more": more, "total": total, "page": page, "per_page": 200}


def _item(sol: int, item_id: int, **changes: Any) -> dict[str, Any]:
    """A real recorded non-thumbnail item re-labelled with another sol and id."""
    item = _recorded(1387954)
    item.update({"sol": sol, "id": item_id, "link": f"/raw_images/{item_id}"}, **changes)
    return item


def _by_sol_and_page(pages: dict[tuple[int, int], Any]) -> Callable[[httpx.Request], Any]:
    def key(request: httpx.Request) -> tuple[int, int]:
        q = query(request)
        return int(q["condition_2"].split(":")[0]), int(q["page"])

    return paged_responder(pages, key=key)


# --- parse_msl_item: recorded data -------------------------------------------------------


def test_parse_msl_item_recorded_mastcam_maps_every_field() -> None:
    raw = _recorded(1387954)
    url = "https://mars.nasa.gov/msl-raw-images/msss/04327/mcam/4327MR1065450012201536E01_DXXX.jpg"

    record = msl.parse_msl_item(raw)

    assert record is not None
    assert record.source_id == "1387954"
    assert record.sol == 4327
    assert record.camera_code == "MAST_RIGHT"
    assert record.taken_at == datetime(2024, 10, 8, 6, 50, 23, tzinfo=UTC)
    assert record.earth_date == date(2024, 10, 8)
    assert record.received_at == datetime(2024, 10, 9, 21, 30, 32, tzinfo=UTC)
    assert record.sample_type == "subframe"
    assert record.img_full == url
    assert (record.img_small, record.img_medium, record.img_large) == (None, None, None)
    assert record.title == "Sol 4327: Mast Camera (Mastcam)"
    assert record.caption == raw["description"]
    assert record.credit == "NASA/JPL-Caltech/MSSS"
    assert record.source_link == "https://mars.nasa.gov/raw_images/1387954"
    assert record.site is None
    assert record.drive is None
    assert record.extra == {
        "imageid": "4327MR1065450012201536E01_DXXX",
        "extended": {"bucket": "msl-raws", "url_list": url, "contributor": "MSSS"},
    }


def test_parse_msl_item_recorded_chemcam_keeps_position_and_camera_fields() -> None:
    raw = _recorded(1387295)

    record = msl.parse_msl_item(raw)

    assert record is not None
    assert record.camera_code == "CHEMCAM_RMI"
    assert record.sample_type == "full"
    assert record.site == 109
    assert record.drive == 1494
    assert record.credit == raw["image_credit"]
    expected_extended = {k: v for k, v in raw["extended"].items() if k != "sample_type"}
    del expected_extended["filter_name"]  # null in the fixture
    assert record.extra == {
        "imageid": raw["imageid"],
        "extended": expected_extended,
        "attitude": raw["attitude"],
        "xyz": "(36.6699,-36.2548,4.63875)",
        "camera_vector": raw["camera_vector"],
        "camera_model_component_list": raw["camera_model_component_list"],
        "camera_position": raw["camera_position"],
        "camera_model_type": "CAHV",
    }
    for service_field in ("created_at", "updated_at", "instrument_sort", "spacecraft_clock"):
        assert service_field not in record.extra


@pytest.mark.parametrize(
    ("item_id", "sample_type"),
    [
        pytest.param(1387296, "chemcam prc", id="chemcam-prc"),
        pytest.param(1387221, "mixed", id="mixed"),
        pytest.param(1387376, "full", id="full"),
        pytest.param(1390993, "subframe", id="subframe"),
    ],
)
def test_parse_msl_item_recorded_sample_types_kept_lower_case(
    item_id: int, sample_type: str
) -> None:
    record = msl.parse_msl_item(_recorded(item_id))

    assert record is not None
    assert record.sample_type == sample_type


@pytest.mark.parametrize("raw", _recorded_items())
def test_parse_msl_item_every_recorded_item_is_normalized_or_thumbnail_dropped(
    raw: dict[str, Any],
) -> None:
    record = msl.parse_msl_item(raw)

    if raw["is_thumbnail"]:
        assert record is None
        return
    assert record is not None
    assert record.source_id == str(raw["id"])
    assert record.sol == raw["sol"]
    assert record.camera_code == raw["instrument"]
    assert record.taken_at.utcoffset().total_seconds() == 0
    assert record.earth_date == record.taken_at.date()
    assert record.img_full == raw["https_url"]
    assert record.source_link == "https://mars.nasa.gov" + raw["link"]
    assert "sample_type" not in record.extra.get("extended", {})
    assert not _contains_empty(record.extra), record.extra


# --- parse_msl_item: quirks --------------------------------------------------------------


def test_parse_msl_item_quirks_are_normalized() -> None:
    raw = _quirk(9000001)

    record = msl.parse_msl_item(raw)

    assert record is not None
    assert record.source_id == "9000001"
    assert record.camera_code == "MAST_LEFT"  # "mast_left"
    assert record.img_full == raw["url"]  # https_url is null
    assert record.taken_at == datetime(2024, 10, 8, 6, 50, 23, tzinfo=UTC)  # no timezone
    assert record.sample_type == "chemcam prc"  # "Chemcam PRC"
    assert record.site == 12  # "12"
    assert record.drive is None  # null
    assert record.caption is None  # whitespace-only description
    assert record.source_link == "https://mars.nasa.gov/raw_images/9000001"
    assert "xyz" not in record.extra  # "UNK"
    assert "mast_az" not in record.extra["extended"]  # "UNK"
    assert "lmst" not in record.extra["extended"]  # ""
    assert record.extra["extended"]["mast_el"] == "-10.045"


def test_parse_msl_item_absolute_link_kept_and_bad_drive_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    record = msl.parse_msl_item(_quirk(9000006))

    assert record is not None
    assert record.source_link == "https://mars.nasa.gov/raw_images/9000006"
    assert record.sample_type == "mixed"
    assert record.drive is None  # "x1"
    assert record.site == 109
    assert any("drive" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


@pytest.mark.parametrize(
    "item_id",
    [
        pytest.param(9000002, id="is-thumbnail-true"),
        pytest.param(9000003, id="sample-type-thumbnail"),
    ],
)
def test_parse_msl_item_thumbnail_returns_none_without_warning(
    caplog: pytest.LogCaptureFixture, item_id: int
) -> None:
    caplog.set_level(logging.DEBUG)

    assert msl.parse_msl_item(_quirk(item_id)) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "item_id",
    [
        pytest.param(9000004, id="no-instrument"),
        pytest.param(9000005, id="no-image-url"),
        pytest.param(9000007, id="no-date-taken"),
        pytest.param(9000008, id="no-sample-type"),
    ],
)
def test_parse_msl_item_missing_required_returns_none_and_warns(
    caplog: pytest.LogCaptureFixture, item_id: int
) -> None:
    caplog.set_level(logging.WARNING)

    assert msl.parse_msl_item(_quirk(item_id)) is None
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


# --- MslAdapter.latest_sol ---------------------------------------------------------------


async def test_latest_sol_reads_first_item_of_latest_query(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    route = upstream_router.get(MSL_URL).respond(200, json=load_fixture("msl/latest.json"))
    adapter = make_adapter()

    assert adapter.rover_id == "curiosity"
    assert await adapter.latest_sol() == 5073
    assert query(route.calls.last.request) == {
        "order": MSL_ORDER,
        "per_page": "1",
        "page": "0",
        "condition_1": "msl:mission",
    }


async def test_latest_sol_empty_items_raises_upstream_error(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    upstream_router.get(MSL_URL).respond(200, json=_response([], more=False))

    with pytest.raises(http.UpstreamError):
        await make_adapter().latest_sol()


# --- MslAdapter.fetch_sols ---------------------------------------------------------------


async def test_fetch_sols_sol_4327_walks_pages_until_more_is_false(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {(4327, k): load_fixture(name) for k, name in enumerate(SOL_PAGES)}
    route = upstream_router.get(MSL_URL).mock(side_effect=_by_sol_and_page(pages))

    result = await _collect(make_adapter(msl_page_size=200).fetch_sols(4327, 4327))

    sent = [query(call.request) for call in route.calls]
    assert [q["page"] for q in sent] == ["0", "1", "2"]
    for q in sent:
        assert q == {
            "order": MSL_ORDER,
            "per_page": "200",
            "page": q["page"],
            "condition_1": "msl:mission",
            "condition_2": "4327:sol:in",
            "extended": "thumbnail::sample_type::noteq",
        }
    expected_ids = [
        str(item["id"])
        for k in range(3)
        for item in pages[(4327, k)]["items"]
        if not item["is_thumbnail"]
    ]
    assert len(expected_ids) == 8
    assert [sol for sol, _ in result] == [4327]
    assert [r.source_id for r in result[0][1]] == expected_ids


async def test_fetch_sols_iterates_descending_and_skips_empty_sols(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        (4327, 0): _response([_item(4327, 1)], more=False, total=1),
        (4326, 0): _response([], more=False),
        (4325, 0): _response([_item(4325, 2)], more=True, total=2),
        (4325, 1): _response([_item(4325, 3)], more=False, page=1, total=2),
        (4324, 0): _response([_item(4324, 4, is_thumbnail=True)], more=False, total=1),
    }
    route = upstream_router.get(MSL_URL).mock(side_effect=_by_sol_and_page(pages))

    result = await _collect(make_adapter().fetch_sols(4324, 4327))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [
        (4327, ["1"]),
        (4325, ["2", "3"]),
    ]
    requested = [(query(c.request)["condition_2"], query(c.request)["page"]) for c in route.calls]
    assert requested == [
        ("4327:sol:in", "0"),
        ("4326:sol:in", "0"),
        ("4325:sol:in", "0"),
        ("4325:sol:in", "1"),
        ("4324:sol:in", "0"),
    ]


async def test_fetch_sols_yields_sol_before_requesting_next_sol(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        (11, 0): _response([_item(11, 1)], more=False, total=1),
        (10, 0): _response([_item(10, 2)], more=False, total=1),
    }
    route = upstream_router.get(MSL_URL).mock(side_effect=_by_sol_and_page(pages))
    agen = make_adapter().fetch_sols(10, 11)

    sol, _ = await agen.__anext__()

    assert sol == 11
    assert route.call_count == 1
    await agen.aclose()


async def test_fetch_sols_items_of_other_sol_skipped_with_warning(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    items = [_item(4327, 1), _item(4326, 4326002), _item(4327, 3)]
    upstream_router.get(MSL_URL).respond(200, json=_response(items, more=False, total=3))

    result = await _collect(make_adapter().fetch_sols(4327, 4327))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [(4327, ["1", "3"])]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("4326002" in m for m in warnings), warnings


async def test_fetch_sols_drops_thumbnails_even_if_filter_ignored(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    thumb = copy.deepcopy(_item(4327, 2, is_thumbnail=True))
    thumb["extended"]["sample_type"] = "thumbnail"
    items = [_item(4327, 1), thumb]
    upstream_router.get(MSL_URL).respond(200, json=_response(items, more=False, total=2))

    result = await _collect(make_adapter().fetch_sols(4327, 4327))

    assert [r.source_id for r in result[0][1]] == ["1"]


async def test_fetch_sols_max_pages_reached_raises_and_keeps_completed_sols(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    def key(request: httpx.Request) -> tuple[int, int]:
        q = query(request)
        return int(q["condition_2"].split(":")[0]), int(q["page"])

    def responder(request: httpx.Request) -> httpx.Response:
        sol, k = key(request)
        if sol == 4327:
            return httpx.Response(200, json=_response([_item(4327, 1)], more=False, total=1))
        body = _response([_item(4326, 100 + k)], more=True, page=k, total=10_000)
        return httpx.Response(200, json=body)

    route = upstream_router.get(MSL_URL).mock(side_effect=responder)
    adapter = make_adapter(upstream_max_pages=3)
    yielded: list[tuple[int, list[str]]] = []

    with pytest.raises(http.PageLimitExceeded) as exc_info:
        async for sol, records in adapter.fetch_sols(4325, 4327):
            yielded.append((sol, [r.source_id for r in records]))

    # 1 request for sol 4327, then 3 pages of sol 4326 (cap); sol 4325 is never requested.
    assert [key(c.request) for c in route.calls] == [(4327, 0), (4326, 0), (4326, 1), (4326, 2)]
    assert yielded == [(4327, ["1"])]  # incomplete sol 4326 is not yielded
    assert isinstance(exc_info.value, http.UpstreamError)


async def test_fetch_sols_max_pages_exactly_enough_does_not_raise(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    pages = {
        (4327, 0): _response([_item(4327, 1)], more=True, total=2),
        (4327, 1): _response([_item(4327, 2)], more=False, page=1, total=2),
    }
    route = upstream_router.get(MSL_URL).mock(side_effect=_by_sol_and_page(pages))

    result = await _collect(make_adapter(upstream_max_pages=2).fetch_sols(4327, 4327))

    assert route.call_count == 2
    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [(4327, ["1", "2"])]


async def test_fetch_sols_duplicate_id_in_sol_yielded_once_with_warning(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    pages = {
        (4327, 0): _response(
            [_item(4327, 7777777, title="first"), _item(4327, 2)], more=True, total=4
        ),
        (4327, 1): _response(
            [_item(4327, 7777777, title="second"), _item(4327, 3)], more=False, page=1, total=4
        ),
    }
    upstream_router.get(MSL_URL).mock(side_effect=_by_sol_and_page(pages))

    result = await _collect(make_adapter().fetch_sols(4327, 4327))

    assert [(sol, [r.source_id for r in recs]) for sol, recs in result] == [
        (4327, ["7777777", "2", "3"])
    ]
    assert result[0][1][0].title == "first"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("7777777" in m for m in warnings), warnings


@pytest.mark.parametrize(
    ("from_sol", "to_sol"),
    [pytest.param(4328, 4327, id="from-greater-than-to"), pytest.param(-2, 3, id="negative")],
)
async def test_fetch_sols_invalid_range_raises_value_error(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    from_sol: int,
    to_sol: int,
) -> None:
    route = upstream_router.get(MSL_URL).respond(200, json=_response([], more=False))

    with pytest.raises(ValueError):
        await _collect(make_adapter().fetch_sols(from_sol, to_sol))
    assert route.call_count == 0
