"""GET /api/v2/photos/{id}: `in_sol`, `local_time`, `width`/`height` (docs/API.md, PhotoDetail).

`in_sol` is the place of the photo in its sol in the order of the `/photos` list
(`taken_at DESC, id DESC`): `position` from 1, `total`, `newer_id` (previous in the list),
`older_id` (next in the list). With `camera`/`sample_type` it is computed over the matching
photos of the sol; a photo that does not match gets `position: null` and the nearest matching
neighbours. Data: the shared `v2_world` (tests/api/v2/conftest.py).

Perseverance sol 2031 in list order: next-day, a, tie2, tie1, early (tie1/tie2 share taken_at;
the greater id comes first); cameras: next-day NAVCAM_LEFT, a NAVCAM_LEFT, tie2 NAVCAM_RIGHT
(subframe), tie1 NAVCAM_RIGHT (full), early MCZ_LEFT (downsampled).
"""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import (
    IN_SOL_KEYS,
    LOCAL_TIME_KEYS,
    OPPORTUNITY_ORDER,
    OPPORTUNITY_SOL,
    PERSEVERANCE_SOL_2031,
    V2,
    World,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api


def detail_url(world: World, source_id: str) -> str:
    return f"{V2}/photos/{world.photo_ids[source_id]}"


async def get_detail(
    client: httpx.AsyncClient, world: World, source_id: str, **params: str
) -> dict[str, Any]:
    response = await client.get(detail_url(world, source_id), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def expect(
    world: World,
    position: int | None,
    total: int,
    newer: str | None,
    older: str | None,
) -> dict[str, Any]:
    return {
        "position": position,
        "total": total,
        "newer_id": world.photo_ids[newer] if newer else None,
        "older_id": world.photo_ids[older] if older else None,
    }


# --- unfiltered: edges, middle, ties ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "position", "newer", "older"),
    [
        ("p2031-next-day", 1, None, "p2031-a"),
        ("p2031-a", 2, "p2031-next-day", "p2031-tie2"),
        ("p2031-tie2", 3, "p2031-a", "p2031-tie1"),
        ("p2031-tie1", 4, "p2031-tie2", "p2031-early"),
        ("p2031-early", 5, "p2031-tie1", None),
    ],
    ids=["first", "second", "middle-tie-greater-id", "tie-smaller-id", "last"],
)
async def test_in_sol_position_and_neighbours_in_list_order(
    client: httpx.AsyncClient,
    world: World,
    source_id: str,
    position: int,
    newer: str | None,
    older: str | None,
) -> None:
    body = await get_detail(client, world, source_id)

    assert list(body["in_sol"]) == IN_SOL_KEYS
    assert_json(body["in_sol"], expect(world, position, 5, newer, older))


async def test_in_sol_equal_taken_at_is_ordered_by_id_desc(
    client: httpx.AsyncClient, world: World
) -> None:
    tie1, tie2 = world.photo_ids["p2031-tie1"], world.photo_ids["p2031-tie2"]
    assert tie2 > tie1  # precondition: the same taken_at, tie2 has the greater id

    first = (await get_detail(client, world, "p2031-tie2"))["in_sol"]
    second = (await get_detail(client, world, "p2031-tie1"))["in_sol"]

    assert first["position"] + 1 == second["position"]
    assert first["older_id"] == tie1
    assert second["newer_id"] == tie2


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("p2030-late", (1, 2, None, "p2030-sub")),
        ("p2030-sub", (2, 2, "p2030-late", None)),
    ],
    ids=["late-first", "sub-last"],
)
async def test_in_sol_does_not_cross_sol_borders(
    client: httpx.AsyncClient,
    world: World,
    source_id: str,
    expected: tuple[int, int, str | None, str | None],
) -> None:
    """p2030-late was taken after every sol-2031 photo, but sol 2031 is not its neighbour."""
    body = await get_detail(client, world, source_id)

    assert_json(body["in_sol"], expect(world, *expected))


@pytest.mark.parametrize("source_id", ["c5-mahli", "c10-nav", "c20-sub"])
async def test_in_sol_single_photo_sol_is_one_of_one_with_null_neighbours(
    client: httpx.AsyncClient, world: World, source_id: str
) -> None:
    body = await get_detail(client, world, source_id)

    assert_json(body["in_sol"], {"position": 1, "total": 1, "newer_id": None, "older_id": None})


@pytest.mark.parametrize(
    "source_id",
    [
        "c1-down",
        "c1-chem",
        "c1-mixed",
        "c1-fhaz-lb-sub",
        "c1-fhaz-lb",
        "c1-fhaz",
        "o-00",
        "o-05",
        "o-29",
        "p2031-a",
    ],
)
async def test_in_sol_matches_reference_model_for_any_photo(
    client: httpx.AsyncClient, world: World, source_id: str
) -> None:
    body = await get_detail(client, world, source_id)

    assert_json(body["in_sol"], world.in_sol(source_id))


# --- camera / sample_type ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "params", "expected"),
    [
        # matching photo: position among the matching photos only
        ("p2031-a", {"camera": "NAVCAM_LEFT"}, (2, 2, "p2031-next-day", None)),
        ("p2031-a", {"camera": "navcam_left"}, (2, 2, "p2031-next-day", None)),
        ("p2031-next-day", {"camera": "NAVCAM_LEFT"}, (1, 2, None, "p2031-a")),
        ("p2031-tie1", {"camera": "NAVCAM_RIGHT"}, (2, 2, "p2031-tie2", None)),
        ("p2031-a", {"sample_type": "full"}, (2, 3, "p2031-next-day", "p2031-tie1")),
        ("p2031-tie1", {"sample_type": "full"}, (3, 3, "p2031-a", None)),
        ("p2031-tie1", {"camera": "NAVCAM_RIGHT", "sample_type": "full"}, (1, 1, None, None)),
        ("p2031-early", {"sample_type": "downsampled"}, (1, 1, None, None)),
        # the photo does not match: position null, nearest matching neighbours
        ("p2031-tie1", {"camera": "NAVCAM_LEFT"}, (None, 2, "p2031-a", None)),
        ("p2031-early", {"camera": "NAVCAM_LEFT"}, (None, 2, "p2031-a", None)),
        ("p2031-next-day", {"camera": "NAVCAM_RIGHT"}, (None, 2, None, "p2031-tie2")),
        ("p2031-tie2", {"sample_type": "full"}, (None, 3, "p2031-a", "p2031-tie1")),
        ("p2031-early", {"sample_type": "subframe"}, (None, 1, "p2031-tie2", None)),
        (
            "p2031-tie2",
            {"camera": "NAVCAM_RIGHT", "sample_type": "full"},
            (None, 1, None, "p2031-tie1"),
        ),
        # nothing in the sol matches
        ("p2031-early", {"camera": "SUPERCAM_RMI"}, (None, 0, None, None)),
        ("p2031-a", {"camera": "MCZ_LEFT", "sample_type": "full"}, (None, 0, None, None)),
        # Curiosity: a camera whose code is a prefix of another one, an unfilterable type
        ("c1-fhaz-lb", {"camera": "FHAZ_LEFT_B"}, (2, 2, "c1-fhaz-lb-sub", None)),
        ("c1-fhaz", {"camera": "FHAZ"}, (1, 1, None, None)),
        ("c1-fhaz-lb", {"camera": "FHAZ"}, (None, 1, None, "c1-fhaz")),
        ("c1-mixed", {"sample_type": "full"}, (None, 2, None, "c1-fhaz-lb")),
        ("c1-chem", {"sample_type": "full"}, (None, 2, None, "c1-fhaz-lb")),
    ],
    ids=[
        "camera-match-last",
        "camera-lowercase",
        "camera-match-first",
        "camera-match-tie",
        "type-match-middle",
        "type-match-last",
        "both-match",
        "type-single-match",
        "camera-mismatch-below",
        "camera-mismatch-last",
        "camera-mismatch-no-newer-match",
        "type-mismatch-between",
        "type-mismatch-last",
        "both-mismatch-tie",
        "no-photo-of-camera-in-sol",
        "nothing-matches-both",
        "curiosity-camera-prefix",
        "curiosity-camera-short",
        "curiosity-camera-mismatch",
        "curiosity-mixed-not-full",
        "curiosity-chemcam-not-full",
    ],
)
async def test_in_sol_with_filters_counts_matching_photos_only(
    client: httpx.AsyncClient,
    world: World,
    source_id: str,
    params: dict[str, str],
    expected: tuple[int | None, int, str | None, str | None],
) -> None:
    body = await get_detail(client, world, source_id, **params)

    assert_json(body["in_sol"], expect(world, *expected))
    # The reference model agrees with the hand-written expectation.
    assert body["in_sol"] == world.in_sol(
        source_id, params.get("camera"), params.get("sample_type")
    )


async def test_in_sol_filters_do_not_change_the_rest_of_the_photo(
    client: httpx.AsyncClient, world: World
) -> None:
    plain = await get_detail(client, world, "p2031-tie1")
    filtered = await get_detail(client, world, "p2031-tie1", camera="NAVCAM_LEFT")

    assert {k: v for k, v in plain.items() if k != "in_sol"} == {
        k: v for k, v in filtered.items() if k != "in_sol"
    }
    assert plain["in_sol"] != filtered["in_sol"]


# --- consistency with the /photos list ---------------------------------------------------------


async def list_sol_ids(
    client: httpx.AsyncClient, rover: str, per_page: int, **params: Any
) -> list[int]:
    ids: list[int] = []
    page = 1
    while True:
        response = await client.get(
            f"{V2}/rovers/{rover}/photos",
            params={**params, "page": str(page), "per_page": str(per_page)},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        if not items:
            return ids
        ids.extend(item["id"] for item in items)
        page += 1


async def test_in_sol_agrees_with_list_order_across_pages(
    client: httpx.AsyncClient, world: World
) -> None:
    """Every photo of a 30-photo sol: position/neighbours match the paginated /photos list."""
    ids = await list_sol_ids(client, "opportunity", per_page=7, sol=str(OPPORTUNITY_SOL))
    assert ids == world.ids(OPPORTUNITY_ORDER)  # precondition: 5 pages, newest first

    for index, photo_id in enumerate(ids):
        body = (await client.get(f"{V2}/photos/{photo_id}")).json()
        assert body["in_sol"] == {
            "position": index + 1,
            "total": len(ids),
            "newer_id": ids[index - 1] if index > 0 else None,
            "older_id": ids[index + 1] if index + 1 < len(ids) else None,
        }, f"photo #{index}"


@pytest.mark.parametrize(
    ("rover", "sol", "params"),
    [
        ("perseverance", "2031", {}),
        ("perseverance", "2031", {"camera": "NAVCAM_RIGHT"}),
        ("perseverance", "2031", {"sample_type": "full"}),
        ("curiosity", "1", {"sample_type": "full"}),
        ("curiosity", "1", {"camera": "MAST_LEFT"}),
    ],
    ids=["plain", "camera", "sample-type", "curiosity-type", "curiosity-camera"],
)
async def test_in_sol_with_filters_agrees_with_filtered_list(
    client: httpx.AsyncClient, world: World, rover: str, sol: str, params: dict[str, str]
) -> None:
    ids = await list_sol_ids(client, rover, per_page=2, sol=sol, **params)
    assert ids  # precondition

    for index, photo_id in enumerate(ids):
        body = (await client.get(f"{V2}/photos/{photo_id}", params=params)).json()
        assert body["in_sol"] == {
            "position": index + 1,
            "total": len(ids),
            "newer_id": ids[index - 1] if index > 0 else None,
            "older_id": ids[index + 1] if index + 1 < len(ids) else None,
        }, f"photo #{index}"


async def test_in_sol_total_equals_list_total_of_the_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    listing = (
        await client.get(
            f"{V2}/rovers/perseverance/photos", params={"sol": "2031", "per_page": "1"}
        )
    ).json()

    body = await get_detail(client, world, "p2031-a")

    assert body["in_sol"]["total"] == listing["total"] == len(PERSEVERANCE_SOL_2031)


# --- 422 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "param", "value"),
    [
        ("p2031-a", "camera", "FHAZ"),  # a Curiosity camera
        ("p2031-a", "camera", "nonexistent"),
        ("p2031-a", "camera", "\x00"),
        ("c1-fhaz", "camera", "NAVCAM_LEFT"),  # a Perseverance camera
        ("o-00", "camera", "FHAZ_LEFT_B"),
        ("p2031-a", "sample_type", "thumbnail"),
        ("p2031-a", "sample_type", "mixed"),
        ("p2031-a", "sample_type", "chemcam prc"),
        ("p2031-a", "sample_type", "all"),
        ("c1-fhaz", "sample_type", "thumbnail"),
    ],
    ids=[
        "camera-of-other-rover",
        "camera-unknown",
        "camera-nul",
        "curiosity-photo-perseverance-camera",
        "opportunity-photo-curiosity-camera",
        "type-thumbnail",
        "type-mixed",
        "type-chemcam-prc",
        "type-all",
        "curiosity-type-thumbnail",
    ],
)
async def test_photo_detail_invalid_filter_returns_422_naming_the_field(
    client: httpx.AsyncClient, world: World, source_id: str, param: str, value: str
) -> None:
    response = await client.get(detail_url(world, source_id), params={param: value})

    assert_validation_error(response, param)


async def test_photo_detail_camera_valid_for_rover_but_absent_in_sol_is_not_an_error(
    client: httpx.AsyncClient, world: World
) -> None:
    body = await get_detail(client, world, "p2031-a", camera="SUPERCAM_RMI")

    assert body["in_sol"]["position"] is None
    assert body["in_sol"]["total"] == 0


async def test_photo_detail_missing_photo_with_filters_returns_404(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(
        f"{V2}/photos/999999999", params={"camera": "NAVCAM_LEFT", "sample_type": "full"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "photo_not_found"


# --- ETag -------------------------------------------------------------------------------------


async def test_in_sol_etag_differs_for_different_params_and_is_stable_for_equal_ones(
    client: httpx.AsyncClient, world: World
) -> None:
    url = detail_url(world, "p2031-tie1")
    variants: list[dict[str, str]] = [
        {},
        {"camera": "NAVCAM_RIGHT"},
        {"camera": "NAVCAM_LEFT"},
        {"sample_type": "full"},
        {"camera": "NAVCAM_RIGHT", "sample_type": "full"},
    ]
    etags = []
    for params in variants:
        first = await client.get(url, params=params)
        again = await client.get(url, params=params)
        assert first.status_code == 200
        assert first.headers["etag"] == again.headers["etag"], params
        etags.append(first.headers["etag"])

    assert len(set(etags)) == len(variants), etags


async def test_in_sol_etag_of_unfiltered_request_does_not_give_304_for_filtered_one(
    client: httpx.AsyncClient, world: World
) -> None:
    url = detail_url(world, "p2031-tie1")
    etag = (await client.get(url)).headers["etag"]

    same = await client.get(url, headers={"If-None-Match": etag})
    filtered = await client.get(
        url, params={"camera": "NAVCAM_RIGHT"}, headers={"If-None-Match": etag}
    )

    assert same.status_code == 304
    assert filtered.status_code == 200
    assert filtered.json()["in_sol"]["total"] == 2


# --- local_time, width, height -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "local_time", "size"),
    [
        ("p2031-tie1", {"time": "11:30:55", "kind": "lmst", "planet": "mars"}, (1648, 1200)),
        (
            "c5-mahli",
            {"time": "11:25:43", "kind": "lmst", "planet": "mars"},
            (None, None),
        ),  # MSL size is always null
        ("o-05", {"time": "12:45:26", "kind": "ltst", "planet": "mars"}, (1024, 800)),
        ("p2031-a", None, (None, None)),  # no data in extra
        ("c1-fhaz", None, (None, None)),
        ("o-00", None, (None, None)),
    ],
    ids=[
        "perseverance",
        "curiosity-no-size",
        "opportunity-true-solar-time",
        "perseverance-no-data",
        "curiosity-no-data",
        "opportunity-no-data",
    ],
)
async def test_photo_detail_local_time_and_size_come_from_extra(
    client: httpx.AsyncClient,
    world: World,
    source_id: str,
    local_time: dict[str, str] | None,
    size: tuple[int | None, int | None],
) -> None:
    body = await get_detail(client, world, source_id)

    assert body["local_time"] == local_time
    if local_time is not None:
        assert list(body["local_time"]) == LOCAL_TIME_KEYS
    assert (body["width"], body["height"]) == size
    assert_json(body, world.detail(source_id))


async def test_photo_list_items_carry_width_height_and_local_time_but_no_in_sol(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/perseverance/photos", params={"sol": "2031"})).json()

    by_source = {item["source"]["id"]: item for item in body["items"]}
    assert (by_source["p2031-tie1"]["width"], by_source["p2031-tie1"]["height"]) == (1648, 1200)
    assert (by_source["p2031-a"]["width"], by_source["p2031-a"]["height"]) == (None, None)
    for item in body["items"]:
        assert list(item)[-2:] == ["local_time", "mast"]
        assert "in_sol" not in item
    assert by_source["p2031-tie1"]["local_time"] == {
        "time": "11:30:55",
        "kind": "lmst",
        "planet": "mars",
    }
    assert by_source["p2031-a"]["local_time"] is None


async def test_photo_latest_items_carry_width_and_height(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/perseverance/photos/latest")).json()

    by_source = {item["source"]["id"]: item for item in body["items"]}
    assert (by_source["p2031-tie1"]["width"], by_source["p2031-tie1"]["height"]) == (1648, 1200)
