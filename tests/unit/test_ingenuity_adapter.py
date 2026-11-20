"""Contract tests for app.sources.ingenuity (stage 12) and the parameterized M2020Adapter.

Inputs: the recorded page tests/fixtures/upstream/ingenuity/list_page0.json (trimmed to 4
items of sol 1069) and the synthetic tests/fixtures/upstream/synthetic/ingenuity_quirks.json.
No real network.
"""

import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
import respx

from app.sources import http, m2020
from app.sources import ingenuity as ing
from app.sources.registry import _ADAPTERS, SYNCABLE_ROVERS
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

RECORDED = "ingenuity/list_page0.json"
QUIRKS = "synthetic/ingenuity_quirks.json"
NAV_ID = "HNM_1069_0761837420_372ECM_N0720001HELI00173_0000A0J"
RTE_ID = "HSF_1069_0761837397_369ECM_N0720001HELI00001_000085J"
BASE = "https://mars.nasa.gov/mars2020-raw-images/pub/ods/surface/sol/01069/ids/edr/browse/heli/"


def _recorded() -> list[Any]:
    return [pytest.param(img, id=img["imageid"][:24]) for img in load_fixture(RECORDED)["images"]]


def _recorded_by_id(imageid: str) -> dict[str, Any]:
    return by_id(load_fixture(RECORDED)["images"], "imageid", imageid)


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
        return ing.IngenuityAdapter(client, settings)

    yield factory
    for client in clients:
        await client.aclose()


# --- class wiring --------------------------------------------------------------------------


def test_ingenuity_adapter_identity_and_inheritance() -> None:
    assert issubclass(ing.IngenuityAdapter, m2020.M2020Adapter)
    assert ing.IngenuityAdapter.rover_id == "ingenuity"
    assert ing.IngenuityAdapter.category == "ingenuity"


def test_m2020_adapter_identity_is_unchanged() -> None:
    assert m2020.M2020Adapter.rover_id == "perseverance"
    assert m2020.M2020Adapter.category == "mars2020"


def test_registry_has_ingenuity_adapter_but_it_is_not_syncable() -> None:
    assert _ADAPTERS["ingenuity"] is ing.IngenuityAdapter
    assert "ingenuity" not in SYNCABLE_ROVERS
    assert SYNCABLE_ROVERS == ("perseverance", "curiosity")


# --- parse_ingenuity_image: recorded data -------------------------------------------------


def test_parse_ingenuity_image_recorded_navigation_camera_maps_every_field() -> None:
    raw = _recorded_by_id(NAV_ID)

    record = ing.parse_ingenuity_image(raw)

    assert record is not None
    assert record.source_id == NAV_ID
    assert record.sol == 1069
    assert record.camera_code == "HELI_NAV"
    assert record.taken_at == datetime(2024, 2, 22, 1, 44, 30, 900092, tzinfo=UTC)
    assert record.earth_date == date(2024, 2, 22)
    assert record.received_at == datetime(2024, 2, 26, 16, 30, 51, tzinfo=UTC)
    assert record.sample_type == "full"
    stem = BASE + "HNM_1069_0761837420_372ECM_N0720001HELI00173_0000A0J01"
    assert record.img_full == stem + ".png"
    assert record.img_small == stem + "_320.jpg"
    assert record.img_medium == stem + "_800.jpg"
    assert record.img_large == stem + "_1200.jpg"
    assert record.title == "Mars Helicopter Sol 1069: Navigation Camera"
    assert record.caption == raw["caption"].strip()
    assert record.credit == "NASA/JPL-Caltech"


def test_parse_ingenuity_image_link_is_kept_as_is_and_absolute() -> None:
    raw = _recorded_by_id(NAV_ID)

    record = ing.parse_ingenuity_image(raw)

    assert record is not None
    assert record.source_link == raw["link"]
    assert record.source_link.startswith("https://")


def test_parse_ingenuity_image_color_camera_is_heli_rte() -> None:
    record = ing.parse_ingenuity_image(_recorded_by_id(RTE_ID))

    assert record is not None
    assert record.camera_code == "HELI_RTE"
    assert record.sample_type == "full"


def test_parse_ingenuity_image_extra_keeps_size_and_local_time_without_placeholders() -> None:
    nav = ing.parse_ingenuity_image(_recorded_by_id(NAV_ID))
    rte = ing.parse_ingenuity_image(_recorded_by_id(RTE_ID))

    assert nav is not None and rte is not None
    assert nav.extra["extended"]["dimension"] == "(640,480)"
    assert rte.extra["extended"]["dimension"] == "(4208,3120)"
    assert nav.extra["date_taken_mars"] == "Sol-01069M11:41:52.53212"
    assert not _contains_empty(nav.extra), nav.extra


@pytest.mark.parametrize("raw", _recorded())
def test_parse_ingenuity_image_site_zero_and_drive_none_string_become_none_silently(
    raw: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert raw["site"] == 0 and raw["drive"] == "None"  # fixture precondition

    record = ing.parse_ingenuity_image(raw)

    assert record is not None
    assert record.site is None
    assert record.drive is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("raw", _recorded())
def test_parse_ingenuity_image_every_recorded_image_is_normalized(raw: dict[str, Any]) -> None:
    record = ing.parse_ingenuity_image(raw)

    assert record is not None
    assert record.source_id == raw["imageid"]
    assert record.sol == raw["sol"] == 1069
    assert record.camera_code == raw["camera"]["instrument"]
    assert record.camera_code in {"HELI_NAV", "HELI_RTE"}
    assert record.taken_at.utcoffset().total_seconds() == 0
    assert record.img_full == raw["image_files"]["full_res"]
    assert record.credit == raw["credit"] == "NASA/JPL-Caltech"
    assert record.source_link == raw["link"]


def test_parse_m2020_image_with_rover_id_ingenuity_equals_parse_ingenuity_image() -> None:
    raw = _recorded_by_id(NAV_ID)

    assert m2020.parse_m2020_image(raw, rover_id="ingenuity") == ing.parse_ingenuity_image(raw)


# --- parse_ingenuity_image: quirks ----------------------------------------------------------


def test_parse_ingenuity_image_thumbnail_returns_none_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    assert ing.parse_ingenuity_image(_quirk("SYN_ING_THUMBNAIL")) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_parse_ingenuity_image_numeric_site_and_drive_are_nulled_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    record = ing.parse_ingenuity_image(_quirk("SYN_ING_STOP"))

    assert record is not None
    assert (record.site, record.drive) == (None, None)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_parse_ingenuity_image_quirks_are_normalized() -> None:
    record = ing.parse_ingenuity_image(_quirk("SYN_ING_QUIRKS"))

    assert record is not None
    assert record.sol == 1069  # "1069"
    assert record.camera_code == "HELI_NAV"  # "heli_nav"
    assert record.taken_at == datetime(2024, 2, 22, 23, 59, 59, 900000, tzinfo=UTC)  # naive
    assert record.title == "Synthetic helicopter image"  # stripped
    assert record.caption is None  # ""
    assert "date_taken_mars" not in record.extra  # "UNK" is not stored


def test_parse_ingenuity_image_missing_full_res_returns_none_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    raw = _recorded_by_id(NAV_ID)
    del raw["image_files"]["full_res"]

    assert ing.parse_ingenuity_image(raw) is None
    assert any(NAV_ID in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


# --- IngenuityAdapter requests ---------------------------------------------------------------


async def test_latest_sol_requests_category_ingenuity(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    body = load_fixture(RECORDED)
    body["images"][0]["sol"] = 1090
    route = upstream_router.get(M2020_URL).respond(200, json=body)
    adapter = make_adapter()

    assert adapter.rover_id == "ingenuity"
    assert await adapter.latest_sol() == 1090
    assert query(route.calls.last.request) == {
        "feed": "raw_images",
        "category": "ingenuity",
        "feedtype": "json",
        "latest": "true",
    }


async def test_fetch_sols_requests_category_ingenuity_with_sol_range_and_parses_records(
    upstream_router: respx.MockRouter,
    make_adapter: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    page = load_fixture(RECORDED)
    page["total_results"] = len(page["images"])
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder({0: page}))
    adapter = make_adapter()

    result = [item async for item in adapter.fetch_sols(1069, 1069)]

    assert [query(call.request) for call in route.calls] == [
        {
            "feed": "raw_images",
            "category": "ingenuity",
            "feedtype": "json",
            "order": "sol desc",
            "num": "100",
            "page": "0",
            "condition_2": "1069:sol:gte",
            "condition_3": "1069:sol:lte",
        }
    ]
    assert [sol for sol, _ in result] == [1069]
    records = result[0][1]
    assert [r.source_id for r in records] == [img["imageid"] for img in page["images"]]
    assert {r.camera_code for r in records} == {"HELI_NAV", "HELI_RTE"}
    assert all(r.site is None and r.drive is None for r in records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_fetch_sols_drops_thumbnails_of_the_helicopter_feed(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    page = load_fixture(RECORDED)
    page["images"] = [page["images"][0], _quirk("SYN_ING_THUMBNAIL")]
    page["total_results"] = 2
    upstream_router.get(M2020_URL).mock(side_effect=paged_responder({0: page}))
    adapter = make_adapter()

    result = [item async for item in adapter.fetch_sols(1069, 1069)]

    assert [r.source_id for r in result[0][1]] == [NAV_ID]


async def test_fetch_sols_empty_result_yields_nothing(
    upstream_router: respx.MockRouter, make_adapter: Callable[..., Any]
) -> None:
    page = {**load_fixture(RECORDED), "images": [], "total_results": 0}
    upstream_router.get(M2020_URL).mock(side_effect=paged_responder({0: page}))
    adapter = make_adapter()

    assert [item async for item in adapter.fetch_sols(1000, 1000)] == []
