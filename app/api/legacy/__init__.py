"""API v1, compatible with the archived Mars Rover Photos API (docs/LEGACY-API.md).

Endpoints arrive in stage 4. The same router is mounted under /api/v1 and /mars-photos/api/v1.
"""

from fastapi import APIRouter

router = APIRouter(tags=["v1 (legacy compatible)"])
