"""v2 single photo: /photos/{id}. The image redirect arrives in stage 6."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.params import PhotoIdPath
from app.api.v2.schemas import ErrorResponse, PhotoDetailOut, photo_detail_out
from app.db.session import get_session
from app.services import v2

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
    id: PhotoIdPath,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PhotoDetailOut:
    return photo_detail_out(await v2.get_photo(session, id))
