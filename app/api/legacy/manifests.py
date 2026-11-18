"""v1 photo manifest: /manifests/{rover}."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.legacy.schemas import ManifestResponse, manifest_dict
from app.api.responses import FastJSONResponse
from app.db.session import get_session
from app.services import legacy

router = APIRouter()


@router.get("/manifests/{rover}", response_model=ManifestResponse)
async def get_manifest(
    rover: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FastJSONResponse:
    manifest = await legacy.get_manifest(session, rover)
    return FastJSONResponse({"photo_manifest": manifest_dict(manifest)})
