"""API v2 for Spirit and Opportunity after the PDS import (stage 8).

Contract: docs/API.md - Photo of Spirit/Opportunity (stage 9a): ``images.small`` is the PDS
browse JPEG (preview), ``images.full`` (= ``img_src``) is our conversion
``{origin}/api/v2/photos/{id}/image?size=full``, ``medium``/``large`` and ``title`` are null,
``source.link`` is the PDS ``.img`` product; filters ``camera`` and ``sample_type`` as for
every rover; ``size=small`` of the image endpoint redirects to the preview, other sizes are
the conversion (tests/api/v2/test_mer_image.py); ``status: complete``; admin sync of
Spirit/Opportunity stays ``422`` (no source to sync, one-time import only).
Data: ``mer_world`` (tests/api/_mer.py).
"""

from typing import Any

import httpx
import pytest

from tests.api._mer import MerWorld, mer_world  # noqa: F401 (fixture)
from tests.api.v2.conftest import V2, assert_validation_error, iso_z
from tests.unit._mer import (
    FRAMES_TOTAL,
    MAX_PHOTO_SOL,
    SPIRIT_MERGED_KEPT,
    SPIRIT_MERGED_PRODUCTS,
)

pytestmark = pytest.mark.api

ROVERS = ["spirit", "opportunity"]


@pytest.fixture
def world(mer_world: MerWorld) -> MerWorld:  # noqa: F811
    return mer_world


async def get(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    response = await client.get(f"{V2}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("rover", ROVERS)
async def test_mer_rover_complete_with_counters(
    client: httpx.AsyncClient, world: MerWorld, rover: str
) -> None:
    body = await get(client, f"/rovers/{rover}")

    assert body["id"] == rover
    assert body["mission"] == "mer"
    assert body["status"] == "complete"
    assert body["max_sol"] == MAX_PHOTO_SOL[rover]
    assert body["total_photos"] == FRAMES_TOTAL[rover]
    assert body["cameras_count"] == 7  # 6 seed cameras + MI
    assert body["last_synced_at"] is not None


async def test_mer_photo_list_item_shape(client: httpx.AsyncClient, world: MerWorld) -> None:
    body = await get(client, "/rovers/spirit/photos", sol=386)
    [item] = body["items"]
    frame = world.frame("2N160633049EDNA2K1F0006R0M1")
    photo_id = world.photo_ids[frame.source_id]
    full = f"http://test/api/v2/photos/{photo_id}/image?size=full"

    assert body["total"] == 1
    assert item == {
        "id": photo_id,
        "rover": "spirit",
        "sol": 386,
        "earth_date": "2005-02-02",
        "taken_at": iso_z(frame.taken_at),
        "camera": {"code": "NAVCAM", "name": "Navigation Camera"},
        "sample_type": "downsampled",
        "title": None,
        "img_src": full,
        "images": {"small": frame.img_full, "medium": None, "large": None, "full": full},
        "credit": "NASA/JPL-Caltech",
        "source": {"id": frame.source_id, "link": frame.source_link},
        # Size of the PDS product from the index columns LINE_SAMPLES x LINES.
        "width": int(frame.row["LINE_SAMPLES"]),
        "height": int(frame.row["LINES"]),
        "local_time": {
            "time": frame.row["LOCAL_TRUE_SOLAR_TIME"].strip()[:8],
            "kind": "ltst",
            "planet": "mars",
        },
    }
    assert list(item)[-3:] == ["width", "height", "local_time"]


@pytest.mark.parametrize("rover", ROVERS)
async def test_mer_rover_end_date_and_archive_source(
    client: httpx.AsyncClient, world: MerWorld, rover: str
) -> None:
    body = await get(client, f"/rovers/{rover}")

    assert body["end_date"] == {"spirit": "2011-05-25", "opportunity": "2019-02-13"}[rover]
    assert body["data_source"] == "archive"


async def test_mer_photo_detail_local_time_is_true_solar_time(
    client: httpx.AsyncClient, world: MerWorld
) -> None:
    frame = world.frame("2N160633049EDNA2K1F0006R0M1")

    body = await get(client, f"/photos/{world.photo_ids[frame.source_id]}")

    assert body["local_time"]["kind"] == "ltst"
    assert body["local_time"]["time"] == frame.row["LOCAL_TRUE_SOLAR_TIME"].strip()[:8]
    assert (body["width"], body["height"]) == (
        int(frame.row["LINE_SAMPLES"]),
        int(frame.row["LINES"]),
    )
    # The only photo of sol 386 of Spirit.
    assert body["in_sol"] == {"position": 1, "total": 1, "newer_id": None, "older_id": None}


async def test_mer_photo_detail_extra_and_source(
    client: httpx.AsyncClient, world: MerWorld
) -> None:
    frame = world.frame(SPIRIT_MERGED_KEPT)

    body = await get(client, f"/photos/{world.photo_ids[SPIRIT_MERGED_KEPT]}")

    assert body["sample_type"] == "subframe"
    assert body["camera"] == {"code": "PANCAM", "name": "Panoramic Camera"}
    assert body["credit"] == "NASA/JPL-Caltech/Cornell"
    assert body["source"] == {"id": SPIRIT_MERGED_KEPT, "link": frame.source_link}
    assert body["source"]["link"].endswith(".img")
    assert body["received_at"] == iso_z(frame.received_at)
    assert (body["site"], body["drive"]) == (frame.site, frame.drive)
    assert body["caption"] is None
    assert body["extra"]["eye"] == "R"
    assert body["extra"]["instrument_id"] == "PANCAM_RIGHT"
    assert sorted(body["extra"]["products"]) == sorted(SPIRIT_MERGED_PRODUCTS)


@pytest.mark.parametrize(
    ("rover", "camera", "sample_type"),
    [
        ("spirit", "NAVCAM", "downsampled"),
        ("spirit", "navcam", "subframe"),
        ("spirit", "PANCAM", "full"),
        ("opportunity", "MI", None),
        ("opportunity", "ENTRY", "downsampled"),
        ("opportunity", "RHAZ", None),
        ("spirit", None, "full"),
    ],
)
async def test_mer_photos_filters_camera_and_sample_type(
    client: httpx.AsyncClient,
    world: MerWorld,
    rover: str,
    camera: str | None,
    sample_type: str | None,
) -> None:
    params = {"per_page": 100}
    if camera:
        params["camera"] = camera
    if sample_type:
        params["sample_type"] = sample_type
    body = await get(client, f"/rovers/{rover}/photos", **params)

    expected = [
        f
        for f in world.frames[rover]
        if camera in (None, f.camera_code.lower(), f.camera_code)
        and sample_type in (None, f.sample_type)
    ]
    expected.sort(key=lambda f: (f.sol, f.taken_at, world.photo_ids[f.source_id]), reverse=True)
    assert body["total"] == len(expected) > 0
    assert [item["id"] for item in body["items"]] == [
        world.photo_ids[f.source_id] for f in expected
    ]


async def test_mer_photos_camera_minites_is_known_but_empty(
    client: httpx.AsyncClient, world: MerWorld
) -> None:
    body = await get(client, "/rovers/spirit/photos", camera="MINITES")

    assert body["items"] == [] and body["total"] == 0


async def test_mer_photos_unknown_camera_is_422(client: httpx.AsyncClient, world: MerWorld) -> None:
    response = await client.get(f"{V2}/rovers/spirit/photos", params={"camera": "MAHLI"})

    details = assert_validation_error(response, "camera")
    assert "MI" in details[0]["message"]


async def test_mer_photos_by_real_earth_date(client: httpx.AsyncClient, world: MerWorld) -> None:
    """v2 earth_date is the UTC capture date: Spirit's descent frames are 2004-01-04."""
    body = await get(client, "/rovers/spirit/photos", earth_date="2004-01-04")

    assert {item["sol"] for item in body["items"]} == {1}
    assert body["total"] == 3


@pytest.mark.parametrize("rover", ROVERS)
async def test_mer_latest_photos(client: httpx.AsyncClient, world: MerWorld, rover: str) -> None:
    body = await get(client, f"/rovers/{rover}/photos/latest")

    assert body["sol"] == MAX_PHOTO_SOL[rover]
    assert body["total"] == len(world.sol_frames(rover, MAX_PHOTO_SOL[rover]))


async def test_mer_image_small_redirects_to_the_browse_jpeg(
    client: httpx.AsyncClient, world: MerWorld
) -> None:
    source_id = "1M192379108EFF64KWP2956M2M1"

    response = await client.get(
        f"{V2}/photos/{world.photo_ids[source_id]}/image", params={"size": "small"}
    )

    assert response.status_code == 302
    assert response.headers["location"] == world.frame(source_id).img_full
    assert response.headers["location"].endswith(".img.jpg")


@pytest.mark.parametrize("rover", ROVERS)
async def test_mer_photos_have_conversion_links_in_every_listing(
    client: httpx.AsyncClient, world: MerWorld, rover: str
) -> None:
    """Photo of every endpoint: full = img_src = our conversion, small = the PDS preview."""
    listing = (await get(client, f"/rovers/{rover}/photos", per_page=100))["items"]
    latest = (await get(client, f"/rovers/{rover}/photos/latest"))["items"]
    frames = {world.photo_ids[f.source_id]: f for f in world.frames[rover]}

    assert listing and latest
    for item in listing + latest:
        link = f"http://test/api/v2/photos/{item['id']}/image?size=full"
        assert item["img_src"] == link
        assert item["images"] == {
            "small": frames[item["id"]].img_full,
            "medium": None,
            "large": None,
            "full": link,
        }


@pytest.mark.parametrize("rover", ROVERS)
async def test_mer_manifest_totals(client: httpx.AsyncClient, world: MerWorld, rover: str) -> None:
    body = await get(client, f"/rovers/{rover}/manifest")

    assert sum(entry["total_photos"] for entry in body["sols"]) == FRAMES_TOTAL[rover]
    assert body["sols"][0]["sol"] == MAX_PHOTO_SOL[rover]


async def test_mer_cameras_include_mi_and_empty_minites(
    client: httpx.AsyncClient, world: MerWorld
) -> None:
    body = await get(client, "/rovers/opportunity/cameras")
    totals = {item["code"]: item["total_photos"] for item in body["items"]}

    assert totals == {
        "ENTRY": 3,
        "FHAZ": 10,
        "MI": 9,
        "MINITES": 0,
        "NAVCAM": 11,
        "PANCAM": 13,
        "RHAZ": 1,
    }
