"""API key accounts and usage (stage 20): issuing, revoking, blocking, admin views, counters.

Every mutation commits and then drops the affected entries of the resolver cache, so a revoke
or a block is seen by the next request in this process.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.apikeys import ApiKeyResolver
from app.core.apikeys import generate_api_key, hash_api_key, key_prefix
from app.core.config import Settings
from app.core.errors import InvalidParam, KeyLimitReached, NotFoundError
from app.db.models import ApiAccount, ApiKey
from app.repositories import api_keys as repo
from app.repositories._common import AnyRow
from app.services.v2 import PageRequest

logger = logging.getLogger(__name__)

MAX_EMAIL_LENGTH = 254
MAX_TEXT_LENGTH = 500
# One "@", a domain with a dot (no empty label); checked on the lowercase form.
_EMAIL_RE = re.compile(r"[^@]+@[^@.]+(?:\.[^@.]+)+")

_EMAIL_MESSAGE = "must be a valid e-mail address of at most 254 characters"

Clock = Callable[[], datetime]


def _is_printable_ascii(value: str) -> bool:
    """No control characters, spaces or non-ASCII (stored lowercase ASCII only for now)."""
    return all(0x21 <= ord(char) <= 0x7E for char in value)


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    """The lowercase e-mail; ``ValueError`` (shown as a 422) if it is not acceptable."""
    email = value.lower()
    if len(email) > MAX_EMAIL_LENGTH or not _is_printable_ascii(email):
        raise ValueError(_EMAIL_MESSAGE)
    if _EMAIL_RE.fullmatch(email) is None:
        raise ValueError(_EMAIL_MESSAGE)
    return email


MAX_ID = 2**63 - 1


def _check_id(value: int, error: NotFoundError) -> None:
    """An id outside bigint cannot exist (and would be a database error): 404 at once."""
    if not 1 <= value <= MAX_ID:
        raise error


class AccountNotFound(NotFoundError):
    def __init__(self) -> None:
        super().__init__("Account not found")


class KeyNotFound(NotFoundError):
    def __init__(self) -> None:
        super().__init__("Key not found")


@dataclass(frozen=True)
class IssuedKey:
    """A new key: ``key`` is the only time its value exists outside the client."""

    key: str
    record: ApiKey
    account: ApiAccount


@dataclass(frozen=True)
class AccountSummary:
    account: ApiAccount
    active_keys: int
    requests_30d: int


@dataclass(frozen=True)
class AccountPage:
    items: list[AccountSummary]
    page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class AccountDetail:
    summary: AccountSummary
    keys: list[ApiKey]
    usage_daily: list[tuple[date, int, int]]


def _summary(row: AnyRow) -> AccountSummary:
    return AccountSummary(row[0], int(row.active_keys), int(row.requests_30d))


class ApiKeyService:
    """Account and key operations over a request session; ``resolver`` may be None."""

    def __init__(
        self, settings: Settings, resolver: ApiKeyResolver | None, clock: Clock = utc_now
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._clock = clock

    async def issue_key(self, session: AsyncSession, email: str, note: str | None) -> IssuedKey:
        """A new key for the account of ``email`` (created, verified, if new). ``KeyLimitReached``
        when it already has ``API_KEY_MAX_PER_ACCOUNT`` active keys."""
        email = normalize_email(email)
        account = await repo.get_or_create_account(session, email, note, self._clock())
        limit = self._settings.api_key_max_per_account
        if await repo.count_active_keys(session, account.id) >= limit:
            await session.rollback()
            raise KeyLimitReached(limit)
        key = generate_api_key()
        record = await repo.add_key(session, account.id, hash_api_key(key), key_prefix(key))
        await session.commit()
        logger.info("API key issued account=%d key=%d", account.id, record.id)
        return IssuedKey(key, record, account)

    async def list_accounts(
        self, session: AsyncSession, q: str | None, status: str | None, page: PageRequest
    ) -> AccountPage:
        rows, total = await repo.list_accounts(
            session,
            q=q or None,
            status=status,
            today=self._clock().date(),
            limit=page.per_page,
            offset=page.offset,
        )
        return AccountPage([_summary(row) for row in rows], page.page, page.per_page, total)

    async def get_account(self, session: AsyncSession, account_id: int) -> AccountDetail:
        _check_id(account_id, AccountNotFound())
        today = self._clock().date()
        row = await repo.account_summary(session, account_id, today)
        if row is None:
            raise AccountNotFound()
        keys = await repo.list_keys(session, account_id)
        usage = await repo.usage_daily(session, account_id, today)
        return AccountDetail(_summary(row), keys, usage)

    async def block(
        self, session: AsyncSession, account_id: int, reason: str | None
    ) -> AccountSummary:
        return await self._set_blocked(session, account_id, blocked=True, reason=reason)

    async def unblock(self, session: AsyncSession, account_id: int) -> AccountSummary:
        return await self._set_blocked(session, account_id, blocked=False, reason=None)

    async def _set_blocked(
        self, session: AsyncSession, account_id: int, *, blocked: bool, reason: str | None
    ) -> AccountSummary:
        _check_id(account_id, AccountNotFound())
        account = await repo.set_blocked(session, account_id, blocked=blocked, reason=reason)
        if account is None:
            raise AccountNotFound()
        await session.commit()
        self._invalidate_account(account_id)
        logger.info("API account %s account=%d", "blocked" if blocked else "unblocked", account_id)
        row = await repo.account_summary(session, account_id, self._clock().date())
        if row is None:  # deleted between the two statements
            raise AccountNotFound()
        return _summary(row)

    async def revoke_key(self, session: AsyncSession, key_id: int, reason: str) -> ApiKey:
        _check_id(key_id, KeyNotFound())
        key = await repo.revoke_key(session, key_id, reason)
        if key is None:
            raise KeyNotFound()
        await session.commit()
        self._invalidate_key(key_id)
        logger.info("API key revoked key=%d reason=%s", key_id, reason)
        return key

    async def delete_account(self, session: AsyncSession, account_id: int) -> None:
        _check_id(account_id, AccountNotFound())
        existed, key_ids = await repo.delete_account(session, account_id)
        if not existed:
            raise AccountNotFound()
        await session.commit()
        self._invalidate_account(account_id)
        for key_id in key_ids:
            self._invalidate_key(key_id)
        logger.info("API account deleted account=%d keys=%d", account_id, len(key_ids))

    def _invalidate_account(self, account_id: int) -> None:
        if self._resolver is not None:
            self._resolver.invalidate_account(account_id)

    def _invalidate_key(self, key_id: int) -> None:
        if self._resolver is not None:
            self._resolver.invalidate_key(key_id)


def check_text_length(field_name: str, value: str | None) -> None:
    if value is not None and len(value) > MAX_TEXT_LENGTH:
        raise InvalidParam(field_name, f"must be at most {MAX_TEXT_LENGTH} characters")


@dataclass
class _Counts:
    requests: int = 0
    rejected: int = 0


@dataclass
class ApiKeyUsage:
    """Request counters of keys, kept in memory and written by ``flush`` (statistics, not
    billing: a crash loses at most one flush interval).

    ``add`` runs once per answered request without awaiting. ``flush`` takes the counters,
    writes them in one transaction with ``ON CONFLICT`` adding to the day's row, and puts
    them back if the write fails. Flushes are serialized by a lock, so the shutdown flush waits
    for one that is running. Known inexactness: if the commit succeeds but the connection
    breaks before the client sees it, the counters are put back and counted a second time
    (statistics only).
    """

    session_factory: async_sessionmaker[AsyncSession]
    clock: Clock = utc_now
    _counts: dict[tuple[int, int, date], _Counts] = field(default_factory=dict)
    _last_used: dict[int, datetime] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def add(self, account_id: int, key_id: int, *, rejected: bool = False) -> None:
        now = self.clock()
        counts = self._counts.setdefault((account_id, key_id, now.date()), _Counts())
        counts.requests += 1
        if rejected:
            counts.rejected += 1
        self._last_used[key_id] = now

    @property
    def pending(self) -> int:
        """Requests counted and not written yet."""
        return sum(counts.requests for counts in self._counts.values())

    async def flush(self) -> int:
        """Write the counters; returns the number of requests written."""
        async with self._lock:
            return await self._flush()

    async def _flush(self) -> int:
        counts, last_used = self._counts, self._last_used
        if not counts:
            return 0
        self._counts, self._last_used = {}, {}
        rows = [
            repo.UsageRow(account, key, day, c.requests, c.rejected)
            for (account, key, day), c in counts.items()
        ]
        try:
            async with self.session_factory() as session:
                await repo.upsert_usage(session, rows)
                await repo.touch_keys(session, last_used)
                await session.commit()
        except BaseException:
            self._restore(counts, last_used)
            raise
        return sum(row.requests for row in rows)

    def _restore(
        self, counts: dict[tuple[int, int, date], _Counts], last: dict[int, datetime]
    ) -> None:
        for key, old in counts.items():
            current = self._counts.setdefault(key, _Counts())
            current.requests += old.requests
            current.rejected += old.rejected
        for key_id, used_at in last.items():
            if key_id not in self._last_used or self._last_used[key_id] < used_at:
                self._last_used[key_id] = used_at


async def flush_api_key_usage(usage: ApiKeyUsage) -> None:
    """Scheduler job: write the counters; a failure is logged and retried next time."""
    try:
        await usage.flush()
    except Exception:
        logger.exception("Flushing API key usage failed")


async def cleanup_api_keys(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> None:
    """Scheduler job: drop counters older than API_KEY_USAGE_RETENTION_DAYS and confirmation
    requests that expired long ago. A failure is logged only."""
    cutoff = now.date() - timedelta(days=settings.api_key_usage_retention_days)
    try:
        async with session_factory() as session:
            usage = await repo.delete_old_usage(session, cutoff)
            requests = await repo.delete_expired_requests(session, now - timedelta(hours=24))
            await session.commit()
        logger.info("API key cleanup usage_rows=%d requests=%d", usage, requests)
    except Exception:
        logger.exception("API key cleanup failed")
