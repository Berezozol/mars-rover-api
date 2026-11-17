"""GET /rovers/{rover}/photos: sol, earth_date, camera, only full photos, order, img_src.

docs/LEGACY-API.md, "photos: параметры и правила", "earth_date", "Камеры" and the
deviations table.
"""

import httpx
import pytest

from tests.api.legacy.conftest import (
    CURIOSITY_SOL1_ORDER,
    PHOTO_CAMERA_KEYS,
    PHOTO_KEYS,
    ROVER_KEYS,
    World,
)

pytestmark = pytest.mark.api


async def get_photos(client: httpx.AsyncClient, url: str, **params: str) -> list[dict]:
    response = await client.get(url, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["photos"]
    return body["photos"]


# --- sol / earth_date presence -------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{}, {"camera": "fhaz"}, {"page": "1"}, {"api_key": "DEMO_KEY"}],
    ids=["no-params", "camera-only", "page-only", "api-key-only"],
)
async def test_photos_without_sol_and_earth_date_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, params: dict[str, str]
) -> None:
    response = await client.get(f"{prefix}/rovers/curiosity/photos", params=params)

    assert response.status_code == 200
    assert response.json() == {"photos": []}


async def test_photos_by_sol_returns_exact_photos_in_camera_then_id_order(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="1")

    assert photos == world.photos(CURIOSITY_SOL1_ORDER)


async def test_photos_object_keys_in_legacy_order(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photo = (await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="1"))[0]

    assert list(photo) == PHOTO_KEYS
    assert list(photo["camera"]) == PHOTO_CAMERA_KEYS
    assert list(photo["rover"]) == ROVER_KEYS
    assert photo["camera"]["rover_id"] == photo["rover"]["id"] == 5


async def test_photos_same_camera_ordered_by_id(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="30")

    assert photos == world.photos(["c30-nav-a", "c30-nav-b"])


@pytest.mark.parametrize(
    ("sol", "expected_date"),
    [("1", "2012-08-07"), ("30", "2012-09-05"), ("100", "2012-11-16"), ("829", "2014-12-05")],
)
async def test_photos_earth_date_is_legacy_date_from_sol_not_capture_date(
    client: httpx.AsyncClient, prefix: str, world: World, sol: str, expected_date: str
) -> None:
    # Seeded photos.earth_date is 2020-01-01 for all of them.
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol=sol)

    assert photos
    assert {photo["earth_date"] for photo in photos} == {expected_date}


@pytest.mark.parametrize("sol", ["-1", "-100"])
async def test_photos_negative_sol_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, sol: str
) -> None:
    assert await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol=sol) == []


async def test_photos_sol_without_photos_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    assert await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="2") == []


@pytest.mark.parametrize("sol", ["abc", "1.5", "1e3", "one"])
async def test_photos_non_integer_sol_returns_400_invalid_sol(
    client: httpx.AsyncClient, prefix: str, world: World, sol: str
) -> None:
    response = await client.get(f"{prefix}/rovers/curiosity/photos", params={"sol": sol})

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid sol"}


# --- earth_date --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("earth_date", "sol"),
    [
        ("2012-08-07", "1"),
        ("2012-8-7", "1"),
        ("2012-08-7", "1"),
        ("2012-8-07", "1"),
        ("2012-09-05", "30"),
        ("2012-9-5", "30"),
        ("2012-11-16", "100"),
        ("2014-12-5", "829"),
    ],
)
async def test_photos_earth_date_with_or_without_leading_zeros_equals_sol_result(
    client: httpx.AsyncClient, prefix: str, world: World, earth_date: str, sol: str
) -> None:
    url = f"{prefix}/rovers/curiosity/photos"
    by_date = await get_photos(client, url, earth_date=earth_date)
    by_sol = await get_photos(client, url, sol=sol)

    assert by_date
    assert by_date == by_sol


async def test_photos_earth_date_before_landing_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    url = f"{prefix}/rovers/curiosity/photos"
    assert await get_photos(client, url, earth_date="2000-01-01") == []


@pytest.mark.parametrize(
    "earth_date",
    ["abc", "2012-13-01", "2012-02-30", "2012/08/07", "20120807", "2012-08", "2012-08-07x"],
    ids=["text", "month-13", "feb-30", "slashes", "no-dashes", "no-day", "trailing"],
)
async def test_photos_unparsable_earth_date_returns_400_invalid_earth_date(
    client: httpx.AsyncClient, prefix: str, world: World, earth_date: str
) -> None:
    response = await client.get(
        f"{prefix}/rovers/curiosity/photos", params={"earth_date": earth_date}
    )

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid earth_date"}


# --- sol priority -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "earth_date", ["2012-09-05", "not-a-date"], ids=["other-valid-date", "invalid-date"]
)
async def test_photos_sol_has_priority_over_earth_date(
    client: httpx.AsyncClient, prefix: str, world: World, earth_date: str
) -> None:
    photos = await get_photos(
        client, f"{prefix}/rovers/curiosity/photos", sol="1", earth_date=earth_date
    )

    assert photos == world.photos(CURIOSITY_SOL1_ORDER)


# --- only full photos ---------------------------------------------------------------------


async def test_photos_hidden_sample_types_never_listed(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    url = f"{prefix}/rovers/curiosity/photos"
    sol1_ids = {photo["id"] for photo in await get_photos(client, url, sol="1")}
    sol829 = await get_photos(client, url, sol="829")

    assert world.photo_ids["c1-fhaz-lb-sub"] not in sol1_ids
    assert world.photo_ids["c1-mast-l-down"] not in sol1_ids
    assert sol829 == world.photos(["c829-mast-l"])
    assert await get_photos(client, url, sol="900") == []


# --- camera filter --------------------------------------------------------------------------


@pytest.mark.parametrize("camera", ["MAHLI", "mahli", "Mahli"])
async def test_photos_camera_filter_is_case_insensitive(
    client: httpx.AsyncClient, prefix: str, world: World, camera: str
) -> None:
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="1", camera=camera)

    assert photos == world.photos(["c1-mahli"])


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ("fhaz", ["c1-fhaz", "c1-fhaz-lb", "c1-fhaz-rb"]),
        ("RHAZ", ["c1-rhaz-lb"]),
        ("navcam", ["c1-nav-rb", "c1-nav-lb"]),
        ("mast", ["c1-mast-r", "c1-mast-l"]),
        ("CHEMCAM", ["c1-chem"]),
        ("mast_left", ["c1-mast-l"]),
        ("FHAZ_LEFT_B", ["c1-fhaz-lb"]),
        ("NAV_RIGHT_B", ["c1-nav-rb"]),
        ("MAHLI", ["c1-mahli"]),
        ("mardi", ["c1-mardi"]),
    ],
    ids=[
        "FHAZ-group",
        "RHAZ-group",
        "NAVCAM-group",
        "MAST-group",
        "CHEMCAM-group",
        "MAST_LEFT-exact",
        "FHAZ_LEFT_B-exact",
        "NAV_RIGHT_B-exact",
        "MAHLI-exact",
        "MARDI-exact",
    ],
)
async def test_photos_curiosity_camera_groups_select_expected_codes(
    client: httpx.AsyncClient, prefix: str, world: World, camera: str, expected: list[str]
) -> None:
    # Expected lists are in v1 order (camera sort_order, then id); hidden photos never appear.
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="1", camera=camera)

    assert photos == world.photos(expected)


@pytest.mark.parametrize("camera", ["PANCAM", "xyz", "NAV", "FHAZ_"])
async def test_photos_unknown_camera_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, camera: str
) -> None:
    url = f"{prefix}/rovers/curiosity/photos"
    assert await get_photos(client, url, sol="1", camera=camera) == []


async def test_photos_camera_with_earth_date_filters_too(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photos = await get_photos(
        client, f"{prefix}/rovers/curiosity/photos", earth_date="2012-8-7", camera="mast"
    )

    assert photos == world.photos(["c1-mast-r", "c1-mast-l"])


@pytest.mark.parametrize(
    ("camera", "expected"),
    [("NAVCAM", []), ("navcam_left", ["p-left"]), ("NAVCAM_RIGHT", ["p-right"])],
    ids=["short-code-no-group", "exact-lower", "exact-upper"],
)
async def test_photos_perseverance_camera_is_exact_match(
    client: httpx.AsyncClient, prefix: str, world: World, camera: str, expected: list[str]
) -> None:
    photos = await get_photos(
        client, f"{prefix}/rovers/perseverance/photos", sol="100", camera=camera
    )

    assert photos == world.photos(expected)


async def test_photos_spirit_fhaz_does_not_group(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    # Spirit has a (synthetic) FHAZ_LEFT_B photo; FHAZ must still select FHAZ only.
    url = f"{prefix}/rovers/spirit/photos"
    assert await get_photos(client, url, sol="5", camera="fhaz") == world.photos(["s-fhaz"])
    assert await get_photos(client, url, sol="5") == world.photos(["s-fhaz", "s-fhaz-lb"])


# --- img_src ------------------------------------------------------------------------------


async def test_photos_perseverance_img_src_is_large_or_full_fallback(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photos = await get_photos(client, f"{prefix}/rovers/perseverance/photos", sol="100")

    assert [photo["img_src"] for photo in photos] == [
        "https://images.example.test/p-left_1200.jpg",
        "https://images.example.test/p-right.png",
    ]
    assert photos == world.photos(["p-left", "p-right"])


async def test_photos_curiosity_img_src_is_full_even_with_large(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photos = await get_photos(client, f"{prefix}/rovers/curiosity/photos", sol="1", camera="mahli")

    assert [photo["img_src"] for photo in photos] == ["https://images.example.test/c1-mahli.png"]


# --- api_key --------------------------------------------------------------------------------


async def test_photos_api_key_is_ignored(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    url = f"{prefix}/rovers/curiosity/photos"
    with_key = await get_photos(client, url, sol="1", camera="fhaz", api_key="DEMO_KEY")

    assert with_key == await get_photos(client, url, sol="1", camera="fhaz")
    assert with_key == world.photos(["c1-fhaz", "c1-fhaz-lb", "c1-fhaz-rb"])
