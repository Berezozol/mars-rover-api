"""API v2, the project's own API (docs/API.md).

Image redirect and admin endpoints arrive in stage 6. Unknown query parameters are ignored.
"""

from fastapi import APIRouter

from app.api.v2 import photos, rovers

router = APIRouter(tags=["v2"])
router.include_router(rovers.router)
router.include_router(photos.router)
