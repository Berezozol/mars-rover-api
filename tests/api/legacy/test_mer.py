"""API v1 for Spirit and Opportunity after the PDS import (stage 8).

Contract: docs/LEGACY-API.md, rows "Spirit/Opportunity" of "Где мы сознательно
отличаемся": img_src = browse JPEG of the PDS archive; one photo per frame and in v1 ALL
frames (full, subframe, downsampled) - photos, latest_photos, /photos/{id}, manifest and
the rover counters; MINITES has no photos (camera=minites -> []); MI exists. Curiosity and
Perseverance keep the full-frame-only rule (regression). earth_date is the legacy one
(from the sol): Spirit 1 -> 2004-01-05, Opportunity 1 -> 2004-01-26.

Data: ``mer_world`` (tests/api/_mer.py) - both rovers imported with ``import-mer`` from the
real index fixtures.
"""

from datetime import date
from typing import Any

import httpx
import pytest

from app.core.legacy_dates import legacy_earth_date
from tests.api._mer import OTHER_SOL, MerWorld, mer_world  # noqa: F401 (fixture)
from tests.api.legacy.conftest import (
    MANIFEST_KEYS,
    PHOTO_KEYS,
    ROVER_KEYS,
    ROVERS,
)
from tests.unit._mer import (
    FRAMES_TOTAL,
    MAX_PHOTO_SOL,
    SPIRIT_MERGED_KEPT,
    SPIRIT_MERGED_SOL,
)

pytestmark = pytest.mark.api

LANDING = {"spirit": date(2004, 1, 4), "opportunity": date(2004, 1, 25)}
MER_CAMERAS = [
    ("FHAZ", "Front Hazard Avoidance Camera"),
    ("RHAZ", "Rear Hazard Avoidance Camera"),
    ("NAVCAM", "Navigation Camera"),
    ("PANCAM", "Panoramic Camera"),
    ("MINITES", "Miniature Thermal Emission Spectrometer (Mini-TES)"),
    ("ENTRY", "Entry, Descent, and Landing Camera"),
    ("MI", "Microscopic Imager"),
]
SORT = {code: n for n, (code, _) in enumerate(MER_CAMERAS)}
NAMES = dict(MER_CAMERAS)


@pytest.fixture
def world(mer_world: MerWorld) -> MerWorld:  # noqa: F811
    return mer_world


def rover_json(rover: str) -> dict[str, Any]:
    max_sol = MAX_PHOTO_SOL[rover]
    return {
        **ROVERS[rover],
        "max_sol": max_sol,
        "max_date": legacy_earth_date(LANDING[rover], max_sol).isoformat(),
        "total_photos": FRAMES_TOTAL[rover],
        "cameras": [{"name": code, "full_name": name} for code, name in MER_CAMERAS],
    }


def photo_json(world: MerWorld, rover: str, source_id: str) -> dict[str, Any]:
    frame = world.frame(source_id)
    return {
        "id": world.photo_ids[source_id],
        "sol": frame.sol,
        "camera": {
            "id": world.camera_ids[(rover, frame.camera_code)],
            "name": frame.camera_code,
            "rover_id": ROVERS[rover]["id"],
            "full_name": NAMES[frame.camera_code],
        },
        "img_src": frame.img_full,
        "earth_date": legacy_earth_date(LANDING[rover], frame.sol).isoformat(),
        "rover": rover_json(rover),
    }


def expected_sol(world: MerWorld, rover: str, sol: int, camera: str | None = None) -> list:
    frames = [f for f in world.sol_frames(rover, sol) if camera in (None, f.camera_code)]
    frames.sort(key=lambda f: (SORT[f.camera_code], world.photo_ids[f.source_id]))
    return [photo_json(world, rover, f.source_id) for f in frames]


async def get(client: httpx.AsyncClient, prefix: str, path: str, **params: Any) -> Any:
    response = await client.get(f"{prefix}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- photos ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rover", "earth_date"), [("spirit", "2004-01-05"), ("opportunity", "2004-01-26")]
)
async def test_mer_photos_sol_1_returns_descent_frames_exact_json(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, earth_date: str
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}/photos", sol=1)

    assert list(body) == ["photos"]
    expected = expected_sol(world, rover, 1)
    assert len(expected) == 3
    assert body["photos"] == expected
    for photo in body["photos"]:
        assert list(photo) == PHOTO_KEYS
        assert list(photo["rover"]) == ROVER_KEYS
        assert photo["camera"]["name"] == "ENTRY"
        assert photo["earth_date"] == earth_date
        assert photo["img_src"].endswith(".img.jpg")
        assert "/browse/sol0001/edr/" in photo["img_src"]


@pytest.mark.parametrize(
    ("rover", "earth_date"),
    [("spirit", "2004-01-05"), ("opportunity", "2004-01-26"), ("opportunity", "2004-1-26")],
)
async def test_mer_photos_by_legacy_earth_date_maps_to_sol_1(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, earth_date: str
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}/photos", earth_date=earth_date)

    assert body["photos"] == expected_sol(world, rover, 1)


async def test_mer_photos_show_every_frame_type(
    client: httpx.AsyncClient, world: MerWorld, prefix: str
) -> None:
    """Sol 296: the only frame is a Pancam subframe (ESF over EDN) - visible in v1."""
    body = await get(client, prefix, "/rovers/spirit/photos", sol=SPIRIT_MERGED_SOL)

    assert [p["id"] for p in body["photos"]] == [world.photo_ids[SPIRIT_MERGED_KEPT]]
    assert body["photos"] == expected_sol(world, "spirit", SPIRIT_MERGED_SOL)


@pytest.mark.parametrize(
    ("rover", "sol"),
    [("spirit", 483), ("spirit", 876), ("opportunity", 980), ("opportunity", 5000)],
)
async def test_mer_photos_sol_ordered_by_camera_then_id(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, sol: int
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}/photos", sol=sol)

    assert body["photos"] == expected_sol(world, rover, sol)
    assert len(body["photos"]) == 2


@pytest.mark.parametrize(
    ("rover", "sol", "camera", "code"),
    [
        ("spirit", 51, "fhaz", "FHAZ"),
        ("spirit", 1898, "RHaz", "RHAZ"),
        ("spirit", 483, "navcam", "NAVCAM"),
        ("spirit", 483, "PANCAM", "PANCAM"),
        ("opportunity", 980, "mi", "MI"),
        ("opportunity", 1, "entry", "ENTRY"),
    ],
)
async def test_mer_photos_camera_filter_case_insensitive(
    client: httpx.AsyncClient,
    world: MerWorld,
    prefix: str,
    rover: str,
    sol: int,
    camera: str,
    code: str,
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}/photos", sol=sol, camera=camera)

    assert body["photos"] == expected_sol(world, rover, sol, code)
    assert body["photos"]


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
@pytest.mark.parametrize("camera", ["minites", "MINITES"])
async def test_mer_photos_camera_minites_returns_empty_list(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, camera: str
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}/photos", sol=1, camera=camera)

    assert body == {"photos": []}


async def test_mer_photos_curiosity_group_codes_do_not_apply(
    client: httpx.AsyncClient, world: MerWorld, prefix: str
) -> None:
    body = await get(client, prefix, "/rovers/spirit/photos", sol=51, camera="fhaz_left_b")

    assert body == {"photos": []}


# --- latest_photos -----------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_mer_latest_photos_use_highest_sol_of_any_frame(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str
) -> None:
    """Opportunity sol 5000 has only downsampled MI frames: still the latest sol in v1."""
    body = await get(client, prefix, f"/rovers/{rover}/latest_photos")

    assert list(body) == ["latest_photos"]
    assert body["latest_photos"] == expected_sol(world, rover, MAX_PHOTO_SOL[rover])
    assert body["latest_photos"]


# --- /photos/{id} -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rover", "source_id"),
    [
        ("spirit", SPIRIT_MERGED_KEPT),  # subframe
        ("spirit", "2N160633049EDNA2K1F0006R0M1"),  # downsampled
        ("opportunity", "1F283102995EFF94B2P1124R0M1"),  # full
    ],
)
async def test_mer_photo_by_id_any_frame_type_returns_photo(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, source_id: str
) -> None:
    body = await get(client, prefix, f"/photos/{world.photo_ids[source_id]}")

    assert body == {"photo": photo_json(world, rover, source_id)}


@pytest.mark.parametrize("source_id", ["o-c-sub", "o-c-down", "o-p-sub", "o-p-thumb"])
async def test_other_rovers_photo_by_id_not_full_still_404(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, source_id: str
) -> None:
    response = await client.get(f"{prefix}/photos/{world.photo_ids[source_id]}")

    assert response.status_code == 404
    assert response.json() == {"errors": "Photo not found"}


# --- rovers and manifests ----------------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "Spirit", "OPPORTUNITY"])
async def test_mer_rover_counters_cover_all_frames(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str
) -> None:
    body = await get(client, prefix, f"/rovers/{rover}")

    assert body == {"rover": rover_json(rover.lower())}


async def test_mer_rovers_list_has_mer_counters(
    client: httpx.AsyncClient, world: MerWorld, prefix: str
) -> None:
    body = await get(client, prefix, "/rovers")
    by_name = {rover["name"]: rover for rover in body["rovers"]}

    assert by_name["Spirit"] == rover_json("spirit")
    assert by_name["Opportunity"] == rover_json("opportunity")


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_mer_manifest_lists_every_sol_with_frames(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str
) -> None:
    body = await get(client, prefix, f"/manifests/{rover}")
    manifest = body["photo_manifest"]

    assert list(body) == ["photo_manifest"]
    assert list(manifest) == MANIFEST_KEYS
    head = {k: v for k, v in rover_json(rover).items() if k not in ("id", "cameras")}
    assert {k: manifest[k] for k in head} == head
    expected = []
    for sol in sorted({f.sol for f in world.frames[rover]}):
        frames = world.sol_frames(rover, sol)
        expected.append(
            {
                "sol": sol,
                "earth_date": legacy_earth_date(LANDING[rover], sol).isoformat(),
                "total_photos": len(frames),
                "cameras": sorted({f.camera_code for f in frames}),
            }
        )
    got = [{**entry, "cameras": sorted(entry["cameras"])} for entry in manifest["photos"]]
    assert got == expected
    assert sum(entry["total_photos"] for entry in manifest["photos"]) == FRAMES_TOTAL[rover]


async def test_mer_manifest_sol_1_spirit(
    client: httpx.AsyncClient, world: MerWorld, prefix: str
) -> None:
    body = await get(client, prefix, "/manifests/spirit")

    assert body["photo_manifest"]["photos"][0] == {
        "sol": 1,
        "earth_date": "2004-01-05",
        "total_photos": 3,
        "cameras": ["ENTRY"],
    }


# --- regression: other rovers stay full-frame only ----------------------------------------


@pytest.mark.parametrize(
    ("rover", "visible"), [("curiosity", "o-c-full"), ("perseverance", "o-p-full")]
)
async def test_other_rovers_photos_stay_full_only(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str, visible: str
) -> None:
    photos = (await get(client, prefix, f"/rovers/{rover}/photos", sol=OTHER_SOL))["photos"]
    latest = (await get(client, prefix, f"/rovers/{rover}/latest_photos"))["latest_photos"]
    detail = (await get(client, prefix, f"/rovers/{rover}"))["rover"]
    manifest = (await get(client, prefix, f"/manifests/{rover}"))["photo_manifest"]

    assert [p["id"] for p in photos] == [world.photo_ids[visible]]
    assert [p["id"] for p in latest] == [world.photo_ids[visible]]
    assert detail["total_photos"] == 1
    assert manifest["total_photos"] == 1
    assert [entry["total_photos"] for entry in manifest["photos"]] == [1]


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_mer_img_src_stays_the_pds_preview_in_v1_after_stage_9a(
    client: httpx.AsyncClient, world: MerWorld, prefix: str, rover: str
) -> None:
    """Stage 9a changes v2 only: v1 img_src of MER is still the stored PDS browse JPEG."""
    sol = MAX_PHOTO_SOL[rover]
    listing = (await get(client, prefix, f"/rovers/{rover}/photos", sol=sol))["photos"]
    latest = (await get(client, prefix, f"/rovers/{rover}/latest_photos"))["latest_photos"]
    frames = {world.photo_ids[f.source_id]: f for f in world.frames[rover]}

    assert listing and latest
    for photo in listing + latest:
        assert photo["img_src"] == frames[photo["id"]].img_full
        assert photo["img_src"].endswith(".img.jpg")
        assert "/api/v2/" not in photo["img_src"]
    single = (await get(client, prefix, f"/photos/{listing[0]['id']}"))["photo"]
    assert single["img_src"] == frames[listing[0]["id"]].img_full
