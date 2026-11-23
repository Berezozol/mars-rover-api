"""Part 17G: `WEATHER_M20_GROUND_ENABLED` and the Perseverance ground-temperature estimate (AC-48).

Flag on (the default): `ground_temp_c` is in a Perseverance answer (sols, hours, `parameters`
with `quality` `estimate`). Flag off: the key is nowhere, even when the database holds ground
values. Curiosity is not affected by the flag.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app
from tests._weather import Factory, set_hour
from tests.api.v2.test_weather import C, P, seed

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

GROUND = {"ground_min_k": 200.15, "ground_max_k": 283.15, "ground_mean_k": 243.15, "n_ground": 50}


async def make_client(settings: Settings, tmp_path: Path, enabled: bool) -> Any:
    app = create_app(
        settings.model_copy(
            update={
                "mer_image_cache_dir": tmp_path / f"mer-{enabled}",
                "weather_m20_ground_enabled": enabled,
            }
        )
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return app, client


@pytest.fixture
async def flag_on(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app, client = await make_client(settings, tmp_path, True)
    try:
        yield client
    finally:
        await client.aclose()
        shared = getattr(app.state, "upstream_client", None)
        if shared is not None:
            await shared.aclose()
        await app.state.engine.dispose()


@pytest.fixture
async def flag_off(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app, client = await make_client(settings, tmp_path, False)
    try:
        yield client
    finally:
        await client.aclose()
        shared = getattr(app.state, "upstream_client", None)
        if shared is not None:
            await shared.aclose()
        await app.state.engine.dispose()


async def seed_with_ground(factory: Factory) -> None:
    await seed(factory, "perseverance", 100, **GROUND)
    await set_hour(factory, 100, 14, ground_k=263.15)


def test_the_flag_is_on_by_default_ac48() -> None:
    assert Settings().weather_m20_ground_enabled is True


async def test_flag_off_hides_ground_everywhere_even_with_data_in_the_base_ac48(
    flag_off: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_with_ground(clean_db)

    listing = await flag_off.get(P)
    detail = await flag_off.get(f"{P}/100")

    assert "ground" not in listing.text and "ground" not in detail.text
    body = listing.json()
    assert list(body["parameters"]) == ["air_temp_c", "pressure_pa", "humidity_pct", "wind"]
    assert "ground_temp_c" not in body["items"][0]
    one = detail.json()
    assert "ground_temp_c" not in one
    assert all("ground_temp_c" not in hour for hour in one["hours"])


async def test_flag_on_gives_the_estimate_in_parameters_sols_and_hours_ac48(
    flag_on: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_with_ground(clean_db)

    listing = (await flag_on.get(P)).json()
    detail = (await flag_on.get(f"{P}/100")).json()

    assert listing["parameters"]["ground_temp_c"] == {"unit": "°C", "quality": "estimate"}
    assert list(listing["parameters"])[-1] == "ground_temp_c"
    assert listing["items"][0]["ground_temp_c"] == {"min": -73.0, "max": 10.0, "mean": -30.0}
    assert detail["ground_temp_c"] == {"min": -73.0, "max": 10.0, "mean": -30.0}
    assert all("ground_temp_c" in hour for hour in detail["hours"])
    assert detail["hours"][14]["ground_temp_c"] == -10.0
    assert detail["hours"][0]["ground_temp_c"] is None


async def test_the_default_app_behaves_like_flag_on_ac48(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_with_ground(clean_db)

    body = (await client.get(P)).json()

    assert body["parameters"]["ground_temp_c"]["quality"] == "estimate"
    assert body["items"][0]["ground_temp_c"]["max"] == 10.0


async def test_flag_on_keeps_the_other_parameters_and_never_adds_uv_ac48(
    flag_on: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed_with_ground(clean_db)

    body = (await flag_on.get(P)).json()

    assert {"air_temp_c", "pressure_pa", "humidity_pct", "wind", "ground_temp_c"} == set(
        body["parameters"]
    )
    assert "uv_wm2" not in body["items"][0]
    assert body["parameters"]["air_temp_c"]["quality"] == "measured"
    assert body["parameters"]["wind"]["quality"] == "measured"


async def test_flag_on_without_ground_data_in_the_sol_gives_null_ac48(
    flag_on: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(
        clean_db,
        "perseverance",
        100,
        ground_min_k=None,
        ground_max_k=None,
        ground_mean_k=None,
        n_ground=0,
    )

    item = (await flag_on.get(P)).json()["items"][0]

    assert item["ground_temp_c"] is None


async def test_the_flag_does_not_change_curiosity_ac48(
    flag_on: httpx.AsyncClient, flag_off: httpx.AsyncClient, clean_db: Factory
) -> None:
    await seed(clean_db, "curiosity", 30)

    on = await flag_on.get(C)
    off = await flag_off.get(C)

    assert on.json() == off.json()
    assert on.json()["parameters"]["ground_temp_c"]["quality"] == "measured"


async def test_body_cache_follows_the_flag_ac48(
    flag_on: httpx.AsyncClient, flag_off: httpx.AsyncClient, clean_db: Factory
) -> None:
    """Two apps on one database: each answers by its own setting (the cache key has the flag)."""
    await seed_with_ground(clean_db)

    off_first = await flag_off.get(P)
    on_first = await flag_on.get(P)
    off_second = await flag_off.get(P)

    assert "ground_temp_c" not in off_first.json()["items"][0]
    assert "ground_temp_c" in on_first.json()["items"][0]
    assert off_second.content == off_first.content
    assert off_first.headers["etag"] != on_first.headers["etag"]
