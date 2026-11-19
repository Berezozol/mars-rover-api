"""v2 single photo: /photos/{id} and the image redirect /photos/{id}/image."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.home import request_origin
from app.api.v2.params import PhotoIdPath
from app.api.v2.schemas import ErrorResponse, PhotoDetailOut, photo_detail_out
from app.db.session import get_session
from app.services import v2
from app.services.mer_images import MerImageService

router = APIRouter()


# Non-integer id → 422; an integer outside 1..2**63-1 → 404 photo_not_found.
@router.get(
    "/photos/{id}",
    response_model=PhotoDetailOut,
    responses={
        404: {"model": ErrorResponse, "description": "photo_not_found"},
        422: {"model": ErrorResponse, "description": "validation_error"},
        503: {"model": ErrorResponse, "description": "db_unavailable"},
    },
)
async def get_photo(
    request: Request,
    id: PhotoIdPath,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PhotoDetailOut:
    return photo_detail_out(await v2.get_photo(session, id), request_origin(request))


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
        503: {"model": ErrorResponse, "description": "db_unavailable / converter_busy"},
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
