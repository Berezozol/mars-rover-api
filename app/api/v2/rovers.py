"""v2 rovers: list, detail, cameras, photos, latest photos, manifest, location and traverse."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.home import request_origin
from app.api.responses import FastJSONResponse, GeoJSONResponse
from app.api.v2.params import (
    CameraParam,
    DriveParam,
    EarthDateParam,
    FlightNumberPath,
    LatestParam,
    Pagination,
    SampleTypeParam,
    SiteParam,
    SolParam,
)
from app.api.v2.schemas import (
    CamerasResponse,
    ErrorResponse,
    FlightOut,
    FlightsResponse,
    LatestPhotosResponse,
    LocationResponse,
    ManifestResponse,
    PhotosResponse,
    RoverOut,
    RoversResponse,
    TraverseResponse,
    camera_out,
    flight_dict,
    flights_body,
    latest_body,
    location_body,
    manifest_body,
    photos_body,
    rover_dict,
    traverse_body,
)
from app.db.session import get_session
from app.services import locations, v2
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
async def list_cameras(rover: str, session: Session, sol: SolParam = None) -> CamerasResponse:
    cameras = await v2.list_cameras(session, rover, sol)
    return CamerasResponse(items=[camera_out(info) for info in cameras])


@router.get("/rovers/{rover}/photos", response_model=PhotosResponse, responses=_ERRORS)
async def list_photos(
    request: Request,
    rover: str,
    session: Session,
    pagination: Pagination,
    sol: SolParam = None,
    earth_date: EarthDateParam = None,
    camera: CameraParam = None,
    sample_type: SampleTypeParam = None,
    site: SiteParam = None,
    drive_from: DriveParam = None,
    drive_to: DriveParam = None,
) -> FastJSONResponse:
    query = PhotoQuery(
        sol=sol,
        earth_date=earth_date,
        camera=camera,
        sample_type=sample_type,
        site=site,
        drive_from=drive_from,
        drive_to=drive_to,
    )
    return FastJSONResponse(
        photos_body(
            await v2.search_photos(session, rover, query, pagination), request_origin(request)
        )
    )


@router.get("/rovers/{rover}/photos/latest", response_model=LatestPhotosResponse, responses=_ERRORS)
async def latest_photos(
    request: Request,
    rover: str,
    session: Session,
    pagination: Pagination,
    camera: CameraParam = None,
    sample_type: SampleTypeParam = None,
) -> FastJSONResponse:
    result = await v2.latest_photos(session, rover, camera, pagination, sample_type)
    return FastJSONResponse(latest_body(result, request_origin(request)))


@router.get("/rovers/{rover}/manifest", response_model=ManifestResponse, responses=_ERRORS)
async def get_manifest(
    rover: str,
    session: Session,
    from_sol: SolParam = None,
    to_sol: SolParam = None,
) -> FastJSONResponse:
    return FastJSONResponse(manifest_body(await v2.get_manifest(session, rover, from_sol, to_sol)))


@router.get("/rovers/{rover}/flights", response_model=FlightsResponse, responses=_ERRORS)
async def list_flights(rover: str, session: Session) -> FastJSONResponse:
    return FastJSONResponse(flights_body(await v2.list_flights(session, rover)))


@router.get("/rovers/{rover}/flights/{n}", response_model=FlightOut, responses=_ERRORS)
async def get_flight(rover: str, n: FlightNumberPath, session: Session) -> FastJSONResponse:
    flight, total_photos = await v2.get_flight(session, rover, n)
    return FastJSONResponse(flight_dict(flight, total_photos))


@router.get("/rovers/{rover}/location", response_model=LocationResponse, responses=_ERRORS)
async def get_location(
    rover: str,
    session: Session,
    sol: SolParam = None,
    from_sol: SolParam = None,
    to_sol: SolParam = None,
    latest: LatestParam = False,
) -> FastJSONResponse:
    result = await locations.get_location(session, rover, sol, from_sol, to_sol, latest)
    return FastJSONResponse(location_body(result))


@router.get(
    "/rovers/{rover}/traverse",
    response_model=TraverseResponse,
    responses={
        # The body is sent as application/geo+json; the plain JSON entry is added by FastAPI.
        200: {
            "description": "GeoJSON FeatureCollection (RFC 7946)",
            "content": {
                "application/geo+json": {
                    "schema": {"$ref": "#/components/schemas/TraverseResponse"}
                }
            },
        },
        **_ERRORS,
    },
)
async def get_traverse(rover: str, session: Session) -> GeoJSONResponse:
    return GeoJSONResponse(traverse_body(await locations.get_traverse(session, rover)))
