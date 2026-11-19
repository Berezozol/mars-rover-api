"""Rover lookup by slug shared by the v2 services."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RoverNotFound
from app.db.models import Rover
from app.repositories import rovers as rovers_repo

# Rover slugs are Latin letters only; anything else never reaches the database.
_ROVER_SLUG_RE = re.compile(r"[A-Za-z]+")


async def find_rover(session: AsyncSession, slug: str) -> Rover:
    """Validate the slug before any query; unknown or malformed -> ``RoverNotFound``."""
    if not _ROVER_SLUG_RE.fullmatch(slug):
        raise RoverNotFound(slug)
    rover = await rovers_repo.get_rover(session, slug)
    if rover is None:
        raise RoverNotFound(slug)
    return rover
