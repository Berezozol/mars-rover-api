"""Liveness and database health check."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return JSONResponse({"status": "degraded", "db": "error"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok"})
