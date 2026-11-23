"""v2 weather: ``GET /api/v2/rovers/{rover}/weather[/{sol}]`` (docs/API.md, stage 17)."""

import asyncio
from collections import OrderedDict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic_core import to_json
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.responses import FastJSONResponse
from app.api.v2.params import MAX_SOL, SolPath, StrictInt
from app.api.v2.radiation import _given_once
from app.api.v2.schemas import (
    ErrorResponse,
    WeatherResponse,
    WeatherSolDetail,
    weather_body,
    weather_detail_body,
)
from app.db.session import get_session
from app.services.weather import WeatherData, WeatherService

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "rover_not_found / not_found (rover without weather) / sol_not_found",
    },
    422: {"model": ErrorResponse, "description": "validation_error"},
    503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
}

WeatherSolMin = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="First sol, inclusive."), StrictInt
]
WeatherSolMax = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="Last sol, inclusive."), StrictInt
]

CacheKey = tuple[str, int | None, int | None]


class WeatherBodyCache:
    """Serialized list responses (a full history of Curiosity is ~4 MB) of the last few queries,
    valid while the fingerprint of the stored summaries is the same; they change only when a
    parse or an import is run."""

    def __init__(self, size: int = 8) -> None:
        self._size = size
        self._items: OrderedDict[CacheKey, tuple[Any, bytes]] = OrderedDict()
        # The full list of each rover (built once per fingerprint); ranges are cut from it.
        self._full: dict[str, tuple[Any, WeatherData]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, rover: str) -> asyncio.Lock:
        return self._locks.setdefault(rover, asyncio.Lock())

    def get_full(self, rover: str, fingerprint: Any) -> WeatherData | None:
        item = self._full.get(rover)
        return item[1] if item is not None and item[0] == fingerprint else None

    def put_full(self, rover: str, fingerprint: Any, data: WeatherData) -> None:
        self._full[rover] = (fingerprint, data)

    def get(self, key: CacheKey, fingerprint: Any) -> bytes | None:
        item = self._items.get(key)
        if item is None or item[0] != fingerprint:
            return None
        self._items.move_to_end(key)
        return item[1]

    def put(self, key: CacheKey, fingerprint: Any, body: bytes) -> None:
        self._items[key] = (fingerprint, body)
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


@router.get(
    "/rovers/{rover}/weather",
    response_model=WeatherResponse,
    response_model_exclude_unset=True,
    responses=_ERRORS,
    summary="Daily weather summaries of Curiosity (REMS) and Perseverance (MEDA)",
)
async def get_weather(
    request: Request,
    rover: str,
    session: Session,
    sol_min: WeatherSolMin = None,
    sol_max: WeatherSolMax = None,
) -> Response:
    _given_once(request, "sol_min", "sol_max")
    service = WeatherService(session, request.app.state.settings)
    rover_id = await service.check(rover, sol_min, sol_max)
    cache: WeatherBodyCache = request.app.state.__dict__.setdefault(
        "weather_cache", WeatherBodyCache()
    )
    fingerprint = await service.fingerprint(rover_id)
    full = await _full_list(cache, service, rover_id, fingerprint)
    sol_min, sol_max = _normalise(full, sol_min, sol_max)
    key = (rover_id, sol_min, sol_max)
    body = cache.get(key, fingerprint)
    if body is None:
        body = await asyncio.to_thread(_serialize, full, sol_min, sol_max)
        cache.put(key, fingerprint, body)
    return Response(body, media_type="application/json")


async def _full_list(
    cache: "WeatherBodyCache", service: WeatherService, rover: str, fingerprint: Any
) -> WeatherData:
    """The rover's full list; concurrent misses build it once."""
    full = cache.get_full(rover, fingerprint)
    if full is None:
        async with cache.lock(rover):
            full = cache.get_full(rover, fingerprint)
            if full is None:
                full = await service.list(rover)
                cache.put_full(rover, fingerprint, full)
    return full


def _normalise(
    full: WeatherData, sol_min: int | None, sol_max: int | None
) -> tuple[int | None, int | None]:
    """Bounds outside the data become None (or the nearest empty range), so that they share a
    cache entry: the answer does not change."""
    if not full.items:
        return None, None
    first, last = int(full.items[0]["sol"]), int(full.items[-1]["sol"])
    if sol_min is not None:
        sol_min = None if sol_min <= first else min(sol_min, last + 1)
    if sol_max is not None:
        sol_max = None if sol_max >= last else max(sol_max, first - 1)
    return sol_min, sol_max


def _serialize(full: WeatherData, sol_min: int | None, sol_max: int | None) -> bytes:
    """The JSON of the answer for a sol range, cut from the rover's full list."""
    return to_json(weather_body(full.between(sol_min, sol_max)))


@router.get(
    "/rovers/{rover}/weather/{sol}",
    response_model=WeatherSolDetail,
    response_model_exclude_unset=True,
    responses=_ERRORS,
    summary="Weather of one sol with its 24 hours",
)
async def get_weather_sol(
    request: Request, rover: str, sol: SolPath, session: Session
) -> FastJSONResponse:
    service = WeatherService(session, request.app.state.settings)
    return FastJSONResponse(weather_detail_body(await service.get_sol(rover, sol)))
