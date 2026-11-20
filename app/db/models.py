"""ORM models (see docs/ARCHITECTURE.md, "Модель данных").

Alembic imports this module, so every model must be defined or imported here.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

__all__ = [
    "Base",
    "Camera",
    "CameraSolCount",
    "LocationDataset",
    "Photo",
    "Rover",
    "RoverTraverseSegment",
    "RoverWaypoint",
    "SolSourceCount",
    "SolSummary",
    "SyncRun",
]

_TIMESTAMPTZ = DateTime(timezone=True)


class Rover(Base):
    __tablename__ = "rovers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    legacy_id: Mapped[int] = mapped_column(SmallInteger, unique=True)
    name: Mapped[str] = mapped_column(Text)
    mission: Mapped[str] = mapped_column(Text)
    launch_date: Mapped[date] = mapped_column(Date)
    landing_date: Mapped[date] = mapped_column(Date)
    # Official end of the mission; NULL for active rovers.
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    max_sol: Mapped[int | None] = mapped_column(Integer)
    max_date: Mapped[date | None] = mapped_column(Date)
    total_photos: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_synced_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    full_max_sol: Mapped[int | None] = mapped_column(Integer)
    full_max_date: Mapped[date | None] = mapped_column(Date)
    full_total_photos: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("rover_id", "code", name="uq_cameras_rover_id_code"),
        CheckConstraint("code = upper(code)", name="code_upper"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        UniqueConstraint("rover_id", "source_id", name="uq_photos_rover_id_source_id"),
        CheckConstraint("sample_type = lower(sample_type)", name="sample_type_lower"),
        # Newest-first listing (v2).
        Index(
            "ix_photos_rover_id_sol_taken_at",
            "rover_id",
            text("sol DESC"),
            text("taken_at DESC"),
            text("id DESC"),
        ),
        # Filter by sample_type without sol (v2): newest-first listing and count.
        Index(
            "ix_photos_rover_id_sample_type_sol",
            "rover_id",
            "sample_type",
            text("sol DESC"),
            text("taken_at DESC"),
            text("id DESC"),
        ),
        # Filter by real earth_date (v2).
        Index("ix_photos_rover_id_earth_date", "rover_id", "earth_date"),
        # Filter by camera (v2).
        Index("ix_photos_rover_id_camera_id_sol", "rover_id", "camera_id", "sol"),
        # v1 serves full-frame photos only, ordered by camera then id.
        Index(
            "ix_photos_full_rover_id_sol_camera_id_id",
            "rover_id",
            "sol",
            "camera_id",
            "id",
            postgresql_where=text("sample_type = 'full'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"))
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    source_id: Mapped[str] = mapped_column(Text)
    sol: Mapped[int] = mapped_column(Integer)
    earth_date: Mapped[date] = mapped_column(Date)
    taken_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ)
    received_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    sample_type: Mapped[str] = mapped_column(Text)
    img_full: Mapped[str] = mapped_column(Text)
    img_small: Mapped[str | None] = mapped_column(Text)
    img_medium: Mapped[str | None] = mapped_column(Text)
    img_large: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    credit: Mapped[str | None] = mapped_column(Text)
    source_link: Mapped[str | None] = mapped_column(Text)
    site: Mapped[int | None] = mapped_column(Integer)
    drive: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TIMESTAMPTZ, server_default=func.now(), onupdate=func.now()
    )

    # Always load explicitly (selectinload/joinedload); lazy loads do not work in async.
    camera: Mapped[Camera] = relationship(lazy="raise")


class SolSummary(Base):
    __tablename__ = "sol_summaries"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    sol: Mapped[int] = mapped_column(Integer, primary_key=True)
    earth_date: Mapped[date] = mapped_column(Date)
    total_photos: Mapped[int] = mapped_column(Integer)
    cameras: Mapped[list[str]] = mapped_column(ARRAY(Text))
    full_photos: Mapped[int] = mapped_column(Integer)
    full_cameras: Mapped[list[str]] = mapped_column(ARRAY(Text))


class CameraSolCount(Base):
    """Photos per (sol, camera), recomputed by the sync with sol_summaries; no zero rows."""

    __tablename__ = "camera_sol_counts"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    sol: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), primary_key=True)
    total_photos: Mapped[int] = mapped_column(Integer)
    full_photos: Mapped[int] = mapped_column(Integer)


class SolSourceCount(Base):
    """What the source returned for a sol in the latest fetch (overwritten by every fetch)."""

    __tablename__ = "sol_source_counts"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    sol: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Records the source returned for the sol.
    received: Mapped[int] = mapped_column(Integer)
    # Received but not stored: thumbnails, failed normalization, duplicates, wrong sol.
    dropped: Mapped[int] = mapped_column(Integer)
    # Per-sol total declared by the source, when the feed exposes one.
    source_total: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('incremental', 'backfill')", name="mode"),
        CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name="status"),
        Index("ix_sync_runs_rover_id_started_at", "rover_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"))
    mode: Mapped[str] = mapped_column(Text)
    from_sol: Mapped[int | None] = mapped_column(Integer)
    to_sol: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    photos_upserted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    # Sols this run could not sync; the next incremental run starts low enough to retry them.
    failed_sols: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'::integer[]")
    )
    # Total the source declared for the run's range, when known.
    source_total: Mapped[int | None] = mapped_column(BigInteger)


class RoverWaypoint(Base):
    """A rover stop from the MMGIS waypoints file; replaced as a whole per rover."""

    __tablename__ = "rover_waypoints"
    __table_args__ = (
        # /location with a sol filter, in driving order.
        Index("ix_rover_waypoints_rover_id_sol", "rover_id", "sol", "site", "drive"),
    )

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    site: Mapped[int] = mapped_column(Integer, primary_key=True)
    drive: Mapped[int] = mapped_column(Integer, primary_key=True)
    sol: Mapped[int] = mapped_column(Integer)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elev_geoid: Mapped[float | None] = mapped_column(Float)
    easting: Mapped[float | None] = mapped_column(Float)
    northing: Mapped[float | None] = mapped_column(Float)
    roll: Mapped[float | None] = mapped_column(Float)
    pitch: Mapped[float | None] = mapped_column(Float)
    yaw: Mapped[float | None] = mapped_column(Float)
    tilt: Mapped[float | None] = mapped_column(Float)
    dist_m: Mapped[float | None] = mapped_column(Float)
    dist_total_m: Mapped[float | None] = mapped_column(Float)


class RoverTraverseSegment(Base):
    """One LineString of the route; ``seq`` is the driving order, always read as a whole."""

    __tablename__ = "rover_traverse_segments"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL when the source has no sol (always for Curiosity).
    sol: Mapped[int | None] = mapped_column(Integer)
    length_m: Mapped[float | None] = mapped_column(Float)
    # [[lon, lat], ...], at least two points.
    coordinates: Mapped[list[list[float]]] = mapped_column(JSONB)


class LocationDataset(Base):
    """State of one MMGIS file (per rover and kind): last checks, validators, counts."""

    __tablename__ = "location_datasets"
    __table_args__ = (CheckConstraint("kind IN ('waypoints', 'traverse')", name="kind"),)

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    # Last attempt / last successful check (304, same content, or an accepted new file).
    checked_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    succeeded_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # When the content last changed (``updated_at`` in the API).
    changed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    dropped: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_sol: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Alerts count a file that never succeeded from this moment.
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
