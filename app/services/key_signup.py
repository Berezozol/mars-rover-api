"""Self-service keys by e-mail (stage 20B): mail requests, confirming a link, deleting data.

Every outcome of ``request`` that does not depend on the caller's address looks the same to
the caller: the service only decides whether a row is queued.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikeys import hash_token
from app.core.config import Settings
from app.core.errors import InvalidParam, InvalidToken, MailUnavailable
from app.db.models import ApiKeyRequest
from app.repositories import api_keys as repo
from app.services.api_keys import ApiKeyService, Clock, IssuedKey, utc_now

logger = logging.getLogger(__name__)

_DAY = timedelta(hours=24)


@dataclass(frozen=True)
class RequestOutcome:
    """``queued``: the row is pending (a mail will be sent); else it is stored as skipped."""

    queued: bool


class KeySignupService:
    def __init__(self, settings: Settings, keys: ApiKeyService, clock: Clock = utc_now) -> None:
        self._settings = settings
        self._keys = keys
        self._clock = clock

    async def request(
        self, session: AsyncSession, email: str, purpose: str, honeypot: str | None = None
    ) -> RequestOutcome:
        """Queue a mail to ``email`` (lowercase, valid) unless a limit or the account state says
        no. ``MailUnavailable`` when the server's daily limit is used; nothing else depends on
        the address."""
        domain = email.rpartition("@")[2]
        if domain in self._settings.key_email_blocked_domain_set:
            raise InvalidParam("email", "this e-mail domain is not accepted")
        now = self._clock()
        since = now - _DAY
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        await repo.lock_mail_requests(session)
        total, _ = await repo.count_mail_requests(session, day_start)
        if total >= self._settings.key_signup_global_per_day:
            await session.rollback()
            raise MailUnavailable(day_start + _DAY, now)
        wanted = not honeypot and await self._wanted(session, email, purpose, since)
        # One row and one commit for every valid request, wanted or not, so that the work
        # (and the global count) does not depend on the address.
        request = await repo.add_mail_request(
            session, email, purpose, now, "pending" if wanted else "skipped"
        )
        await session.commit()
        logger.info(
            "Mail request stored request=%d purpose=%s queued=%s", request.id, purpose, wanted
        )
        return RequestOutcome(wanted)

    async def _wanted(
        self, session: AsyncSession, email: str, purpose: str, since: datetime
    ) -> bool:
        count, _ = await repo.count_mail_requests(session, since, email)
        if count >= self._settings.key_signup_per_email_per_day:
            return False
        account = await repo.get_account_by_email(session, email)
        if purpose == "key":
            return account is None or account.status != "blocked"
        return account is not None

    async def _lock_valid(self, session: AsyncSession, token: str, purpose: str) -> ApiKeyRequest:
        row = await repo.lock_request_by_token(session, hash_token(token), purpose)
        if (
            row is None
            or row.status != "sent"
            or row.used_at is not None
            or row.expires_at is None
            or row.expires_at <= self._clock()
        ):
            await session.rollback()
            raise InvalidToken()
        return row

    async def confirm_key(
        self, session: AsyncSession, token: str, revoke_existing: bool
    ) -> IssuedKey:
        """Spend the link and issue a key. ``InvalidToken``, ``AccountBlocked`` and
        ``KeyLimitReached`` leave the link unspent."""
        row = await self._lock_valid(session, token, "key")
        try:
            issued = await self._keys.issue_from_signup(session, row.email, revoke_existing)
        except Exception:
            await session.rollback()
            raise
        row.used_at = self._clock()
        await session.commit()
        self._keys.invalidate_keys(issued.replaced)
        logger.info("API key issued by link account=%d key=%d", issued.account.id, issued.record.id)
        return issued

    async def confirm_delete(self, session: AsyncSession, token: str) -> None:
        """Spend a deletion link: the account, its keys, counters and mail requests go."""
        row = await self._lock_valid(session, token, "delete")
        account = await repo.get_account_by_email(session, row.email)
        if account is None:
            await repo.delete_requests_of_email(session, row.email)
            await session.commit()
            return
        await self._keys.delete_account(session, account.id)
