"""Helpers shared by the upstream (source adapter / HTTP client) unit tests."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "upstream"

M2020_URL = "https://mars.nasa.gov/rss/api/"
MSL_URL = "https://mars.nasa.gov/api/v1/raw_image_items/"
MSL_ORDER = "sol desc,instrument_sort asc,sample_type_sort asc, date_taken desc"
USER_AGENT = "mars-rover-api-tests/0.0 (unit tests)"


def load_fixture(relative: str) -> Any:
    """Load a recorded or synthetic upstream fixture (a fresh deep copy every time)."""
    with (FIXTURES / relative).open(encoding="utf-8") as fh:
        return json.load(fh)


def by_id(items: list[dict[str, Any]], key: str, value: Any) -> dict[str, Any]:
    matches = [item for item in items if item.get(key) == value]
    assert len(matches) == 1, f"fixture must contain exactly one {key}={value!r}"
    return copy.deepcopy(matches[0])


def make_settings(**overrides: Any) -> Settings:
    """Settings for upstream tests: no .env, no rate limit, fast deterministic retries."""
    values: dict[str, Any] = {
        "upstream_user_agent": USER_AGENT,
        "upstream_timeout_seconds": 12.5,
        "upstream_max_rps": 0,
        "upstream_max_retries": 4,
        "upstream_retry_base_seconds": 1.0,
        "upstream_max_pages": 200,
        "m2020_feed_url": M2020_URL,
        "msl_feed_url": MSL_URL,
        "m2020_page_size": 100,
        "msl_page_size": 200,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeClock:
    """Monotonic clock + async sleep that never waits; records every sleep."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds > 0:
            self.now += seconds


def query(request: httpx.Request) -> dict[str, str]:
    """Query parameters of a captured request as a flat dict (last value wins)."""
    return dict(request.url.params.multi_items())


def paged_responder(
    pages: dict[int, Any], key: Callable[[httpx.Request], Any] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """respx side effect returning ``pages[<page param>]`` (or ``pages[key(request)]``)."""

    def responder(request: httpx.Request) -> httpx.Response:
        k = key(request) if key else int(query(request)["page"])
        if k not in pages:
            return httpx.Response(500, json={"error": f"unexpected page {k!r} in test"})
        return httpx.Response(200, json=pages[k])

    return responder
