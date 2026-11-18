"""v2 rovers: list, detail, cameras, photos, latest photos and manifest."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.responses import FastJSONResponse
from app.api.v2.params import (
    CameraParam,
    EarthDateParam,
    Pagination,
    SampleTypeParam,
    SolParam,
)
from app.api.v2.schemas import (
    CamerasResponse,
    ErrorResponse,
    LatestPhotosResponse,
    ManifestResponse,
    PhotosResponse,
    RoverOut,
    RoversResponse,
    camera_out,
    latest_body,
    manifest_body,
    photos_body,
    rover_dict,
)
from app.db.session import get_session
from app.services import v2
from app.services.v2 import PhotoQuery

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "db_unavailable"}
}
# Declaring 422 also replaces FastAPI's default HTTPValidationError schema.
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "rover_not_found"},
    422: {"model": ErrorResponse, "description": "validation_error"},
    **_UNAVAILABLE,
}


@router.get("/rovers", response_model=RoversResponse, responses=_UNAVAILABLE)
async def list_rovers(session: Session) -> FastJSONResponse:
    rovers = await v2.list_rovers(session)
    return FastJSONResponse({"items": [rover_dict(info) for info in rovers]})


@router.get("/rovers/{rover}", response_model=RoverOut, responses=_ERRORS)
async def get_rover(rover: str, session: Session) -> FastJSONResponse:
    return FastJSONResponse(rover_dict(await v2.get_rover(session, rover)))


@router.get("/rovers/{rover}/cameras", response_model=CamerasResponse, responses=_ERRORS)
async def list_cameras(rover: str, session: Session) -> CamerasResponse:
    cameras = await v2.list_cameras(session, rover)
    return CamerasResponse(items=[camera_out(info) for info in cameras])


@router.get("/rovers/{rover}/photos", response_model=PhotosResponse, responses=_ERRORS)
async def list_photos(
    rover: str,
    session: Session,
    pagination: Pagination,
    sol: SolParam = None,
    earth_date: EarthDateParam = None,
    camera: CameraParam = None,
    sample_type: SampleTypeParam = None,
) -> FastJSONResponse:
    query = PhotoQuery(sol=sol, earth_date=earth_date, camera=camera, sample_type=sample_type)
    return FastJSONResponse(photos_body(await v2.search_photos(session, rover, query, pagination)))


@router.get("/rovers/{rover}/photos/latest", response_model=LatestPhotosResponse, responses=_ERRORS)
async def latest_photos(
    rover: str,
    session: Session,
    pagination: Pagination,
    camera: CameraParam = None,
) -> FastJSONResponse:
    return FastJSONResponse(latest_body(await v2.latest_photos(session, rover, camera, pagination)))


@router.get("/rovers/{rover}/manifest", response_model=ManifestResponse, responses=_ERRORS)
async def get_manifest(
    rover: str,
    session: Session,
    from_sol: SolParam = None,
    to_sol: SolParam = None,
) -> FastJSONResponse:
    return FastJSONResponse(manifest_body(await v2.get_manifest(session, rover, from_sol, to_sol)))
