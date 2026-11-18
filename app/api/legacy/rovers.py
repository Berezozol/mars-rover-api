"""v1 rovers: /rovers, /rovers/{rover}, /rovers/{rover}/photos, /rovers/{rover}/latest_photos."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.legacy.schemas import (
    LatestPhotosResponse,
    PhotosResponse,
    RoverResponse,
    RoversResponse,
    photos_dicts,
    rover_dict,
)
from app.api.responses import FastJSONResponse
from app.db.session import get_session
from app.services import legacy

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]
# Raw strings: bad values must produce the exact legacy 400 texts (see app.core.legacy_params).
OptionalParam = Annotated[str | None, Query()]


@router.get("/rovers", response_model=RoversResponse)
async def list_rovers(session: Session) -> FastJSONResponse:
    rovers = await legacy.list_rovers(session)
    return FastJSONResponse({"rovers": [rover_dict(item) for item in rovers]})


@router.get("/rovers/{rover}", response_model=RoverResponse)
async def get_rover(rover: str, session: Session) -> FastJSONResponse:
    return FastJSONResponse({"rover": rover_dict(await legacy.get_rover(session, rover))})


@router.get("/rovers/{rover}/photos", response_model=PhotosResponse)
async def list_photos(
    rover: str,
    session: Session,
    sol: OptionalParam = None,
    earth_date: OptionalParam = None,
    camera: OptionalParam = None,
    page: OptionalParam = None,
    per_page: OptionalParam = None,
) -> FastJSONResponse:
    result = await legacy.search_photos(
        session,
        rover,
        sol=sol,
        earth_date=earth_date,
        camera=camera,
        page=page,
        per_page=per_page,
    )
    return FastJSONResponse({"photos": photos_dicts(result.rover, result.photos)})


@router.get("/rovers/{rover}/latest_photos", response_model=LatestPhotosResponse)
async def latest_photos(
    rover: str,
    session: Session,
    camera: OptionalParam = None,
    page: OptionalParam = None,
    per_page: OptionalParam = None,
) -> FastJSONResponse:
    result = await legacy.latest_photos(session, rover, camera=camera, page=page, per_page=per_page)
    return FastJSONResponse({"latest_photos": photos_dicts(result.rover, result.photos)})
