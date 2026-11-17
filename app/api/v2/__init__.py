"""API v2, the project's own API (docs/API.md). Endpoints arrive in stage 5."""

from fastapi import APIRouter

router = APIRouter(tags=["v2"])
