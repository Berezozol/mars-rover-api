"""Helpers shared by repositories."""

from typing import Any

# Upserts go through Core and bypass the identity map, so ORM reads refresh
# objects that are already loaded in the session.
FRESH: dict[str, Any] = {"populate_existing": True}
