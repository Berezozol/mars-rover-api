"""Add photos.feed_at: when the regular sync first stored a photo (RSS feed, stage 16).

``feed_at`` equals ``created_at`` for photos inserted by an incremental sync run and is NULL for
everything else (backfill, MER import, rows older than the feed window). The last
30 days (the default ``FEED_WINDOW_DAYS`` at migration time, hard-coded) are filled from the
incremental runs of ``sync_runs``: a rover is
locked during a run, so a photo created inside an incremental run's window came from that run.
The partial index ``ix_photos_feed`` is built and dropped CONCURRENTLY outside the migration
transaction. If the build fails it leaves an INVALID index: drop it (``DROP INDEX CONCURRENTLY
ix_photos_feed``) and upgrade again.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_photos_feed"

_FILL = """
UPDATE photos p SET feed_at = p.created_at
WHERE p.created_at >= now() - interval '30 days'
  AND EXISTS (
    SELECT 1 FROM sync_runs r
    WHERE r.rover_id = p.rover_id
      AND r.mode = 'incremental'
      AND p.created_at BETWEEN r.started_at AND coalesce(r.finished_at, now())
  )
"""


def upgrade() -> None:
    op.add_column("photos", sa.Column("feed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text(_FILL))
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "photos",
            ["rover_id", sa.text("feed_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_include=["camera_id", "sample_type", "sol", "taken_at"],
            postgresql_where=sa.text("feed_at IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name="photos", postgresql_concurrently=True)
    op.drop_column("photos", "feed_at")
