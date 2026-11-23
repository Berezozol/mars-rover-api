"""API v2, the project's own API (docs/API.md).

Unknown query parameters are ignored. The admin router (app.api.v2.admin) is mounted by
create_app only when ADMIN_TOKEN is set.
"""

from fastapi import APIRouter

from app.api.v2 import feeds, photos, radiation, rovers, weather

router = APIRouter(tags=["v2"])
router.include_router(rovers.router)
router.include_router(radiation.router)
router.include_router(weather.router)
router.include_router(feeds.router)
router.include_router(photos.router)
