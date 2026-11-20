"""`nearest_sols` of GET /api/v2/rovers/{rover}/photos (docs/API.md).

Not null only when `sol` or `earth_date` is given and `total = 0`: `before` is the greatest sol
with photos below the requested one, `after` the smallest above it (null if none). For
`earth_date` the earliest photo date of a sol (`manifest.sols[].earth_date`) is compared.
`camera` and `sample_type` are ignored when looking for the sols. Otherwise the key is null.

Dataset: curiosity has photos on sols 1, 5, 10, 20 (earth dates 2012-08-07, -11, -16, -26);
perseverance on 2030 and 2031; opportunity on sol 10; spirit has none.
"""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import NEAREST_SOLS_KEYS, V2, World, assert_json

pytestmark = pytest.mark.api


def photos_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/photos"


async def nearest(client: httpx.AsyncClient, rover: str, **params: str) -> Any:
    response = await client.get(photos_url(rover), params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["nearest_sols"], body


@pytest.mark.parametrize(
    ("rover", "sol", "expected"),
    [
        ("curiosity", "7", {"before": 5, "after": 10}),
        ("curiosity", "2", {"before": 1, "after": 5}),
        ("curiosity", "19", {"before": 10, "after": 20}),
        ("curiosity", "0", {"before": None, "after": 1}),
        ("curiosity", "25", {"before": 20, "after": None}),
        ("curiosity", "2147483647", {"before": 20, "after": None}),
        ("perseverance", "2000", {"before": None, "after": 2030}),
        ("perseverance", "2032", {"before": 2031, "after": None}),
        ("opportunity", "9", {"before": None, "after": 10}),
        ("opportunity", "11", {"before": 10, "after": None}),
        ("spirit", "5", {"before": None, "after": None}),
    ],
    ids=[
        "between",
        "between-near-first",
        "between-near-last",
        "below-first",
        "above-last",
        "int32-max",
        "perseverance-below",
        "perseverance-above",
        "opportunity-below",
        "opportunity-above",
        "no-data",
    ],
)
async def test_nearest_sols_for_empty_sol_are_the_sols_with_photos_around_it(
    client: httpx.AsyncClient, world: World, rover: str, sol: str, expected: dict[str, int | None]
) -> None:
    value, body = await nearest(client, rover, sol=sol)

    assert body["items"] == [] and body["total"] == 0
    assert value == expected
    assert list(value) == NEAREST_SOLS_KEYS


async def test_nearest_sols_response_has_the_key_last_after_total(
    client: httpx.AsyncClient, world: World
) -> None:
    _, body = await nearest(client, "curiosity", sol="7")

    assert_json(
        body,
        {
            "items": [],
            "page": 1,
            "per_page": 25,
            "total": 0,
            "nearest_sols": {"before": 5, "after": 10},
        },
    )


@pytest.mark.parametrize(
    ("rover", "earth_date", "expected"),
    [
        ("curiosity", "2012-08-13", {"before": 5, "after": 10}),
        ("curiosity", "2012-08-12", {"before": 5, "after": 10}),
        ("curiosity", "2012-08-01", {"before": None, "after": 1}),
        ("curiosity", "2012-09-30", {"before": 20, "after": None}),
        ("curiosity", "2012-08-08", {"before": 1, "after": 5}),
        ("perseverance", "2026-11-01", {"before": None, "after": 2030}),
        ("perseverance", "2026-12-31", {"before": 2031, "after": None}),
        ("opportunity", "2004-02-03", {"before": None, "after": 10}),
        ("opportunity", "2004-02-05", {"before": 10, "after": None}),
        ("spirit", "2005-01-01", {"before": None, "after": None}),
    ],
    ids=[
        "between",
        "between-other-day",
        "below-first",
        "above-last",
        "right-after-sol-1",
        "perseverance-below",
        "perseverance-above",
        "opportunity-day-before",
        "opportunity-day-after",
        "no-data",
    ],
)
async def test_nearest_sols_for_empty_earth_date_compare_the_earliest_date_of_each_sol(
    client: httpx.AsyncClient,
    world: World,
    rover: str,
    earth_date: str,
    expected: dict[str, int | None],
) -> None:
    value, body = await nearest(client, rover, earth_date=earth_date)

    assert body["items"] == [] and body["total"] == 0
    assert value == expected
    assert list(value) == NEAREST_SOLS_KEYS


@pytest.mark.parametrize(
    ("rover", "params", "expected"),
    [
        # sol 5 has photos, but none of this camera / type: the nearest sols ignore the filter
        ("curiosity", {"sol": "5", "camera": "NAVCAM"}, {"before": 1, "after": 10}),
        ("curiosity", {"sol": "5", "sample_type": "subframe"}, {"before": 1, "after": 10}),
        ("curiosity", {"sol": "1", "camera": "MARDI"}, {"before": None, "after": 5}),
        ("curiosity", {"sol": "20", "camera": "MAHLI"}, {"before": 10, "after": None}),
        (
            "perseverance",
            {"sol": "2031", "camera": "SUPERCAM_RMI"},
            {"before": 2030, "after": None},
        ),
        (
            "curiosity",
            {"earth_date": "2012-08-11", "camera": "NAVCAM"},
            {"before": 1, "after": 10},
        ),
    ],
    ids=[
        "camera-middle",
        "sample-type-middle",
        "camera-first-sol",
        "camera-last-sol",
        "perseverance-camera-last-sol",
        "earth-date-camera",
    ],
)
async def test_nearest_sols_when_filters_empty_a_non_empty_sol_ignore_the_filters(
    client: httpx.AsyncClient,
    world: World,
    rover: str,
    params: dict[str, str],
    expected: dict[str, int | None],
) -> None:
    value, body = await nearest(client, rover, **params)

    assert body["items"] == [] and body["total"] == 0
    assert value == expected


@pytest.mark.parametrize(
    ("rover", "params"),
    [
        ("curiosity", {"sol": "5"}),
        ("curiosity", {"earth_date": "2012-08-11"}),
        ("perseverance", {"sol": "2031", "camera": "NAVCAM_LEFT"}),
        ("perseverance", {"sol": "2031", "page": "2", "per_page": "2"}),
        # out-of-range page of a non-empty result: total > 0
        ("perseverance", {"sol": "2031", "page": "9"}),
    ],
    ids=["sol", "earth-date", "sol-camera", "second-page", "page-beyond-end"],
)
async def test_nearest_sols_is_null_when_the_result_has_photos(
    client: httpx.AsyncClient, world: World, rover: str, params: dict[str, str]
) -> None:
    value, body = await nearest(client, rover, **params)

    assert body["total"] > 0
    assert value is None
    assert "nearest_sols" in body


@pytest.mark.parametrize(
    ("rover", "params"),
    [
        ("curiosity", {}),
        ("spirit", {}),  # nothing at all, but no sol / earth_date to look around
        ("curiosity", {"camera": "MARDI"}),  # empty by camera only
        ("curiosity", {"sample_type": "downsampled", "camera": "MAHLI"}),
        ("curiosity", {"page": "9"}),
    ],
    ids=["all", "no-data-rover", "camera-only", "camera-and-type", "page-beyond-end"],
)
async def test_nearest_sols_is_null_without_sol_and_earth_date(
    client: httpx.AsyncClient, world: World, rover: str, params: dict[str, str]
) -> None:
    value, body = await nearest(client, rover, **params)

    assert value is None
    assert list(body)[-1] == "nearest_sols"


async def test_nearest_sols_does_not_mix_in_other_rovers(
    client: httpx.AsyncClient, world: World
) -> None:
    """Perseverance sol 5: the nearest sol is its own (2030), not Curiosity's sol 5 or 10."""
    value, _ = await nearest(client, "perseverance", sol="5")

    assert value == {"before": None, "after": 2030}


async def test_latest_response_has_no_nearest_sols_key(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/photos/latest")).json()

    assert list(body) == ["sol", "items", "page", "per_page", "total"]
