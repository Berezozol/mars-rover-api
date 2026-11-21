"""GET /api/v2/rovers/{rover}/photos: exact JSON, sort, filters, pagination, 422s."""

import re
from datetime import date

import httpx
import pytest

from app.core.legacy_dates import legacy_earth_date
from tests.api.v2.conftest import (
    CURIOSITY_ORDER,
    HUGE,
    INT32_MAX,
    LIST_KEYS,
    OPPORTUNITY_COUNT,
    OPPORTUNITY_ORDER,
    PERSEVERANCE_ORDER,
    PERSEVERANCE_SOL_2031,
    PHOTO_KEYS,
    V2,
    World,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api

STRICT_INTS = ["+1", "1_0", " 1", "1.0"]


def photos_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/photos"


async def list_ids(client: httpx.AsyncClient, rover: str, **params: str) -> list[int]:
    response = await client.get(photos_url(rover), params=params)
    assert response.status_code == 200, response.text
    return [photo["id"] for photo in response.json()["items"]]


# --- exact JSON and sort -------------------------------------------------------------------


async def test_photos_without_filters_return_exact_json_newest_first(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(photos_url("perseverance"))

    assert response.status_code == 200
    expected = {
        "items": world.photos(PERSEVERANCE_ORDER),
        "page": 1,
        "per_page": 25,
        "total": len(PERSEVERANCE_ORDER),
        "nearest_sols": None,
    }
    assert_json(response.json(), expected)


async def test_photo_object_keys_and_values_match_contract(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(photos_url("perseverance"), params={"sol": "2031"})).json()
    photo = next(item for item in body["items"] if item["source"]["id"] == "p2031-a")

    assert list(photo) == PHOTO_KEYS
    assert list(photo["camera"]) == ["code", "name"]
    assert list(photo["images"]) == ["small", "medium", "large", "full"]
    assert list(photo["source"]) == ["id", "link"]
    assert photo["rover"] == "perseverance"
    assert photo["taken_at"] == "2026-11-06T12:16:25Z"  # .999999 dropped, not rounded
    assert photo["img_src"] == photo["images"]["full"]
    assert photo["camera"] == {"code": "NAVCAM_LEFT", "name": "Navigation Camera - Left"}


async def test_photos_curiosity_have_only_full_image(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(photos_url("curiosity"))).json()

    assert body["items"]
    for photo in body["items"]:
        assert photo["images"]["small"] is None
        assert photo["images"]["medium"] is None
        assert photo["images"]["large"] is None
        assert photo["img_src"] == photo["images"]["full"]
    assert_json(body["items"], world.photos(CURIOSITY_ORDER))


async def test_photos_sort_ties_on_taken_at_broken_by_id_desc(
    client: httpx.AsyncClient, world: World
) -> None:
    ids = await list_ids(client, "perseverance", sol="2031")

    tie1, tie2 = world.photo_ids["p2031-tie1"], world.photo_ids["p2031-tie2"]
    assert tie2 > tie1  # precondition: tie2 was inserted later
    assert ids == world.ids(PERSEVERANCE_SOL_2031)
    assert ids.index(tie2) == ids.index(tie1) - 1


async def test_photos_sort_sol_desc_beats_later_taken_at(
    client: httpx.AsyncClient, world: World
) -> None:
    """p2030-late was taken after every sol-2031 photo but belongs to a lower sol."""
    ids = await list_ids(client, "perseverance")

    assert ids.index(world.photo_ids["p2030-late"]) > ids.index(world.photo_ids["p2031-early"])


async def test_photos_sort_taken_at_beats_id(client: httpx.AsyncClient, world: World) -> None:
    """p2031-early has a greater id than the ties but an earlier taken_at."""
    ids = await list_ids(client, "perseverance", sol="2031")

    assert world.photo_ids["p2031-early"] > world.photo_ids["p2031-tie2"]
    assert ids[-1] == world.photo_ids["p2031-early"]


async def test_photos_rover_without_data_return_empty_list(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(photos_url("spirit"))

    assert response.status_code == 200
    assert_json(
        response.json(),
        {"items": [], "page": 1, "per_page": 25, "total": 0, "nearest_sols": None},
    )


async def test_photos_unknown_params_are_ignored(client: httpx.AsyncClient, world: World) -> None:
    ids = await list_ids(client, "perseverance", foo="bar", api_key="DEMO_KEY", order="asc")

    assert ids == world.ids(PERSEVERANCE_ORDER)


# --- filters -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sol", "expected"),
    [
        ("2031", PERSEVERANCE_SOL_2031),
        ("2030", ["p2030-late", "p2030-sub"]),
        ("2029", []),
        ("0", []),
        (str(INT32_MAX), []),
    ],
    ids=["2031", "2030", "no-photos", "zero", "int32-max"],
)
async def test_photos_filter_sol(
    client: httpx.AsyncClient, world: World, sol: str, expected: list[str]
) -> None:
    response = await client.get(photos_url("perseverance"), params={"sol": sol})

    assert response.status_code == 200
    body = response.json()
    assert_json(body["items"], world.photos(expected))
    assert body["total"] == len(expected)


@pytest.mark.parametrize(
    ("earth_date", "expected"),
    [
        ("2026-11-06", ["p2031-a", "p2031-tie2", "p2031-tie1", "p2031-early"]),
        ("2026-11-07", ["p2031-next-day"]),  # same sol, after UTC midnight
        ("2026-11-08", ["p2030-late"]),
        ("2026-11-05", ["p2030-sub"]),
        ("2026-01-01", []),
    ],
    ids=["sol-2031-day", "after-midnight", "lower-sol-later-day", "legacy-date-of-2031", "none"],
)
async def test_photos_filter_earth_date_uses_real_capture_date(
    client: httpx.AsyncClient, world: World, earth_date: str, expected: list[str]
) -> None:
    response = await client.get(photos_url("perseverance"), params={"earth_date": earth_date})

    assert response.status_code == 200
    body = response.json()
    assert_json(body["items"], world.photos(expected))
    assert body["total"] == len(expected)


async def test_photos_filter_earth_date_ignores_legacy_formula(
    client: httpx.AsyncClient, world: World
) -> None:
    """The v1 date of sol 2031 is 2026-11-05; v2 must not return sol 2031 for it."""
    legacy = legacy_earth_date(date(2021, 2, 18), 2031)
    assert legacy == date(2026, 11, 5)  # precondition: differs from the real dates 11-06/07

    ids = await list_ids(client, "perseverance", earth_date=legacy.isoformat())

    assert ids == world.ids(["p2030-sub"])


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ("FHAZ", ["c20-sub", "c1-fhaz"]),
        ("fhaz", ["c20-sub", "c1-fhaz"]),
        ("FHAZ_LEFT_B", ["c1-fhaz-lb-sub", "c1-fhaz-lb"]),
        ("fhaz_left_b", ["c1-fhaz-lb-sub", "c1-fhaz-lb"]),
        ("Mast_Left", ["c1-down", "c1-mixed"]),
        ("MAST", []),  # known camera without photos; no MAST -> MAST_LEFT grouping
        ("MARDI", []),
    ],
    ids=["exact", "lower", "instrument", "instrument-lower", "mixed-case", "no-group", "empty"],
)
async def test_photos_filter_camera_exact_code_case_insensitive(
    client: httpx.AsyncClient, world: World, camera: str, expected: list[str]
) -> None:
    response = await client.get(photos_url("curiosity"), params={"camera": camera})

    assert response.status_code == 200
    body = response.json()
    assert_json(body["items"], world.photos(expected))
    assert body["total"] == len(expected)


@pytest.mark.parametrize(
    ("sample_type", "expected"),
    [
        ("full", ["c10-nav", "c5-mahli", "c1-fhaz-lb", "c1-fhaz"]),
        ("FULL", ["c10-nav", "c5-mahli", "c1-fhaz-lb", "c1-fhaz"]),
        ("subframe", ["c20-sub", "c1-fhaz-lb-sub"]),
        ("SubFrame", ["c20-sub", "c1-fhaz-lb-sub"]),
        ("downsampled", ["c1-down"]),
        ("Downsampled", ["c1-down"]),
    ],
    ids=["full", "full-upper", "subframe", "subframe-mixed-case", "downsampled", "down-cap"],
)
async def test_photos_filter_sample_type(
    client: httpx.AsyncClient, world: World, sample_type: str, expected: list[str]
) -> None:
    response = await client.get(photos_url("curiosity"), params={"sample_type": sample_type})

    assert response.status_code == 200
    body = response.json()
    assert_json(body["items"], world.photos(expected))
    assert body["total"] == len(expected)


async def test_photos_without_sample_type_include_every_source_type(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(photos_url("curiosity"))).json()

    types = {photo["sample_type"] for photo in body["items"]}
    assert types == {"full", "subframe", "downsampled", "mixed", "chemcam prc"}
    assert body["total"] == len(CURIOSITY_ORDER)


async def test_photos_filters_combine(client: httpx.AsyncClient, world: World) -> None:
    params = {"sol": "1", "camera": "fhaz_left_b", "sample_type": "full"}
    response = await client.get(photos_url("curiosity"), params=params)

    assert response.status_code == 200
    expected = {**_page(world.photos(["c1-fhaz-lb"])), "total": 1, "nearest_sols": None}
    assert_json(response.json(), expected)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"sol": "1", "camera": "fhaz_left_b"}, ["c1-fhaz-lb-sub", "c1-fhaz-lb"]),
        ({"sol": "1", "camera": "MAST_LEFT"}, ["c1-down", "c1-mixed"]),
        ({"sol": "20", "camera": "FHAZ"}, ["c20-sub"]),
        ({"sol": "5", "camera": "FHAZ"}, []),
    ],
    ids=["instrument", "mixed-types", "subframe-only-sol", "none"],
)
async def test_photos_total_exact_with_sol_and_camera(
    client: httpx.AsyncClient, world: World, params: dict[str, str], expected: list[str]
) -> None:
    body = (await client.get(photos_url("curiosity"), params={**params, "per_page": "1"})).json()

    assert body["total"] == len(expected)
    assert [p["id"] for p in body["items"]] == world.ids(expected[:1])


@pytest.mark.parametrize("rover", ["curiosity", "opportunity", "perseverance", "spirit"])
async def test_photos_total_without_filters_equals_rover_total_photos(
    client: httpx.AsyncClient, world: World, rover: str
) -> None:
    rover_body = (await client.get(f"{V2}/rovers/{rover}")).json()
    body = (await client.get(photos_url(rover), params={"per_page": "1"})).json()

    assert body["total"] == rover_body["total_photos"]


@pytest.mark.parametrize(
    ("param", "value"),
    [("sol", "abc"), ("page", "+1"), ("earth_date", "2026-13-01"), ("earth_date", "1700000000")],
    ids=["int-word", "int-sign", "date-month", "date-timestamp"],
)
async def test_photos_validation_message_has_no_pydantic_prefix(
    client: httpx.AsyncClient, world: World, param: str, value: str
) -> None:
    response = await client.get(photos_url("curiosity"), params={param: value})

    details = assert_validation_error(response, param)
    for item in details:
        assert not item["message"].startswith("Value error, "), item


async def test_photos_filter_earth_date_and_camera_combine(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"earth_date": "2026-11-06", "camera": "NAVCAM_RIGHT"}
    ids = await list_ids(client, "perseverance", **params)

    assert ids == world.ids(["p2031-tie2", "p2031-tie1"])


def _page(items: list[dict[str, object]]) -> dict[str, object]:
    return {"items": items, "page": 1, "per_page": 25}


# --- pagination ----------------------------------------------------------------------------


async def test_pagination_defaults_page_1_per_page_25(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(photos_url("opportunity"))

    body = response.json()
    assert list(body) == LIST_KEYS
    assert (body["page"], body["per_page"], body["total"]) == (1, 25, OPPORTUNITY_COUNT)
    assert [p["id"] for p in body["items"]] == world.ids(OPPORTUNITY_ORDER[:25])


async def test_pagination_second_default_page_is_the_rest(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(photos_url("opportunity"), params={"page": "2"})).json()

    assert (body["page"], body["per_page"], body["total"]) == (2, 25, OPPORTUNITY_COUNT)
    assert [p["id"] for p in body["items"]] == world.ids(OPPORTUNITY_ORDER[25:])


@pytest.mark.parametrize(
    ("page", "per_page", "start", "stop"),
    [
        (1, 10, 0, 10),
        (2, 10, 10, 20),
        (3, 10, 20, 30),  # last full page
        (4, 7, 21, 28),
        (5, 7, 28, 30),  # last partial page
        (1, 100, 0, 30),  # max per_page
        (30, 1, 29, 30),  # min per_page, last item
    ],
    ids=["p1", "p2", "last-full", "middle-7", "last-partial", "max-per-page", "per-page-1"],
)
async def test_pagination_pages_slice_the_sorted_list(
    client: httpx.AsyncClient, world: World, page: int, per_page: int, start: int, stop: int
) -> None:
    params = {"page": str(page), "per_page": str(per_page)}
    body = (await client.get(photos_url("opportunity"), params=params)).json()

    assert (body["page"], body["per_page"], body["total"]) == (page, per_page, OPPORTUNITY_COUNT)
    assert_json(body["items"], world.photos(OPPORTUNITY_ORDER[start:stop]))


@pytest.mark.parametrize(
    ("page", "per_page"),
    [("2", "30"), ("4", "10"), ("1000000", "100")],
    ids=["just-beyond", "beyond", "max-page"],
)
async def test_pagination_beyond_end_returns_empty_items_with_total(
    client: httpx.AsyncClient, world: World, page: str, per_page: str
) -> None:
    response = await client.get(
        photos_url("opportunity"), params={"page": page, "per_page": per_page}
    )

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "items": [],
            "page": int(page),
            "per_page": int(per_page),
            "total": OPPORTUNITY_COUNT,
            "nearest_sols": None,  # total > 0: the page is only out of range
        },
    )


async def test_pagination_pages_concatenate_to_the_full_list(
    client: httpx.AsyncClient, world: World
) -> None:
    ids: list[int] = []
    for page in range(1, 5):
        ids += await list_ids(client, "opportunity", page=str(page), per_page="8")

    assert ids == world.ids(OPPORTUNITY_ORDER)


async def test_pagination_total_counts_filtered_photos(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"sample_type": "full", "per_page": "1", "page": "2"}
    body = (await client.get(photos_url("curiosity"), params=params)).json()

    assert body["total"] == 4
    assert [p["id"] for p in body["items"]] == world.ids(["c5-mahli"])


# --- 422 -----------------------------------------------------------------------------------


async def test_photos_sol_and_earth_date_together_return_422(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"sol": "2031", "earth_date": "2026-11-06"}
    response = await client.get(photos_url("perseverance"), params=params)

    assert_validation_error(response, "sol", "earth_date")


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("sol", "-1"),
        ("sol", str(INT32_MAX + 1)),
        ("sol", "abc"),
        ("sol", "1.5"),
        ("sol", HUGE),
        ("earth_date", "2026-13-01"),
        ("earth_date", "2026-02-30"),
        ("earth_date", "06-11-2026"),
        ("earth_date", "2026/11/06"),
        ("earth_date", "yesterday"),
        ("page", "0"),
        ("page", "-1"),
        ("page", "1000001"),
        ("page", "abc"),
        ("page", "1.5"),
        ("page", HUGE),
        ("per_page", "0"),
        ("per_page", "-5"),
        ("per_page", "101"),
        ("per_page", "abc"),
        ("per_page", HUGE),
        ("sample_type", "thumbnail"),
        ("sample_type", "mixed"),  # exists in responses, but is not a filter value
        ("sample_type", "chemcam prc"),
        ("sample_type", "all"),
        # Strict integers: no sign, underscores, spaces or decimals (pydantic lax mode).
        *[(param, value) for param in ("sol", "page", "per_page") for value in STRICT_INTS],
        ("earth_date", "1700000000"),  # Unix timestamp is not a date
    ],
    ids=lambda value: repr(value) if len(value) < 20 else f"{len(value)}-digits",
)
async def test_photos_invalid_param_returns_422_naming_the_field(
    client: httpx.AsyncClient, world: World, param: str, value: str
) -> None:
    response = await client.get(photos_url("curiosity"), params={param: value})

    assert_validation_error(response, param)


@pytest.mark.parametrize(
    ("rover", "camera"),
    [
        ("curiosity", "FOO"),
        ("curiosity", "NAVCAM_LEFT"),  # Perseverance camera
        ("perseverance", "FHAZ"),  # Curiosity camera
        ("perseverance", "NAVCAM"),  # v1 group name, not a v2 code
        ("spirit", "MAHLI"),
        ("curiosity", "mahlı"),  # dotless i: "mahlı".upper() == "MAHLI" in Python
    ],
    ids=[
        "unknown",
        "other-rover",
        "other-rover-legacy",
        "v1-group",
        "rover-without-data",
        "dotless-i",
    ],
)
async def test_photos_camera_unknown_for_rover_returns_422_listing_codes(
    client: httpx.AsyncClient, world: World, rover: str, camera: str
) -> None:
    response = await client.get(photos_url(rover), params={"camera": camera})

    details = assert_validation_error(response, "camera")
    assert len(details) == 1
    message = details[0]["message"]
    allowed = sorted(world.cameras[rover])
    # The rejected value is never an allowed code, so whole-token matches are the list.
    listed = [token for token in re.findall(r"[A-Z][A-Z0-9_]*", message) if token in allowed]
    assert listed == allowed, message
