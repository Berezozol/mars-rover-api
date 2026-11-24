"""ORM models (see docs/ARCHITECTURE.md, "Модель данных").

Alembic imports this module, so every model must be defined or imported here.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
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
    "ApiAccount",
    "ApiKey",
    "ApiKeyRequest",
    "ApiKeyUsageDaily",
    "Base",
    "Camera",
    "CameraSolCount",
    "IngenuityFlight",
    "LocationDataset",
    "Photo",
    "RadDaily",
    "RadDataset",
    "RadFile",
    "Rover",
    "RoverSample",
    "RoverTraverseSegment",
    "RoverWaypoint",
    "SampleDataset",
    "SolSourceCount",
    "SolSummary",
    "SyncRun",
    "WeatherDaily",
    "WeatherDataset",
    "WeatherFile",
    "WeatherHourly",
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
        # Order by capture time and date ranges across rovers (v2, stage 18B).
        Index("ix_photos_rover_id_taken_at", "rover_id", text("taken_at DESC"), text("id DESC")),
        # Filter by real earth_date (v2).
        Index("ix_photos_rover_id_earth_date", "rover_id", "earth_date"),
        # Filter by stop (site, drive range) (v2) and the near search, which reads the sort keys
        # from it (index-only scan); photos without a position are not indexed.
        Index(
            "ix_photos_near_cover",
            "rover_id",
            "site",
            "drive",
            postgresql_include=["sol", "taken_at", "id", "sample_type", "camera_id"],
            postgresql_where=text("site IS NOT NULL"),
        ),
        # Filter by local solar time and frame width (v2, stage 18B-2); NULLs are not indexed.
        Index(
            "ix_photos_rover_id_local_time",
            "rover_id",
            "local_time_s",
            postgresql_where=text("local_time_s IS NOT NULL"),
        ),
        Index(
            "ix_photos_rover_id_width",
            "rover_id",
            "width",
            postgresql_where=text("width IS NOT NULL"),
        ),
        Index(
            "ix_photos_rover_id_height",
            "rover_id",
            "height",
            postgresql_where=text("height IS NOT NULL"),
        ),
        # Filter by camera (v2).
        Index("ix_photos_rover_id_camera_id_sol", "rover_id", "camera_id", "sol"),
        # RSS feed (stage 16): photos stored by the regular sync, newest first.
        Index(
            "ix_photos_feed",
            "rover_id",
            text("feed_at DESC"),
            text("id DESC"),
            postgresql_include=["camera_id", "sample_type", "sol", "taken_at"],
            postgresql_where=text("feed_at IS NOT NULL"),
        ),
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
    # Filter columns derived from ``extra`` by ``photo_meta.stored_meta`` (stage 18B-2); the API
    # still reads the values it returns from ``extra``. local_time_s: second of the local solar
    # day (0-86399; LMST or LTST by rover); width / height: full frame in pixels.
    local_time_s: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(SmallInteger)
    height: Mapped[int | None] = mapped_column(SmallInteger)
    # Set (= created_at) only when the regular (incremental) sync inserts the photo; the upsert
    # never changes it. NULL for backfill and imports. Feeds the RSS feed (stage 16).
    feed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)

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


class IngenuityFlight(Base):
    """One flight of the Ingenuity helicopter from NASA's flight log (seeded by migration 0008)."""

    __tablename__ = "ingenuity_flights"
    __table_args__ = (CheckConstraint("flight > 0", name="flight_positive"),)

    flight: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    # Sol of Perseverance's mission clock; unique, it links a flight to photos.
    sol: Mapped[int] = mapped_column(Integer, unique=True)
    # Pacific time date, as in the log (not UTC).
    date: Mapped[date] = mapped_column(Date)
    distance_m: Mapped[int] = mapped_column(Integer)
    max_altitude_m: Mapped[int] = mapped_column(Integer)
    max_groundspeed_mps: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    duration_s: Mapped[Decimal] = mapped_column(Numeric(6, 1))
    route_from: Mapped[str] = mapped_column(Text)
    route_to: Mapped[str | None] = mapped_column(Text)


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


class RoverSample(Base):
    """A sample row of the NASA "Mars Rock Samples" table; replaced as a whole per rover."""

    __tablename__ = "rover_samples"
    __table_args__ = (
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("status IN ('on_rover', 'depot')", name="status"),
        CheckConstraint(
            "sample_type IN ('rock_core', 'regolith', 'atmospheric')", name="sample_type"
        ),
        CheckConstraint("sol >= 0", name="sol_non_negative"),
    )

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    # Order number on the page, not a tube serial number.
    number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    feature_name: Mapped[str | None] = mapped_column(Text)
    sampling_location: Mapped[str | None] = mapped_column(Text)
    current_location: Mapped[str | None] = mapped_column(Text)
    # Parsed from current_location; NULL for an unknown value.
    status: Mapped[str | None] = mapped_column(Text)
    sample_type: Mapped[str | None] = mapped_column(Text)
    sample_type_label: Mapped[str | None] = mapped_column(Text)
    rock_type: Mapped[str | None] = mapped_column(Text)
    sealed_on: Mapped[date | None] = mapped_column(Date)
    sol: Mapped[int | None] = mapped_column(Integer)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    height_note: Mapped[str | None] = mapped_column(Text)
    dossier_url: Mapped[str | None] = mapped_column(Text)


class SampleDataset(Base):
    """State of the samples page (one row per rover): last checks, content hash, count."""

    __tablename__ = "sample_datasets"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    # Last attempt / last successful check (304, same content, or an accepted new page).
    checked_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    succeeded_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # When the rows last changed (``updated_at`` in the API).
    changed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # ``article:modified_time`` of the page.
    source_modified_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    # sha256 of the canonical JSON of the parsed rows (the page body is not stable).
    content_sha256: Mapped[str | None] = mapped_column(Text)
    items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)


class ApiAccount(Base):
    """An API key account (stage 20): the unit of the rate limit; e-mail is stored lowercase."""

    __tablename__ = "api_accounts"
    __table_args__ = (
        CheckConstraint("length(email) <= 254", name="email_length"),
        CheckConstraint("status IN ('active', 'blocked')", name="status_valid"),
        CheckConstraint("length(block_reason) <= 500", name="block_reason_length"),
        CheckConstraint("length(note) <= 500", name="note_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    blocked_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    block_reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class ApiKey(Base):
    """A key of an account; only its SHA-256 and a display prefix are stored."""

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("octet_length(key_hash) = 32", name="key_hash_length"),
        CheckConstraint(
            "revoke_reason IN ('owner', 'admin', 'replaced')", name="revoke_reason_valid"
        ),
        # Admin search by key prefix (LIKE 'si_...%'): needs the pattern ops class.
        Index("ix_api_keys_prefix", "prefix", postgresql_ops={"prefix": "text_pattern_ops"}),
        Index("ix_api_keys_account_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("api_accounts.id", ondelete="CASCADE"))
    key_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    revoked_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    revoke_reason: Mapped[str | None] = mapped_column(Text)


class ApiKeyRequest(Base):
    """A mail request (stage 20B): a row of the outbox. It is ``pending`` until the sender
    mails the link; then it holds the SHA-256 of the token and its expiry (the token itself is
    never stored). ``purpose`` is ``key`` (issue a key) or ``delete`` (delete the account)."""

    __tablename__ = "api_key_requests"
    __table_args__ = (
        CheckConstraint("purpose IN ('key', 'delete')", name="purpose_valid"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped', 'dropped')", name="status_valid"
        ),
        CheckConstraint(
            "status <> 'sent' OR (token_hash IS NOT NULL AND expires_at IS NOT NULL "
            "AND sent_at IS NOT NULL)",
            name="sent_complete",
        ),
        Index("ix_api_key_requests_email_created_at", "email", "created_at"),
        Index(
            "ix_api_key_requests_pending",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[bytes | None] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    used_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    purpose: Mapped[str] = mapped_column(Text, server_default=text("'key'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)


class ApiKeyUsageDaily(Base):
    """Requests of a key per UTC day; ``rejected`` counts its 429 answers."""

    __tablename__ = "api_key_usage_daily"
    __table_args__ = (Index("ix_api_key_usage_daily_account_id_day", "account_id", "day"),)

    key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("api_accounts.id", ondelete="CASCADE"))
    requests: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rejected: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class RadFile(Base):
    """A daily MSL RAD file of the PDS volume and what became of it (stage 21)."""

    __tablename__ = "rad_files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('loaded', 'duplicate', 'empty', 'failed', 'md5_mismatch')",
            name="status_valid",
        ),
        CheckConstraint("role IS NULL OR role IN ('primary', 'extra')", name="role_valid"),
        CheckConstraint(
            "dup_check IS NULL OR dup_check IN ('tail', 'size')", name="dup_check_valid"
        ),
        # The md5 of the listing is kept exactly for files that were read to the end.
        CheckConstraint(
            "(md5 IS NULL) = (status IN ('failed', 'md5_mismatch'))", name="md5_present"
        ),
        # The files of a sol, when it is processed or when a file leaves the listing.
        Index("ix_rad_files_sol", "sol"),
    )

    # Path from the volume root, without "./".
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    sol: Mapped[int] = mapped_column(Integer)
    md5: Mapped[str | None] = mapped_column(CHAR(32))
    status: Mapped[str] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(Text)
    dup_check: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    creation_time: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    rdr_version: Mapped[int | None] = mapped_column(SmallInteger)
    # Blocks of the file and how many of them carry dosimetry (diagnostics).
    blocks: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    dose_obs: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    timing_listed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())


class RadDaily(Base):
    """The summary of one sol of the RAD dosimetry (doses in uGy/h as in the source)."""

    __tablename__ = "rad_daily"
    __table_args__ = (
        CheckConstraint("live_s > 0", name="live_positive"),
        CheckConstraint("n_used > 0", name="used_positive"),
    )

    sol: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    earth_date: Mapped[date] = mapped_column(Date)
    dose_b_mean: Mapped[float] = mapped_column(Float)
    dose_b_min: Mapped[float] = mapped_column(Float)
    dose_b_max: Mapped[float] = mapped_column(Float)
    dose_e_mean: Mapped[float] = mapped_column(Float)
    dose_e_min: Mapped[float] = mapped_column(Float)
    dose_e_max: Mapped[float] = mapped_column(Float)
    bu_count: Mapped[int] = mapped_column(BigInteger)
    # Live time of the used observations, seconds.
    live_s: Mapped[float] = mapped_column(Float)
    n_used: Mapped[int] = mapped_column(Integer)
    n_dropped_qc: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_dropped_low_rate: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_dropped_invalid: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # OR of the QC words of the used observations (bit 2 DAN, bit 3 Solar Event).
    qc_bits: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    timing_caveat: Mapped[bool | None] = mapped_column(Boolean)
    # No foreign key: the file may leave rad_files before the sol is recomputed.
    source_path: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())


class RadDataset(Base):
    """State of the RAD volume (one row, ``id`` = 1): checks, validators of the listings."""

    __tablename__ = "rad_dataset"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    checked_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    succeeded_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # When the listing of the files last changed.
    changed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    checksums_etag: Mapped[str | None] = mapped_column(Text)
    checksums_last_modified: Mapped[str | None] = mapped_column(Text)
    checksums_sha256: Mapped[str | None] = mapped_column(Text)
    timing_etag: Mapped[str | None] = mapped_column(Text)
    timing_last_modified: Mapped[str | None] = mapped_column(Text)
    timing_sha256: Mapped[str | None] = mapped_column(Text)
    files_listed: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # exported_at, host and counts of the last imported export file.
    import_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class WeatherFile(Base):
    """A raw weather file of the local archive copy and what became of it (stage 17)."""

    __tablename__ = "weather_files"
    __table_args__ = (
        CheckConstraint("status IN ('parsed', 'superseded', 'failed')", name="status_valid"),
        # The files of a sol, when it is planned or when a file disappears.
        Index("ix_weather_files_rover_sol", "rover_id", "sol"),
    )

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    # From the root of the mission directory, "/" separated.
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    sol: Mapped[int] = mapped_column(Integer)
    product: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    rows_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_used: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    foreign_sol_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    label_start_time: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # Diagnostics only: the Ls of the answers is calculated, not read from here.
    label_ls: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    # The parser revision that read the file (a newer revision reads every file again).
    parser_revision: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    parsed_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())


def _triple(name: str, unit: str) -> CheckConstraint:
    """min, max and mean of a parameter are all set or all null."""
    low, high, mean = (f"{name}_{kind}_{unit}" for kind in ("min", "max", "mean"))
    return CheckConstraint(
        f"({low} IS NULL) = ({high} IS NULL) AND ({high} IS NULL) = ({mean} IS NULL)",
        name=f"{name}_triple",
    )


class WeatherDaily(Base):
    """The weather summary of one sol: temperatures in K, pressure in Pa, humidity in %,
    UV in W/m2, wind in m/s (the API converts on read)."""

    __tablename__ = "weather_daily"
    __table_args__ = (
        CheckConstraint("coverage >= 0 AND coverage <= 1", name="coverage_range"),
        CheckConstraint("hours_covered >= 0 AND hours_covered <= 24", name="hours_covered_range"),
        CheckConstraint("sol >= 0", name="sol_not_negative"),
        _triple("air", "k"),
        _triple("pressure", "pa"),
        _triple("humidity", "pct"),
        _triple("ground", "k"),
        CheckConstraint("(uv_max_wm2 IS NULL) = (uv_mean_wm2 IS NULL)", name="uv_pair"),
    )

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    sol: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    earth_date: Mapped[date] = mapped_column(Date)
    ls: Mapped[float | None] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(REAL)
    hours_covered: Mapped[int] = mapped_column(SmallInteger)
    air_min_k: Mapped[float | None] = mapped_column(Float)
    air_max_k: Mapped[float | None] = mapped_column(Float)
    air_mean_k: Mapped[float | None] = mapped_column(Float)
    pressure_min_pa: Mapped[float | None] = mapped_column(Float)
    pressure_max_pa: Mapped[float | None] = mapped_column(Float)
    pressure_mean_pa: Mapped[float | None] = mapped_column(Float)
    humidity_min_pct: Mapped[float | None] = mapped_column(Float)
    humidity_max_pct: Mapped[float | None] = mapped_column(Float)
    humidity_mean_pct: Mapped[float | None] = mapped_column(Float)
    # Curiosity; Perseverance only with the estimate of part 17G.
    ground_min_k: Mapped[float | None] = mapped_column(Float)
    ground_max_k: Mapped[float | None] = mapped_column(Float)
    ground_mean_k: Mapped[float | None] = mapped_column(Float)
    uv_max_wm2: Mapped[float | None] = mapped_column(Float)
    uv_mean_wm2: Mapped[float | None] = mapped_column(Float)
    wind_mean_ms: Mapped[float | None] = mapped_column(Float)
    wind_gust_ms: Mapped[float | None] = mapped_column(Float)
    wind_dir_mode_deg: Mapped[float | None] = mapped_column(Float)
    # Length of the mean wind vector, 0..1 (diagnostics).
    wind_dir_r: Mapped[float | None] = mapped_column(Float)
    n_air: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_pressure: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_humidity: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_ground: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_uv: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    n_wind: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    source_version: Mapped[dict[str, str]] = mapped_column(JSONB)
    parser_revision: Mapped[int] = mapped_column(SmallInteger)
    updated_at: Mapped[datetime] = mapped_column(_TIMESTAMPTZ, server_default=func.now())


class WeatherHourly(Base):
    """One hour (local mean solar time) of a sol; the 24 rows of a sol are always all there."""

    __tablename__ = "weather_hourly"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rover_id", "sol"],
            ["weather_daily.rover_id", "weather_daily.sol"],
            ondelete="CASCADE",
            name="fk_weather_hourly_rover_id_weather_daily",
        ),
        CheckConstraint("hour >= 0 AND hour <= 23", name="hour_range"),
        CheckConstraint("coverage >= 0 AND coverage <= 1", name="coverage_range"),
    )

    rover_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sol: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    coverage: Mapped[float] = mapped_column(REAL, server_default=text("0"))
    air_k: Mapped[float | None] = mapped_column(Float)
    pressure_pa: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    ground_k: Mapped[float | None] = mapped_column(Float)
    uv_wm2: Mapped[float | None] = mapped_column(Float)
    wind_ms: Mapped[float | None] = mapped_column(Float)
    wind_dir_deg: Mapped[float | None] = mapped_column(Float)


class WeatherDataset(Base):
    """State of the weather data of one rover: when it was parsed and last changed."""

    __tablename__ = "weather_dataset"

    rover_id: Mapped[str] = mapped_column(ForeignKey("rovers.id"), primary_key=True)
    parsed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    changed_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    # The last change of any summary or hour: the fingerprint of cached responses.
    updated_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    source_max_sol: Mapped[int | None] = mapped_column(Integer)
    files_parsed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    files_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime | None] = mapped_column(_TIMESTAMPTZ)
    import_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
