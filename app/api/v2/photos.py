"""v2 single photo: /photos/{id} and the image redirect /photos/{id}/image."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ClientKey, get_heavy_query_gate
from app.api.home import request_origin
from app.api.responses import FastJSONResponse
from app.api.v2.params import (
    CameraListParam,
    CameraParam,
    DateMaxParam,
    DateMinParam,
    DriveParam,
    EarthDateParam,
    GoldenHourParam,
    LocalTimeParam,
    MinSizeParam,
    NearParam,
    Pagination,
    PhotoIdPath,
    RadiusKmParam,
    RoversParam,
    SampleTypeParam,
    SiteMaxParam,
    SiteMinParam,
    SiteParam,
    SolMaxParam,
    SolMinParam,
    SolParam,
    SortParam,
)
from app.api.v2.schemas import (
    ErrorResponse,
    PhotoDetailOut,
    PhotoListResponse,
    photo_detail_out,
    photo_list_body,
)
from app.core.overload import HeavyQueryGate
from app.db.session import get_session
from app.services import v2
from app.services.mer_images import MerImageService
from app.services.v2 import DEFAULT_SORT, MultiPhotoQuery

router = APIRouter()


@router.get(
    "/photos",
    response_model=PhotoListResponse,
    responses={
        422: {"model": ErrorResponse, "description": "validation_error"},
        503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
    },
)
async def list_all_photos(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    gate: Annotated[HeavyQueryGate, Depends(get_heavy_query_gate)],
    client: ClientKey,
    pagination: Pagination,
    rovers: RoversParam = None,
    sol: SolParam = None,
    sol_min: SolMinParam = None,
    sol_max: SolMaxParam = None,
    earth_date: EarthDateParam = None,
    date_min: DateMinParam = None,
    date_max: DateMaxParam = None,
    camera: CameraListParam = None,
    sample_type: SampleTypeParam = None,
    site: SiteParam = None,
    drive_from: DriveParam = None,
    drive_to: DriveParam = None,
    site_min: SiteMinParam = None,
    site_max: SiteMaxParam = None,
    sort: SortParam = DEFAULT_SORT,
    local_time_min: LocalTimeParam = None,
    local_time_max: LocalTimeParam = None,
    golden_hour: GoldenHourParam = False,
    min_width: MinSizeParam = None,
    min_height: MinSizeParam = None,
    near: NearParam = None,
    radius_km: RadiusKmParam = None,
) -> FastJSONResponse:
    query = MultiPhotoQuery(
        rovers=rovers,
        sol=sol,
        sol_min=sol_min,
        sol_max=sol_max,
        earth_date=earth_date,
        date_min=date_min,
        date_max=date_max,
        camera=camera,
        sample_type=sample_type,
        site=site,
        drive_from=drive_from,
        drive_to=drive_to,
        site_min=site_min,
        site_max=site_max,
        sort=sort,
        local_time_min=local_time_min,
        local_time_max=local_time_max,
        golden_hour=golden_hour,
        min_width=min_width,
        min_height=min_height,
        near=near,
        radius_km=radius_km,
    )
    result = await v2.search_photos_multi(session, query, pagination, gate=gate, client=client)
    return FastJSONResponse(photo_list_body(result, request_origin(request)))


# Non-integer id → 422; an integer outside 1..2**63-1 → 404 photo_not_found.
@router.get(
    "/photos/{id}",
    response_model=PhotoDetailOut,
    responses={
        404: {"model": ErrorResponse, "description": "photo_not_found"},
        422: {"model": ErrorResponse, "description": "validation_error"},
        503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
    },
)
async def get_photo(
    request: Request,
    id: PhotoIdPath,
    session: Annotated[AsyncSession, Depends(get_session)],
    camera: CameraParam = None,
    sample_type: SampleTypeParam = None,
) -> PhotoDetailOut:
    # camera / sample_type narrow only in_sol; the ETag follows the body, so it differs with them.
    detail = await v2.get_photo_detail(session, id, camera, sample_type)
    return photo_detail_out(detail, request_origin(request))


IMAGE_CACHE_CONTROL = "public, max-age=86400"


@router.get(
    "/photos/{id}/image",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        200: {
            "description": "Spirit/Opportunity full frame converted to JPEG",
            "content": {"image/jpeg": {}},
        },
        302: {"description": "Redirect to the stored image URL (Location header)"},
        404: {"model": ErrorResponse, "description": "photo_not_found"},
        422: {"model": ErrorResponse, "description": "validation_error"},
        502: {"model": ErrorResponse, "description": "upstream_error"},
        503: {
            "model": ErrorResponse,
            "description": "db_unavailable / service_busy / converter_busy",
        },
        504: {"model": ErrorResponse, "description": "upstream_timeout"},
    },
)
async def get_photo_image(
    request: Request,
    id: PhotoIdPath,
    session: Annotated[AsyncSession, Depends(get_session)],
    size: Annotated[
        str,
        Query(
            description=(
                f"One of {', '.join(v2.IMAGE_SIZES)} (case-insensitive); "
                "a size the photo lacks falls back to full."
            )
        ),
    ] = "full",
) -> Response:
    # Location comes only from the database, never from the request.
    target = await v2.resolve_image(session, id, size)
    if target.convert is None:
        url = target.redirect_url
        if url is None:
            raise RuntimeError("Image target has neither a redirect URL nor a photo")
        return RedirectResponse(
            url, status_code=302, headers={"Cache-Control": IMAGE_CACHE_CONTROL}
        )
    # Release the database connection before the long conversion (up to 60 s).
    await session.close()
    service: MerImageService = request.app.state.mer_image_service
    jpeg = await service.get_full_jpeg(target.convert)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": IMAGE_CACHE_CONTROL})
