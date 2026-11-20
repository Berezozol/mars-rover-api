"""Load the Perseverance sample table of the NASA samples page (stage 13).

Like the location sync: the page is checked at most once a day (state in the database),
parsed rows are compared by a hash (the page body itself is not stable), accepted all or
nothing and replaced in one transaction under an advisory lock. A page that fails or is
rejected keeps the old data and records the reason in ``last_error``.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import SampleDataset
from app.db.session import create_session_factory
from app.repositories import rovers as rovers_repo
from app.repositories import samples as samples_repo
from app.sources.http import ConditionalBody, UpstreamClient, UpstreamError
from app.sources.samples import (
    SAMPLES_ROVER,
    ParsedSamples,
    SamplesAdapter,
    SamplesFormatError,
    parse_samples,
)

logger = logging.getLogger(__name__)

# First key of the two-int advisory lock; the second is the rover's legacy_id.
SAMPLES_LOCK_NAMESPACE = 7_250_003
MAX_ERROR_LENGTH = 500

SampleSyncStatus = Literal["changed", "unchanged", "skipped", "rejected", "failed"]


@dataclass
class SampleSyncResult:
    status: SampleSyncStatus
    items: int = 0
    error: str | None = None


class _Rejected(Exception):
    """The downloaded page does not pass the acceptance checks; old data stays."""


class SampleSyncService:
    """Loads the samples page; never raises out of ``refresh_due`` or ``refresh``."""

    def __init__(self, engine: AsyncEngine, client: UpstreamClient, settings: Settings) -> None:
        self._factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
        self._adapter = SamplesAdapter(client, settings)
        self._settings = settings

    async def refresh_due(self, now: datetime | None = None) -> SampleSyncResult:
        """Check the page if the last successful check is older than SAMPLES_REFRESH_HOURS."""
        if not self._settings.samples_sync_enabled:
            return SampleSyncResult("skipped")
        return await self._safe(now or datetime.now(UTC), due_only=True, force=False)

    async def refresh(self, *, force: bool = False) -> SampleSyncResult:
        """Check the page now (no once-a-day rule).

        The conditional request is sent too; ``force`` drops it, the hash no-op and the check
        that no stored sample disappears (a broken page is still rejected).
        """
        return await self._safe(datetime.now(UTC), due_only=False, force=force)

    async def _safe(self, now: datetime, *, due_only: bool, force: bool) -> SampleSyncResult:
        try:
            result = await self._process(now, due_only=due_only, force=force)
        except UpstreamError as exc:
            # Expected: the source is down, slow or sent too much (logged once below).
            result = await self._record_failure(now, exc)
        except Exception as exc:
            logger.exception("Samples sync crashed")
            result = await self._record_failure(now, exc)
        self._log(result)
        return result

    async def _record_failure(self, now: datetime, exc: BaseException) -> SampleSyncResult:
        error = _describe(exc)
        try:
            async with self._factory() as session:
                await samples_repo.save_dataset(
                    session, SAMPLES_ROVER, checked_at=now, last_error=error
                )
                await session.commit()
        except Exception:
            logger.exception("Could not record the samples failure")
        return SampleSyncResult("failed", error=error)

    async def _process(self, now: datetime, *, due_only: bool, force: bool) -> SampleSyncResult:
        """Short read, download with no connection held, then one locked transaction."""
        async with self._factory() as session:
            rover = await rovers_repo.get_rover(session, SAMPLES_ROVER)
            if rover is None:
                raise ValueError(f"Unknown rover: {SAMPLES_ROVER}")
            legacy_id = rover.legacy_id
            state = _State.of(await samples_repo.get_dataset(session, SAMPLES_ROVER))
        if due_only and self._is_fresh(state, now):
            return state.result("skipped")
        conditional = state.exists and not force
        response = await self._adapter.fetch(
            etag=state.etag if conditional else None,
            last_modified=state.last_modified if conditional else None,
        )
        try:
            page = None if response.not_modified else await _parse_page(_body(response))
        except _Rejected as exc:
            return await self._record_rejection(now, str(exc), state)
        async with self._factory() as session:
            if not await _try_lock(session, legacy_id):
                return SampleSyncResult("skipped")
            current = _State.of(await samples_repo.get_dataset(session, SAMPLES_ROVER))
            if current.version != state.version:
                # Another process stored the page while we downloaded: nothing to do.
                return current.result("skipped")
            try:
                return await self._apply(session, now, response, page, current, force)
            except _Rejected as exc:
                await session.rollback()
                return await self._record_rejection(now, str(exc), current)

    def _is_fresh(self, state: "_State", now: datetime) -> bool:
        if state.succeeded_at is None:
            return False
        return now - state.succeeded_at < timedelta(hours=self._settings.samples_refresh_hours)

    async def _apply(
        self,
        session: AsyncSession,
        now: datetime,
        response: ConditionalBody,
        page: tuple[ParsedSamples, str] | None,
        state: "_State",
        force: bool,
    ) -> SampleSyncResult:
        if page is None:
            await _save_success(session, now)
            return state.result("unchanged")
        parsed, digest = page
        validators = {"etag": response.etag, "last_modified": response.last_modified}
        modified = {"source_modified_at": parsed.source_modified_at}
        if not force and state.sha == digest:
            await _save_success(session, now, **validators, **modified)
            return state.result("unchanged")
        if not force:
            await _check_kept(session, parsed)
        await samples_repo.replace_samples(session, SAMPLES_ROVER, parsed.items)
        await _save_success(
            session,
            now,
            changed_at=now,
            content_sha256=digest,
            items=len(parsed.items),
            **validators,
            **modified,
        )
        return SampleSyncResult("changed", len(parsed.items))

    async def _record_rejection(
        self, now: datetime, reason: str, state: "_State"
    ) -> SampleSyncResult:
        async with self._factory() as session:
            await samples_repo.save_dataset(
                session, SAMPLES_ROVER, checked_at=now, last_error=f"rejected: {reason}"
            )
            await session.commit()
        result = state.result("rejected")
        result.error = reason
        return result

    @staticmethod
    def _log(result: SampleSyncResult) -> None:
        level = logging.WARNING if result.status in ("rejected", "failed") else logging.INFO
        logger.log(
            level,
            "Samples page status=%s items=%d error=%s",
            result.status,
            result.items,
            result.error,
        )


async def _check_kept(session: AsyncSession, parsed: ParsedSamples) -> None:
    """Samples never disappear from the page: a missing stored number means a broken page."""
    stored = {sample.number for sample in await samples_repo.list_samples(session, SAMPLES_ROVER)}
    missing = sorted(stored - {item.number for item in parsed.items})
    if missing:
        listed = ", ".join(str(number) for number in missing[:10])
        raise _Rejected(f"stored sample number(s) missing from the page: {listed}")


async def _try_lock(session: AsyncSession, legacy_id: int) -> bool:
    acquired = await session.scalar(
        select(func.pg_try_advisory_xact_lock(SAMPLES_LOCK_NAMESPACE, legacy_id))
    )
    return bool(acquired)


def _body(response: ConditionalBody) -> bytes:
    if response.body is None:
        raise RuntimeError("Conditional response has neither 304 nor a body")
    return response.body


async def _parse_page(body: bytes) -> tuple[ParsedSamples, str]:
    """Parse (off the event loop) and hash the page; no database connection is held."""
    try:
        parsed = await asyncio.to_thread(parse_samples, body)
    except SamplesFormatError as exc:
        raise _Rejected(str(exc)) from exc
    return parsed, _digest(parsed)


def _digest(parsed: ParsedSamples) -> str:
    """sha256 of the canonical JSON of the parsed rows (sorted by number)."""
    rows = [asdict(item) for item in parsed.items]
    canonical = json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _save_success(session: AsyncSession, now: datetime, **fields: object) -> None:
    """State of a successful check, committed together with any replaced rows."""
    await samples_repo.save_dataset(
        session, SAMPLES_ROVER, checked_at=now, succeeded_at=now, last_error=None, **fields
    )
    await session.commit()


@dataclass(frozen=True)
class _State:
    """Plain values of a ``sample_datasets`` row (safe to use after the session ends)."""

    exists: bool = False
    etag: str | None = None
    last_modified: str | None = None
    sha: str | None = None
    changed_at: datetime | None = None
    succeeded_at: datetime | None = None
    items: int = 0

    @classmethod
    def of(cls, row: SampleDataset | None) -> "_State":
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
        )

    @property
    def version(self) -> tuple[object, ...]:
        """Changes whenever another process stores the page."""
        return (self.exists, self.sha, self.changed_at)

    def result(self, status: SampleSyncStatus) -> SampleSyncResult:
        return SampleSyncResult(status, self.items)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
