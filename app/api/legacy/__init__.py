"""API v1, compatible with the archived Mars Rover Photos API (docs/LEGACY-API.md).

The same router is mounted under /api/v1 and /mars-photos/api/v1. ``api_key`` is not
declared anywhere, so it is accepted and ignored.
"""

from fastapi import APIRouter

from app.api.legacy import manifests, photos, rovers

router = APIRouter(tags=["v1 (legacy compatible)"])
router.include_router(rovers.router)
router.include_router(photos.router)
router.include_router(manifests.router)
