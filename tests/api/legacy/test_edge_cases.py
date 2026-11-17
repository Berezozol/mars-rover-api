"""v1 edge cases decided in stage 4 review round 1 (team lead kept the backend's decisions).

- Huge integers never cause 500: huge sol -> [], huge page -> [], huge per_page -> capped.
- Empty values count as given: sol= / earth_date= / page= -> 400, camera= -> [].
- per_page without page is ignored, even when it is not an integer.
- page is validated even without sol/earth_date.
- /photos/{id}: 0, negative and beyond bigint -> 404; non-strict integers (1.0) -> 400.
- An earth_date that falls on a skipped day (no sol maps to it) -> [].
"""

from datetime import date

import httpx
import pytest

from app.core.legacy_dates import legacy_sol_for_date
from tests.api.legacy.conftest import (
    CURIOSITY_SOL1_ORDER,
    LANDING,
    PAGED_SOL,
    Factory,
    MakeRecord,
    World,
    load_world,
    paged_ids,
    seed,
)

pytestmark = pytest.mark.api

HUGE = "9" * 5000
INT32_MAX = 2**31 - 1


async def get_json(client: httpx.AsyncClient, url: str, **params: str) -> tuple[int, object]:
    response = await client.get(url, params=params)
    return response.status_code, response.json()


# --- huge integers (MAJOR 2) -------------------------------------------------------------


@pytest.mark.parametrize(
    "sol",
    [HUGE, "-" + HUGE, str(INT32_MAX + 1), str(-INT32_MAX - 2), str(2**63)],
    ids=["5000-nines", "minus-5000-nines", "int32-max-plus-1", "int32-min-minus-1", "2-pow-63"],
)
async def test_photos_huge_sol_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, sol: str
) -> None:
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", sol=sol)

    assert (status, body) == (200, {"photos": []})


@pytest.mark.parametrize(
    "page", [HUGE, str(INT32_MAX + 1), str(2**63)], ids=["5000-digits", "int32+1", "2-pow-63"]
)
@pytest.mark.parametrize("per_page", [None, "30", HUGE], ids=["default", "30", "huge-per-page"])
async def test_photos_huge_page_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, page: str, per_page: str | None
) -> None:
    params = {"sol": "1", "page": page}
    if per_page is not None:
        params["per_page"] = per_page
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", **params)

    assert (status, body) == (200, {"photos": []})


@pytest.mark.parametrize(
    ("path", "params", "key"),
    [
        ("/rovers/curiosity/photos", {"sol": str(PAGED_SOL)}, "photos"),
        ("/rovers/curiosity/latest_photos", {}, "latest_photos"),
    ],
    ids=["photos", "latest_photos"],
)
@pytest.mark.parametrize("per_page", [HUGE, str(2**63)], ids=["5000-digits", "2-pow-63"])
async def test_huge_per_page_is_capped_at_200(
    client: httpx.AsyncClient,
    prefix: str,
    paged_world: World,
    path: str,
    params: dict[str, str],
    key: str,
    per_page: str,
) -> None:
    status, body = await get_json(client, prefix + path, **params, page="1", per_page=per_page)

    assert status == 200
    assert isinstance(body, dict)
    assert [photo["id"] for photo in body[key]] == paged_ids(paged_world)[:200]


async def test_huge_negative_page_is_page_1(
    client: httpx.AsyncClient, prefix: str, paged_world: World
) -> None:
    status, body = await get_json(
        client, f"{prefix}/rovers/curiosity/photos", sol=str(PAGED_SOL), page="-" + HUGE
    )

    assert status == 200
    assert isinstance(body, dict)
    assert [photo["id"] for photo in body["photos"]] == paged_ids(paged_world)[:25]


@pytest.mark.parametrize(
    ("sol", "page", "per_page", "window"),
    [("0001", None, None, None), ("1", "0001", "0005", slice(0, 5))],
    ids=["sol-leading-zeros", "page-per-page-leading-zeros"],
)
async def test_photos_leading_zeros_are_plain_integers(
    client: httpx.AsyncClient,
    prefix: str,
    world: World,
    sol: str,
    page: str | None,
    per_page: str | None,
    window: slice | None,
) -> None:
    params = {"sol": sol}
    if page is not None:
        params["page"] = page
    if per_page is not None:
        params["per_page"] = per_page
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", **params)

    expected = CURIOSITY_SOL1_ORDER if window is None else CURIOSITY_SOL1_ORDER[window]
    assert (status, body) == (200, {"photos": world.photos(expected)})


# --- empty values count as given (MINOR 3) -------------------------------------------------


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"sol": ""}, "Invalid sol"),
        ({"earth_date": ""}, "Invalid earth_date"),
        ({"sol": "1", "page": ""}, "Invalid page"),
        ({"sol": "1", "page": "1", "per_page": ""}, "Invalid per_page"),
    ],
    ids=["sol", "earth_date", "page", "per_page-with-page"],
)
async def test_photos_empty_value_returns_400(
    client: httpx.AsyncClient, prefix: str, world: World, params: dict[str, str], message: str
) -> None:
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", **params)

    assert (status, body) == (400, {"errors": message})


async def test_photos_empty_camera_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", sol="1", camera="")

    assert (status, body) == (200, {"photos": []})


async def test_latest_photos_empty_page_returns_400(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/latest_photos", page="")

    assert (status, body) == (400, {"errors": "Invalid page"})


# --- per_page without page; page without sol (MINOR 3) -------------------------------------


@pytest.mark.parametrize("per_page", ["abc", "", "1.5", "0", "5"])
async def test_photos_per_page_without_page_is_ignored(
    client: httpx.AsyncClient, prefix: str, world: World, per_page: str
) -> None:
    status, body = await get_json(
        client, f"{prefix}/rovers/curiosity/photos", sol="1", per_page=per_page
    )

    assert (status, body) == (200, {"photos": world.photos(CURIOSITY_SOL1_ORDER)})


@pytest.mark.parametrize("page", ["x", "", "1.5"])
async def test_photos_invalid_page_without_sol_or_earth_date_returns_400(
    client: httpx.AsyncClient, prefix: str, world: World, page: str
) -> None:
    status, body = await get_json(client, f"{prefix}/rovers/curiosity/photos", page=page)

    assert (status, body) == (400, {"errors": "Invalid page"})


# --- /photos/{id} (MINOR 3, MINOR 5) ----------------------------------------------------------


@pytest.mark.parametrize(
    "photo_id",
    ["0", "-1", str(2**63), "99999999999999999999"],
    ids=["zero", "negative", "bigint-max-plus-1", "20-nines"],
)
async def test_photo_by_id_out_of_range_returns_404(
    client: httpx.AsyncClient, prefix: str, world: World, photo_id: str
) -> None:
    status, body = await get_json(client, f"{prefix}/photos/{photo_id}")

    assert (status, body) == (404, {"errors": "Photo not found"})


@pytest.mark.parametrize("photo_id", ["1.0", "1e3", "0x10", "1_000", "+1", " 1"])
async def test_photo_by_id_not_strict_integer_returns_400(
    client: httpx.AsyncClient, prefix: str, world: World, photo_id: str
) -> None:
    status, body = await get_json(client, f"{prefix}/photos/{photo_id}")

    assert (status, body) == (400, {"errors": "Invalid id"})


# --- earth_date on a skipped day (MINOR 3) --------------------------------------------------

# Curiosity: sol 36 -> landing + 36 days (2012-09-11), sol 37 -> landing + 38 days
# (2012-09-13); no sol maps to landing + 37 days.
SKIPPED_DAY = date(2012, 9, 12)


@pytest.fixture
async def around_skipped_day(clean_db: Factory, make_record: MakeRecord) -> World:
    records = [
        make_record(source_id="skip-36", sol=36, camera_code="MAHLI"),
        make_record(source_id="skip-37", sol=37, camera_code="MAHLI"),
    ]
    await seed(clean_db, "curiosity", records)
    return await load_world(clean_db, {"curiosity": records}, {"curiosity": (37, None, 2)})


def test_skipped_day_has_no_sol() -> None:
    # Sanity check of the test data against the legacy formula.
    assert legacy_sol_for_date(LANDING["curiosity"], SKIPPED_DAY) is None
    assert legacy_sol_for_date(LANDING["curiosity"], date(2012, 9, 11)) == 36
    assert legacy_sol_for_date(LANDING["curiosity"], date(2012, 9, 13)) == 37


@pytest.mark.parametrize(
    ("earth_date", "expected"),
    [("2012-9-11", ["skip-36"]), ("2012-09-12", []), ("2012-9-13", ["skip-37"])],
    ids=["day-before", "skipped-day", "day-after"],
)
async def test_photos_earth_date_on_skipped_day_returns_empty_list(
    client: httpx.AsyncClient,
    prefix: str,
    around_skipped_day: World,
    earth_date: str,
    expected: list[str],
) -> None:
    status, body = await get_json(
        client, f"{prefix}/rovers/curiosity/photos", earth_date=earth_date
    )

    assert status == 200
    assert isinstance(body, dict)
    assert [photo["id"] for photo in body["photos"]] == [
        around_skipped_day.photo_ids[source_id] for source_id in expected
    ]
