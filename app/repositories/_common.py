"""Helpers shared by repositories."""

from typing import Any

from sqlalchemy import Row, Select

# Upserts go through Core and bypass the identity map, so ORM reads refresh
# objects that are already loaded in the session.
FRESH: dict[str, Any] = {"populate_existing": True}

# SQLAlchemy 2.1 types a statement by its column tuple, so ``Select[Any]`` only matches a
# one-column select. These aliases mean "any number of columns".
type AnySelect = Select[*tuple[Any, ...]]
type AnyRow = Row[*tuple[Any, ...]]
