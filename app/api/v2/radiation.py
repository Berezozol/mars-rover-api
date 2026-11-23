"""v2 radiation: ``GET /api/v2/rovers/{rover}/radiation`` (docs/API.md, stage 21)."""

from collections import OrderedDict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic_core import to_json
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.params import MAX_SOL, StrictInt
from app.api.v2.schemas import ErrorResponse, RadiationResponse, radiation_body
from app.core.errors import InvalidParam
from app.db.session import get_session
from app.services.radiation import RadiationService

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "rover_not_found / not_found (rover without RAD)"},
    422: {"model": ErrorResponse, "description": "validation_error"},
    503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
}

RadSolMin = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="First sol, inclusive."), StrictInt
]
RadSolMax = Annotated[
    int | None, Query(ge=0, le=MAX_SOL, description="Last sol, inclusive."), StrictInt
]


class RadiationBodyCache:
    """Serialized responses (a full history is ~2.4 MB) of the last few queries, valid while the
    fingerprint of the stored summaries is the same; they change about once a month."""

    def __init__(self, size: int = 8) -> None:
        self._size = size
        self._items: OrderedDict[tuple[int | None, int | None], tuple[Any, bytes]] = OrderedDict()

    def get(self, key: tuple[int | None, int | None], fingerprint: Any) -> bytes | None:
        item = self._items.get(key)
        if item is None or item[0] != fingerprint:
            return None
        self._items.move_to_end(key)
        return item[1]

    def put(self, key: tuple[int | None, int | None], fingerprint: Any, body: bytes) -> None:
        self._items[key] = (fingerprint, body)
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


def _given_once(request: Request, *names: str) -> None:
    """A repeated parameter is rejected rather than reduced to its last value."""
    for name in names:
        if len(request.query_params.getlist(name)) > 1:
            raise InvalidParam(name, f"{name} must be given once")


@router.get(
    "/rovers/{rover}/radiation",
    response_model=RadiationResponse,
    responses=_ERRORS,
    summary="Daily radiation summaries of Curiosity (MSL RAD)",
)
async def get_radiation(
    request: Request,
    rover: str,
    session: Session,
    sol_min: RadSolMin = None,
    sol_max: RadSolMax = None,
) -> Response:
    _given_once(request, "sol_min", "sol_max")
    service = RadiationService(session)
    await service.check(rover, sol_min, sol_max)
    cache: RadiationBodyCache = request.app.state.__dict__.setdefault(
        "rad_cache", RadiationBodyCache()
    )
    key, fingerprint = (sol_min, sol_max), await service.fingerprint()
    body = cache.get(key, fingerprint)
    if body is None:
        data = await service.list(rover, sol_min, sol_max)
        body = to_json(radiation_body(data, _source_url(request)))
        cache.put(key, fingerprint, body)
    return Response(body, media_type="application/json")


def _source_url(request: Request) -> str:
    return request.app.state.settings.rad_base_url.rstrip("/") + "/"
