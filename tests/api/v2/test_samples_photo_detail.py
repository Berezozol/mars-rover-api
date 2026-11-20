"""PhotoDetail.samples (docs/API.md, stage 13) and "v1 is unchanged".

`samples` is the last key of the photo card: numbers of the Perseverance samples sealed in the
photo's sol, ascending; `[]` for a sol without samples, for a sample with an unknown sol and for
every other rover. Lists of photos keep their keys. v1 has no samples at all.
"""

import httpx
import pytest
from sqlalchemy import text

from tests._samples import Factory, db_sample, insert_samples
from tests.api.legacy.conftest import PHOTO_KEYS as LEGACY_PHOTO_KEYS
from tests.api.v2._samples_data import photo, seed_dataset, seed_photos
from tests.api.v2.conftest import PHOTO_DETAIL_KEYS, PHOTO_KEYS, V2

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

P = "perseverance"


@pytest.fixture
async def ids(clean_db: Factory) -> dict[str, int]:
    """Photos of several sols/rovers and samples; returns a label -> photo id."""
    await insert_samples(
        clean_db,
        [
            db_sample(41, sol=700),  # two samples sealed in one sol, inserted out of order
            db_sample(40, sol=700),
            db_sample(1, sol=164),
            db_sample(29, sol=None),  # TBD: matches no sol
        ],
    )
    await seed_dataset(clean_db, items=4)
    plan = {
        "p164": (P, photo(164)),
        "p700": (P, photo(700)),
        "p701": (P, photo(701)),
        "c164": ("curiosity", photo(164, rover="curiosity")),
        "c700": ("curiosity", photo(700, rover="curiosity")),
        "h164": ("ingenuity", photo(164, rover="ingenuity")),
        "h700": ("ingenuity", photo(700, rover="ingenuity")),
    }
    for rover, record in plan.values():
        await seed_photos(clean_db, rover, [record])
    async with clean_db() as session:
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        by_source = {source_id: photo_id for source_id, photo_id in rows}
    return {label: by_source[record.source_id] for label, (_, record) in plan.items()}


@pytest.mark.parametrize(
    ("label", "samples"),
    [
        pytest.param("p164", [1], id="one-sample"),
        pytest.param("p700", [40, 41], id="two-samples-ascending"),
        pytest.param("p701", [], id="sol-without-samples"),
        pytest.param("c164", [], id="curiosity-same-sol"),
        pytest.param("c700", [], id="curiosity-sol-with-two-samples"),
        pytest.param("h164", [], id="ingenuity-same-sol"),
        pytest.param("h700", [], id="ingenuity-sol-with-two-samples"),
    ],
)
async def test_photo_detail_samples_follow_the_sol_of_perseverance(
    client: httpx.AsyncClient, ids: dict[str, int], label: str, samples: list[int]
) -> None:
    response = await client.get(f"{V2}/photos/{ids[label]}")

    assert response.status_code == 200
    body = response.json()
    assert body["samples"] == samples
    assert list(body) == PHOTO_DETAIL_KEYS
    assert list(body)[-1] == "samples"


async def test_photo_detail_samples_are_integers(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    samples = (await client.get(f"{V2}/photos/{ids['p700']}")).json()["samples"]

    assert samples == [40, 41] and all(type(n) is int for n in samples)


async def test_photo_detail_without_loaded_samples_has_an_empty_list(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    record = photo(164)
    await seed_photos(clean_db, P, [record])
    async with clean_db() as session:
        photo_id = await session.scalar(text("SELECT id FROM photos"))

    body = (await client.get(f"{V2}/photos/{photo_id}")).json()

    assert body["samples"] == []


async def test_photo_detail_samples_ignore_the_camera_and_type_of_the_photo(
    client: httpx.AsyncClient, clean_db: Factory, ids: dict[str, int]
) -> None:
    """The link is by sol only (a tube is shot by CACHECAM, but any photo of the sol links)."""
    await seed_photos(clean_db, P, [photo(164, sample_type="subframe")])
    async with clean_db() as session:
        photo_id = await session.scalar(
            text("SELECT max(id) FROM photos WHERE rover_id = 'perseverance' AND sol = 164")
        )

    body = (await client.get(f"{V2}/photos/{photo_id}")).json()

    assert body["samples"] == [1]


async def test_photo_lists_do_not_get_the_samples_key(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    body = (await client.get(f"{V2}/rovers/perseverance/photos", params={"sol": 164})).json()

    assert body["items"]
    for item in body["items"]:
        assert list(item) == PHOTO_KEYS
        assert "samples" not in item


# --- v1 is unchanged ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_photo_has_no_samples_key(
    client: httpx.AsyncClient, ids: dict[str, int], prefix: str
) -> None:
    response = await client.get(f"{prefix}/photos/{ids['p164']}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["photo"]
    assert list(body["photo"]) == LEGACY_PHOTO_KEYS
    assert "samples" not in response.text


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
@pytest.mark.parametrize("tail", ["/samples", "/samples/1"])
async def test_v1_has_no_samples_endpoints(
    client: httpx.AsyncClient, ids: dict[str, int], prefix: str, tail: str
) -> None:
    response = await client.get(f"{prefix}/rovers/perseverance{tail}")

    assert response.status_code == 404
    assert "Sapphire" not in response.text and '"items"' not in response.text


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_rover_and_photos_listing_are_not_affected_by_samples(
    client: httpx.AsyncClient, ids: dict[str, int], prefix: str
) -> None:
    rover = await client.get(f"{prefix}/rovers/perseverance")
    photos = await client.get(f"{prefix}/rovers/perseverance/photos", params={"sol": 164})

    assert rover.status_code == 200 and "samples" not in rover.text
    assert photos.status_code == 200 and "samples" not in photos.text
    assert list(photos.json()) == ["photos"]
