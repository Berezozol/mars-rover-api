"""photo_meta for Ingenuity (stage 12, decision A8): the M20 format is read for it too."""

from typing import Any

import pytest

from app.sources.ingenuity import parse_ingenuity_image
from app.sources.photo_meta import LocalTime, frame_size, local_time
from tests.unit._upstream import by_id, load_fixture

pytestmark = pytest.mark.unit

NAV_ID = "HNM_1069_0761837420_372ECM_N0720001HELI00173_0000A0J"
RTE_ID = "HSF_1069_0761837397_369ECM_N0720001HELI00001_000085J"


def test_frame_size_ingenuity_reads_dimension_as_width_height() -> None:
    assert frame_size("ingenuity", {"extended": {"dimension": "(640,480)"}}) == (640, 480)


def test_frame_size_ingenuity_color_camera_dimension() -> None:
    assert frame_size("ingenuity", {"extended": {"dimension": "(4208,3120)"}}) == (4208, 3120)


@pytest.mark.parametrize(
    "extra",
    [{}, {"extended": {}}, {"extended": {"dimension": "UNK"}}],
    ids=["empty", "no-dimension", "unk"],
)
def test_frame_size_ingenuity_without_dimension_is_none(extra: dict[str, Any]) -> None:
    assert frame_size("ingenuity", extra) is None


def test_local_time_ingenuity_reads_lmst_from_date_taken_mars() -> None:
    result = local_time("ingenuity", {"date_taken_mars": "Sol-01069M11:41:52.53212"})

    assert result == LocalTime(time="11:41:52", kind="lmst")


@pytest.mark.parametrize("value", [None, "UNK", "", "garbage"])
def test_local_time_ingenuity_garbage_is_none(value: object) -> None:
    assert local_time("ingenuity", {"date_taken_mars": value}) is None


@pytest.mark.parametrize(
    ("imageid", "size", "time"),
    [(NAV_ID, (640, 480), "11:41:52"), (RTE_ID, (4208, 3120), "11:41:30")],
    ids=["navigation", "color"],
)
def test_recorded_ingenuity_items_give_size_and_local_time_through_extra(
    imageid: str, size: tuple[int, int], time: str
) -> None:
    raw = by_id(load_fixture("ingenuity/list_page0.json")["images"], "imageid", imageid)

    record = parse_ingenuity_image(raw)

    assert record is not None
    assert frame_size("ingenuity", record.extra) == size
    assert local_time("ingenuity", record.extra) == LocalTime(time=time, kind="lmst")
