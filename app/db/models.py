"""ORM models. Tables arrive in stage 1 (see docs/ARCHITECTURE.md, "Модель данных").

Alembic imports this module, so every model must be defined or imported here.
"""

from app.db.base import Base

__all__ = ["Base"]
