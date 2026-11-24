"""AC-7 (docs/proposals/msl-thumbnails.md, docs/API.md "Объекты"): Curiosity `images.small`.

A Mastcam/MAHLI/MARDI photo with a stored thumbnail serves it as `images.small`; `medium` and
`large` stay null, `full` and `img_src` stay the full frame. `/image?size=small` redirects to the
thumbnail, the RSS feed uses it as `media:thumbnail`, and v1 `img_src` is unchanged.
Photos are committed with `upsert_photos` (the sync write path) on a clean database.
"""

import httpx
import pytest

from tests.api.v2._feed_data import (
    ANCHOR,
    MEDIA,
    Factory,
    get_feed,
    items_of,
    photo_ids,
    record,
    seed_batch,
)
from tests.api.v2.conftest import V2

pytestmark = pytest.mark.api

SOL = 5073
FULL = "https://mars.nasa.gov/msl-raw-images/msss/05073/mcam/5073ML1102680061307131C00_DXXX.jpg"
THUMB = "https://mars.nasa.gov/msl-raw-images/msss/05073/mcam/5073ML1102680061307131I01_DXXX.jpg"
BARE_FULL = (
    "https://mars.nasa.gov/msl-raw-images/msss/05073/mcam/5073ML1102670011307122E01_DXXX.jpg"
)
NAV_FULL = (
    "https://mars.nasa.gov/msl-raw-images/proj/msl/redops/ods/surface/sol/05073/opgs/edr/ncam/"
    "NLB_847853270EDR_F1252490NCAM00354M_.JPG"
)


@pytest.fixture
async def ids(clean_db: Factory) -> dict[str, int]:
    """Three Curiosity photos: Mastcam with a thumbnail, Mastcam without, Navcam (no thumbnail)."""
    taken = ANCHOR
    plain = {"img_small": None, "img_medium": None, "img_large": None, "sample_type": "full"}
    await seed_batch(
        clean_db,
        "curiosity",
        [
            record(
                "thumb", SOL, "MAST_LEFT", taken, **{**plain, "img_full": FULL, "img_small": THUMB}
            ),
            record("bare", SOL, "MAST_LEFT", taken, **{**plain, "img_full": BARE_FULL}),
            record("nav", SOL, "NAV_LEFT_B", taken, **{**plain, "img_full": NAV_FULL}),
        ],
        ANCHOR,
    )
    return await photo_ids(clean_db)


async def test_photo_detail_mastcam_with_thumbnail_serves_it_as_images_small_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids['thumb']}")

    assert response.status_code == 200
    body = response.json()
    assert body["images"] == {"small": THUMB, "medium": None, "large": None, "full": FULL}
    assert body["img_src"] == FULL == body["images"]["full"]


@pytest.mark.parametrize("source_id", ["bare", "nav"])
async def test_photo_detail_without_thumbnail_has_null_small_ac7(
    client: httpx.AsyncClient, ids: dict[str, int], source_id: str
) -> None:
    body = (await client.get(f"{V2}/photos/{ids[source_id]}")).json()

    assert body["images"]["small"] is None
    assert body["images"]["medium"] is None
    assert body["images"]["large"] is None
    assert body["img_src"] == body["images"]["full"]


async def test_photo_list_by_sol_and_camera_has_small_only_where_stored_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(
        f"{V2}/rovers/curiosity/photos", params={"sol": SOL, "camera": "MAST_LEFT"}
    )

    assert response.status_code == 200
    by_id = {p["id"]: p for p in response.json()["items"]}
    assert set(by_id) == {ids["thumb"], ids["bare"]}
    assert by_id[ids["thumb"]]["images"]["small"] == THUMB
    assert by_id[ids["thumb"]]["img_src"] == FULL
    assert by_id[ids["bare"]]["images"]["small"] is None


async def test_image_size_small_redirects_to_the_thumbnail_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids['thumb']}/image", params={"size": "small"})

    assert response.status_code == 302
    assert response.headers["location"] == THUMB


async def test_image_size_small_without_thumbnail_falls_back_to_full_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids['bare']}/image", params={"size": "small"})

    assert response.status_code == 302
    assert response.headers["location"] == BARE_FULL


async def test_image_size_full_still_redirects_to_the_full_frame_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids['thumb']}/image", params={"size": "full"})

    assert response.status_code == 302
    assert response.headers["location"] == FULL


async def test_feed_group_photo_uses_the_thumbnail_as_media_thumbnail_ac7(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    items = items_of(await get_feed(client, "curiosity", group="photo", camera="MAST_LEFT"))

    by_guid_text = {}
    for item in items:
        thumbnail = item.find(f"{MEDIA}thumbnail")
        content = item.find(f"{MEDIA}content")
        assert content is not None
        by_guid_text[content.attrib["url"]] = (
            thumbnail.attrib["url"] if thumbnail is not None else None
        )
    assert by_guid_text[FULL] == THUMB
    assert by_guid_text.get(BARE_FULL) in (None, BARE_FULL)  # no thumbnail: never an invented URL


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_img_src_stays_the_full_frame_ac7(
    client: httpx.AsyncClient, ids: dict[str, int], prefix: str
) -> None:
    response = await client.get(f"{prefix}/rovers/curiosity/photos", params={"sol": SOL})

    assert response.status_code == 200
    photos = response.json()["photos"]
    assert {p["img_src"] for p in photos} == {FULL, BARE_FULL, NAV_FULL}
    assert all(THUMB not in str(p) for p in photos)
