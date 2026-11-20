"""Fast serialization of v1/v2 responses (stage 9): the JSON must not change.

Large responses are serialized straight from prepared data (orjson, no `response_model`
re-validation). The contract is that clients see the same JSON as before: same keys, same
key order, same values, same date/datetime format (`YYYY-MM-DD`; v2 datetimes
`YYYY-MM-DDTHH:MM:SSZ`), nulls kept as `null`, non-ASCII text unchanged.

Oracle: the response models of the API (their field order is the JSON key order, see the
module docstrings of app/api/legacy/schemas.py and app/api/v2/schemas.py). Every response is
parsed, re-validated by its model and dumped again; the result must equal the response both
in values and in key order at every level. The values that matter are also pinned directly.

The module commits its own dataset via `clean_db`.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.legacy import schemas as v1
from app.api.v2 import schemas as v2
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.conftest import build_record

pytestmark = pytest.mark.api

Factory = async_sessionmaker[AsyncSession]

UNICODE_CREDIT = "NASA/JPL-Caltech/MSSS — Ünïcödé ✓ 火星 \U0001f680"
UNICODE_TITLE = 'Sol 1: «Gale» crater, "quoted" \\ back\\slash, tab\tend'
UNICODE_CAPTION = "Ελληνικά · русский · 日本語"
# A big sol: the v1 list without `page` is far above any size threshold.
BIG_SOL = 7
BIG_COUNT = 150
SMALL_SOL = 1
TAKEN = datetime(2012, 8, 7, 12, 0, 30, 999999, tzinfo=UTC)


def _records() -> list[Any]:
    records = [
        # All optional fields filled, non-ASCII everywhere it can be.
        build_record(
            source_id="ser-full-unicode",
            sol=SMALL_SOL,
            camera_code="FHAZ",
            sample_type="full",
            taken_at=TAKEN,
            earth_date=TAKEN.date(),
            received_at=TAKEN + timedelta(hours=3, microseconds=1),
            img_small="https://images.example.test/ser-small.jpg",
            img_medium="https://images.example.test/ser-medium.jpg",
            img_large="https://images.example.test/ser-large.jpg",
            img_full="https://images.example.test/ser-ünï.JPG",
            title=UNICODE_TITLE,
            caption=UNICODE_CAPTION,
            credit=UNICODE_CREDIT,
            source_link="https://mars.nasa.gov/msl/raw/?id=ser-full-unicode",
            site=7,
            drive=0,
            extra={"filter_name": "UNK", "nested": {"ключ": [1, None, "火"]}, "empty": None},
        ),
        # Every optional field null.
        build_record(
            source_id="ser-nulls",
            sol=SMALL_SOL,
            camera_code="NAVCAM",
            sample_type="full",
            taken_at=TAKEN - timedelta(minutes=1),
            earth_date=TAKEN.date(),
            img_full="https://images.example.test/ser-nulls.JPG",
            credit=None,
        ),
    ]
    for n in range(BIG_COUNT):
        taken_at = datetime(2012, 8, 13, 0, 0, tzinfo=UTC) + timedelta(seconds=n)
        records.append(
            build_record(
                source_id=f"ser-big-{n:03d}",
                sol=BIG_SOL,
                camera_code="MAST" if n % 2 else "FHAZ",
                sample_type="full",
                taken_at=taken_at,
                earth_date=taken_at.date(),
                img_full=f"https://images.example.test/ser-big-{n:03d}.JPG",
                title=f"Снимок №{n}",
                credit=UNICODE_CREDIT,
            )
        )
    return records


@pytest.fixture
async def dataset(clean_db: Factory) -> dict[str, int]:
    async with clean_db() as session:
        records = _records()
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(session, "curiosity", {r.sol for r in records})
        await refresh_rover_stats(session, "curiosity")
        await session.commit()
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        return {source_id: photo_id for source_id, photo_id in rows}


# --- oracle -------------------------------------------------------------------------------


def assert_same_key_order(actual: Any, expected: Any, path: str = "$") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert list(actual) == list(expected), path
        for key in expected:
            assert_same_key_order(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_same_key_order(a, e, f"{path}[{i}]")


def assert_model_shape(response: httpx.Response, model: type[BaseModel]) -> Any:
    """The body equals its response model's own JSON, values and key order."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = json.loads(response.content.decode("utf-8"))
    canonical = model.model_validate_json(response.content).model_dump(mode="json")
    assert body == canonical
    assert_same_key_order(body, canonical)
    return body


Z_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --- v1 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
@pytest.mark.parametrize("sol", [SMALL_SOL, BIG_SOL], ids=["small", "big"])
async def test_v1_photos_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int], prefix: str, sol: int
) -> None:
    response = await client.get(f"{prefix}/rovers/curiosity/photos", params={"sol": str(sol)})

    body = assert_model_shape(response, v1.PhotosResponse)
    expected = 2 if sol == SMALL_SOL else BIG_COUNT
    assert len(body["photos"]) == expected
    assert list(body["photos"][0]) == ["id", "sol", "camera", "img_src", "earth_date", "rover"]


async def test_v1_photos_values_unicode_and_nulls(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v1/rovers/curiosity/photos", params={"sol": "1"})
    body = assert_model_shape(response, v1.PhotosResponse)
    by_id = {photo["id"]: photo for photo in body["photos"]}

    photo = by_id[dataset["ser-full-unicode"]]
    assert photo["img_src"] == "https://images.example.test/ser-ünï.JPG"
    assert photo["earth_date"] == "2012-08-07"
    assert photo["sol"] == 1
    assert list(photo["camera"]) == ["id", "name", "rover_id", "full_name"]
    assert photo["camera"]["name"] == "FHAZ"
    rover = photo["rover"]
    assert list(rover) == [
        "id",
        "name",
        "landing_date",
        "launch_date",
        "status",
        "max_sol",
        "max_date",
        "total_photos",
        "cameras",
    ]
    assert (rover["landing_date"], rover["launch_date"]) == ("2012-08-06", "2011-11-26")
    assert rover["max_sol"] == BIG_SOL


async def test_v1_latest_photos_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v1/rovers/curiosity/latest_photos")

    body = assert_model_shape(response, v1.LatestPhotosResponse)
    assert len(body["latest_photos"]) == BIG_COUNT
    assert {photo["sol"] for photo in body["latest_photos"]} == {BIG_SOL}


async def test_v1_manifest_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v1/manifests/curiosity")

    body = assert_model_shape(response, v1.ManifestResponse)
    manifest = body["photo_manifest"]
    assert manifest["total_photos"] == BIG_COUNT + 2
    by_sol = {entry["sol"]: entry for entry in manifest["photos"]}
    assert set(by_sol) == {SMALL_SOL, BIG_SOL}
    assert sorted(by_sol[SMALL_SOL]["cameras"]) == ["FHAZ", "NAVCAM"]
    assert by_sol[BIG_SOL]["earth_date"] == "2012-08-13"


@pytest.mark.parametrize("path", ["/rovers/curiosity", "/rovers/spirit", "/rovers"])
async def test_v1_rover_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int], path: str
) -> None:
    response = await client.get(f"/api/v1{path}")

    model = v1.RoversResponse if path == "/rovers" else v1.RoverResponse
    body = assert_model_shape(response, model)
    if path == "/rovers/spirit":
        # No data: nulls stay null, not missing and not 0.
        assert body["rover"]["max_sol"] is None
        assert body["rover"]["max_date"] is None
        assert "max_sol" in body["rover"]


# --- v2 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{"sol": "1"}, {"sol": str(BIG_SOL), "per_page": "100"}, {}],
    ids=["small-sol", "big-page", "no-filter"],
)
async def test_v2_photos_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int], params: dict[str, str]
) -> None:
    response = await client.get("/api/v2/rovers/curiosity/photos", params=params)

    body = assert_model_shape(response, v2.PhotosResponse)
    assert list(body) == ["items", "page", "per_page", "total", "nearest_sols"]
    for item in body["items"]:
        assert Z_DATETIME.match(item["taken_at"]), item["taken_at"]


async def test_v2_photos_values_unicode_nulls_and_datetime_format(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v2/rovers/curiosity/photos", params={"sol": "1"})
    body = assert_model_shape(response, v2.PhotosResponse)
    by_id = {item["id"]: item for item in body["items"]}

    full = by_id[dataset["ser-full-unicode"]]
    assert full["taken_at"] == "2012-08-07T12:00:30Z"  # fraction dropped, not rounded
    assert full["earth_date"] == "2012-08-07"
    assert full["title"] == UNICODE_TITLE
    assert full["credit"] == UNICODE_CREDIT
    assert full["images"] == {
        "small": "https://images.example.test/ser-small.jpg",
        "medium": "https://images.example.test/ser-medium.jpg",
        "large": "https://images.example.test/ser-large.jpg",
        "full": "https://images.example.test/ser-ünï.JPG",
    }

    nulls = by_id[dataset["ser-nulls"]]
    assert nulls["title"] is None
    assert nulls["credit"] is None
    assert nulls["images"] == {
        "small": None,
        "medium": None,
        "large": None,
        "full": "https://images.example.test/ser-nulls.JPG",
    }
    assert list(nulls["images"]) == ["small", "medium", "large", "full"]
    assert nulls["source"] == {"id": "ser-nulls", "link": None}


async def test_v2_photos_body_is_valid_utf8_with_non_ascii_text(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    raw = (await client.get("/api/v2/rovers/curiosity/photos", params={"sol": "1"})).content

    text_body = raw.decode("utf-8")  # strict: no lone surrogates, no Latin-1
    assert json.loads(text_body)["items"]
    # Either raw UTF-8 or \u escapes are valid JSON; the decoded values were checked above.
    assert "火星" in text_body or "\\u706b\\u661f" in text_body


async def test_v2_photo_detail_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get(f"/api/v2/photos/{dataset['ser-full-unicode']}")

    body = assert_model_shape(response, v2.PhotoDetailOut)
    assert body["caption"] == UNICODE_CAPTION
    assert body["received_at"] == "2012-08-07T15:00:31Z"  # 30.999999 + 3 h + 1 us
    assert body["extra"] == {
        "filter_name": "UNK",
        "nested": {"ключ": [1, None, "火"]},
        "empty": None,
    }


async def test_v2_manifest_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v2/rovers/curiosity/manifest")

    body = assert_model_shape(response, v2.ManifestResponse)
    assert list(body) == ["rover", "sols"]
    assert [entry["sol"] for entry in body["sols"]] == [BIG_SOL, SMALL_SOL]
    big = body["sols"][0]
    assert (big["sol"], big["earth_date"], big["total_photos"]) == (
        BIG_SOL,
        "2012-08-13",
        BIG_COUNT,
    )
    assert sorted(big["cameras"]) == ["FHAZ", "MAST"]
    rover = body["rover"]
    assert rover["last_synced_at"] is None or Z_DATETIME.match(rover["last_synced_at"])
    assert rover["max_date"] == "2012-08-13"
    assert date.fromisoformat(rover["launch_date"]) == date(2011, 11, 26)


async def test_v2_manifest_rover_without_data_keeps_nulls(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v2/rovers/spirit/manifest")

    body = assert_model_shape(response, v2.ManifestResponse)
    assert body["sols"] == []
    assert body["rover"]["max_sol"] is None
    assert body["rover"]["last_synced_at"] is None


async def test_v2_latest_json_matches_model_shape(
    client: httpx.AsyncClient, dataset: dict[str, int]
) -> None:
    response = await client.get("/api/v2/rovers/curiosity/photos/latest", params={"per_page": "50"})

    body = assert_model_shape(response, v2.LatestPhotosResponse)
    assert body["sol"] == BIG_SOL
    assert body["total"] == BIG_COUNT
