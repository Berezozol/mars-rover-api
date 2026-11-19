"""Load the MMGIS position files of Perseverance and Curiosity (stage 11).

Each file (rover x kind) is processed on its own under a transaction-level advisory lock:
conditional request, sha256 no-op, acceptance checks (ADR-7) and an atomic replace of the
rover's rows together with the state row. A file that fails or is rejected keeps the old data.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import NoSourceForRover
from app.db.models import LocationDataset
from app.db.session import create_session_factory
from app.repositories import locations as locations_repo
from app.repositories import rovers as rovers_repo
from app.sources.http import ConditionalBody, UpstreamClient, UpstreamError
from app.sources.locations import (
    KINDS,
    LOCATION_MISSIONS,
    Kind,
    LocationAdapter,
    LocationFormatError,
    ParsedDataset,
    parse_traverse,
    parse_waypoints,
)

logger = logging.getLogger(__name__)

# First key of the two-int advisory lock; the second is legacy_id * 2 + (0 waypoints, 1 traverse).
LOCATION_LOCK_NAMESPACE = 7_250_002
MAX_ERROR_LENGTH = 500
# Share of valid objects below which a file is rejected.
MIN_VALID_RATIO = 0.5

Status = Literal["changed", "unchanged", "skipped", "rejected", "failed"]


@dataclass
class DatasetResult:
    rover_id: str
    kind: str
    status: Status
    items: int = 0
    dropped: int = 0
    error: str | None = None


class _Rejected(Exception):
    """The downloaded file does not pass the acceptance checks; old data stays."""


class LocationSyncService:
    """Loads the position files; never raises out of ``refresh_due``."""

    def __init__(self, engine: AsyncEngine, client: UpstreamClient, settings: Settings) -> None:
        self._factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
        self._adapter = LocationAdapter(client, settings)
        self._settings = settings

    async def refresh_due(self, now: datetime | None = None) -> list[DatasetResult]:
        """Check every file whose last successful check is older than LOCATION_REFRESH_HOURS."""
        if not self._settings.location_sync_enabled:
            return []
        now = now or datetime.now(UTC)
        results: list[DatasetResult] = []
        for rover_id in LOCATION_MISSIONS:
            results.extend(await self._rover(rover_id, now, due_only=True, force=False))
        return results

    async def refresh(self, rover_id: str, *, force: bool = False) -> list[DatasetResult]:
        """Load both files of one rover now (no once-a-day rule).

        The conditional request is still sent; ``force`` drops it, the sha256 no-op and the
        shrink checks. ``NoSourceForRover`` for a rover without a position source.
        """
        rover_id = rover_id.lower()
        if rover_id not in LOCATION_MISSIONS:
            raise NoSourceForRover(rover_id)
        return await self._rover(rover_id, datetime.now(UTC), due_only=False, force=force)

    async def _rover(
        self, rover_id: str, now: datetime, *, due_only: bool, force: bool
    ) -> list[DatasetResult]:
        results = []
        for kind in KINDS:
            result = await self._safe(rover_id, kind, now, due_only=due_only, force=force)
            results.append(result)
            self._log(result)
        return results

    async def _safe(
        self, rover_id: str, kind: Kind, now: datetime, *, due_only: bool, force: bool
    ) -> DatasetResult:
        try:
            return await self._process(rover_id, kind, now, due_only=due_only, force=force)
        except UpstreamError as exc:
            # Expected: the source is down, slow or sent too much (logged once by _log).
            return await self._record_failure(rover_id, kind, now, exc)
        except Exception as exc:
            logger.exception("Location sync crashed rover=%s kind=%s", rover_id, kind)
            return await self._record_failure(rover_id, kind, now, exc)

    async def _record_failure(
        self, rover_id: str, kind: Kind, now: datetime, exc: BaseException
    ) -> DatasetResult:
        error = _describe(exc)
        try:
            async with self._factory() as session:
                await locations_repo.save_dataset(
                    session, rover_id, kind, checked_at=now, last_error=error
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Could not record the location failure rover=%s kind=%s", rover_id, kind
            )
        return DatasetResult(rover_id, kind, "failed", error=error)

    async def _process(
        self, rover_id: str, kind: Kind, now: datetime, *, due_only: bool, force: bool
    ) -> DatasetResult:
        """Short read, download with no connection held, then one locked transaction."""
        async with self._factory() as session:
            rover = await rovers_repo.get_rover(session, rover_id)
            if rover is None:
                raise ValueError(f"Unknown rover: {rover_id}")
            legacy_id = rover.legacy_id
            state = _State.of(await locations_repo.get_dataset(session, rover_id, kind))
        if due_only and self._is_fresh(state, now):
            return state.result(rover_id, kind, "skipped")
        conditional = state.exists and not force
        response = await self._adapter.fetch(
            rover_id,
            kind,
            etag=state.etag if conditional else None,
            last_modified=state.last_modified if conditional else None,
        )
        async with self._factory() as session:
            if not await _try_lock(session, legacy_id, kind):
                return DatasetResult(rover_id, kind, "skipped")
            current = _State.of(await locations_repo.get_dataset(session, rover_id, kind))
            if current.version != state.version:
                # Another process stored this file while we downloaded: nothing to do.
                return current.result(rover_id, kind, "skipped")
            try:
                return await self._apply(session, rover_id, kind, now, response, current, force)
            except _Rejected as exc:
                await session.rollback()
                return await self._record_rejection(
                    rover_id, kind, now, str(exc), current.result(rover_id, kind, "rejected")
                )

    def _is_fresh(self, state: "_State", now: datetime) -> bool:
        if state.succeeded_at is None:
            return False
        return now - state.succeeded_at < timedelta(hours=self._settings.location_refresh_hours)

    async def _apply(
        self,
        session: AsyncSession,
        rover_id: str,
        kind: Kind,
        now: datetime,
        response: ConditionalBody,
        state: "_State",
        force: bool,
    ) -> DatasetResult:
        if response.not_modified:
            await _save_success(session, rover_id, kind, now)
            return state.result(rover_id, kind, "unchanged")
        body = _body(response)
        digest = hashlib.sha256(body).hexdigest()
        if not force and state.sha == digest:
            await _save_success(
                session,
                rover_id,
                kind,
                now,
                etag=response.etag,
                last_modified=response.last_modified,
            )
            return state.result(rover_id, kind, "unchanged")
        parsed = await asyncio.to_thread(_parse, kind, body)
        self._check(parsed, state, force)
        await _replace(session, rover_id, kind, parsed)
        await _save_success(
            session,
            rover_id,
            kind,
            now,
            changed_at=now,
            etag=response.etag,
            last_modified=response.last_modified,
            content_sha256=digest,
            items=len(parsed.items),
            dropped=parsed.dropped,
            max_sol=parsed.max_sol,
        )
        return DatasetResult(rover_id, kind, "changed", len(parsed.items), parsed.dropped)

    def _check(self, parsed: ParsedDataset, state: "_State", force: bool) -> None:
        """Acceptance checks of ADR-7; ``_Rejected`` with the reason if the file fails them."""
        total = len(parsed.items) + parsed.dropped
        if total == 0 or len(parsed.items) / total < MIN_VALID_RATIO:
            raise _Rejected(f"only {len(parsed.items)} of {total} objects are valid")
        if force or not state.exists:
            return
        keep = state.items * self._settings.location_min_keep_ratio
        if state.items > 0 and len(parsed.items) < keep:
            raise _Rejected(f"{len(parsed.items)} rows, stored {state.items}: file shrank")
        if state.max_sol is not None and (parsed.max_sol is None or parsed.max_sol < state.max_sol):
            raise _Rejected(f"max sol fell from {state.max_sol} to {parsed.max_sol}")

    async def _record_rejection(
        self,
        rover_id: str,
        kind: Kind,
        now: datetime,
        reason: str,
        stored: DatasetResult,
    ) -> DatasetResult:
        async with self._factory() as session:
            await locations_repo.save_dataset(
                session, rover_id, kind, checked_at=now, last_error=f"rejected: {reason}"
            )
            await session.commit()
        stored.error = reason
        return stored

    @staticmethod
    def _log(result: DatasetResult) -> None:
        level = logging.WARNING if result.status in ("rejected", "failed") else logging.INFO
        logger.log(
            level,
            "Location file rover=%s kind=%s status=%s items=%d dropped=%d error=%s",
            result.rover_id,
            result.kind,
            result.status,
            result.items,
            result.dropped,
            result.error,
        )
        if result.dropped and result.status == "changed":
            logger.warning(
                "Location file had dropped objects rover=%s kind=%s dropped=%d",
                result.rover_id,
                result.kind,
                result.dropped,
            )


async def _try_lock(session: AsyncSession, legacy_id: int, kind: Kind) -> bool:
    key = legacy_id * 2 + KINDS.index(kind)
    acquired = await session.scalar(
        select(func.pg_try_advisory_xact_lock(LOCATION_LOCK_NAMESPACE, key))
    )
    return bool(acquired)


def _body(response: ConditionalBody) -> bytes:
    if response.body is None:
        raise RuntimeError("Conditional response has neither 304 nor a body")
    return response.body


def _parse(kind: Kind, body: bytes) -> ParsedDataset:
    try:
        return parse_waypoints(body) if kind == "waypoints" else parse_traverse(body)
    except LocationFormatError as exc:
        raise _Rejected(str(exc)) from exc


async def _replace(session: AsyncSession, rover_id: str, kind: Kind, parsed: ParsedDataset) -> None:
    if kind == "waypoints":
        await locations_repo.replace_waypoints(session, rover_id, parsed.items)
    else:
        await locations_repo.replace_traverse(session, rover_id, parsed.items)


async def _save_success(
    session: AsyncSession, rover_id: str, kind: Kind, now: datetime, **fields: object
) -> None:
    """State of a successful check, committed together with any replaced rows."""
    await locations_repo.save_dataset(
        session, rover_id, kind, checked_at=now, succeeded_at=now, last_error=None, **fields
    )
    await session.commit()


@dataclass(frozen=True)
class _State:
    """Plain values of a ``location_datasets`` row (safe to use after the session ends)."""

    exists: bool = False
    etag: str | None = None
    last_modified: str | None = None
    sha: str | None = None
    changed_at: datetime | None = None
    succeeded_at: datetime | None = None
    items: int = 0
    dropped: int = 0
    max_sol: int | None = None

    @classmethod
    def of(cls, row: LocationDataset | None) -> "_State":
        if row is None:
            return cls()
        return cls(
            True,
            row.etag,
            row.last_modified,
            row.content_sha256,
            row.changed_at,
            row.succeeded_at,
            row.items,
            row.dropped,
            row.max_sol,
        )

    @property
    def version(self) -> tuple[object, ...]:
        """Changes whenever another process stores a file."""
        return (self.exists, self.etag, self.sha, self.changed_at)

    def result(self, rover_id: str, kind: Kind, status: Status) -> DatasetResult:
        return DatasetResult(rover_id, kind, status, self.items, self.dropped)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
