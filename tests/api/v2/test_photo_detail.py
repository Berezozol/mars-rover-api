"""GET /api/v2/photos/{id} (docs/API.md, PhotoDetail)."""

import httpx
import pytest

from tests.api.v2.conftest import (
    PHOTO_DETAIL_KEYS,
    V2,
    World,
    assert_error,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api


async def test_photo_detail_returns_exact_json(client: httpx.AsyncClient, world: World) -> None:
    photo_id = world.photo_ids["p2031-a"]

    response = await client.get(f"{V2}/photos/{photo_id}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == PHOTO_DETAIL_KEYS
    assert_json(body, world.detail("p2031-a"))
    assert body["received_at"] == "2026-11-07T01:02:03Z"
    assert body["extra"] == {"filter_name": "UNK", "attitude": "(0.1,0.2,0.3,0.4)"}


@pytest.mark.parametrize(
    "source_id",
    ["c1-mixed", "c1-chem", "p2030-sub", "o-00"],
    ids=["curiosity-mixed", "curiosity-chemcam-prc", "perseverance-subframe", "opportunity"],
)
async def test_photo_detail_any_sample_type_and_null_fields(
    client: httpx.AsyncClient, world: World, source_id: str
) -> None:
    response = await client.get(f"{V2}/photos/{world.photo_ids[source_id]}")

    assert response.status_code == 200
    assert_json(response.json(), world.detail(source_id))


async def test_photo_detail_without_optional_fields_has_nulls_and_empty_extra(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/photos/{world.photo_ids['c1-mixed']}")).json()

    assert body["caption"] is None
    assert body["received_at"] is None
    assert body["site"] is None
    assert body["drive"] is None
    assert body["extra"] == {}
    assert body["sample_type"] == "mixed"


@pytest.mark.parametrize(
    "photo_id",
    ["0", "-1", "999999999", str(2**63 - 1), str(2**63), "9" * 20, "9" * 5000],
    ids=["zero", "negative", "missing", "bigint-max", "bigint-max-plus-1", "20-digits", "huge"],
)
async def test_photo_detail_missing_or_out_of_range_returns_404(
    client: httpx.AsyncClient, world: World, photo_id: str
) -> None:
    response = await client.get(f"{V2}/photos/{photo_id}")

    error = assert_error(response, 404, "photo_not_found")
    assert error["details"] is None


@pytest.mark.parametrize(
    "photo_id", ["abc", "1.5", "1e3", "0x10", "%00"], ids=["word", "float", "exp", "hex", "nul"]
)
async def test_photo_detail_non_integer_id_returns_422(
    client: httpx.AsyncClient, world: World, photo_id: str
) -> None:
    response = await client.get(f"{V2}/photos/{photo_id}")

    assert_validation_error(response, "id", "photo_id")
