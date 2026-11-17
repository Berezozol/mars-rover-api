import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def test_migrations_reach_head_revision(migrated_db: str) -> None:
    engine = create_async_engine(migrated_db)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar_one()
    finally:
        await engine.dispose()

    assert version == "0001"
