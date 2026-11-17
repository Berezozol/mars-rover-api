"""v1 single photo: /photos/{id}."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.legacy.schemas import PhotoResponse, photo_out
from app.db.session import get_session
from app.services import legacy

router = APIRouter()


# Read as a string and parsed by the service: lax int parsing would accept "1.0".
# A non-integer gives {"errors": "Invalid id"}.
@router.get("/photos/{id}", response_model=PhotoResponse)
async def get_photo(
    id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PhotoResponse:
    result = await legacy.get_photo(session, id)
    return PhotoResponse(photo=photo_out(result.rover, result.photo))
