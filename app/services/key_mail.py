"""The outbox of key mails (stage 20B).

Rows of ``api_key_requests`` with ``status = 'pending'`` are the queue. The sender locks due
rows (``FOR UPDATE SKIP LOCKED``), makes the token at the moment of sending, mails the link and
stores only the token's hash. A failed send is retried by the schedule of
``KEY_MAIL_RETRY_MINUTES`` up to ``KEY_MAIL_MAX_ATTEMPTS`` attempts. Nothing here logs an
address, a token or the text of a mail error.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.apikeys import hash_token
from app.core.config import Settings
from app.db.models import ApiKeyRequest
from app.repositories import api_keys as repo
from app.services.api_keys import Clock, utc_now
from app.services.mail import Mailer, MailerError
from app.services.mail_templates import delete_link_message, key_link_message

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
SEND_JOB_SECONDS = 60


@dataclass(frozen=True)
class MailStatus:
    pending: int
    sent_24h: int
    failed_24h: int
    global_limit: int
    last_success_at: datetime | None
    last_error_at: datetime | None


class KeyMailOutbox:
    """Sends the queued mails. ``mailer`` returns the current ``Mailer`` (the application
    passes ``lambda: app.state.mailer``, so tests may swap it)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        mailer: Callable[[], Mailer],
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._mailer = mailer
        self._settings = settings
        self._clock = clock
        # Set False in tests to send only through ``run_once()``.
        self.auto_wake = True
        self.last_success_at: datetime | None = None
        self.last_error_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._again = False

    @property
    def degraded(self) -> bool:
        """A send failed recently and nothing was sent after it."""
        failed = self.last_error_at
        if failed is None:
            return False
        if self._clock() - failed > timedelta(minutes=self._settings.key_mail_degraded_minutes):
            return False
        return self.last_success_at is None or self.last_success_at < failed

    def wake(self) -> None:
        """Send soon, in one background task (a second call while it runs asks for one more
        pass). Needs a running event loop."""
        if not self.auto_wake:
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.get_running_loop().create_task(self._drain())

    async def aclose(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _drain(self) -> None:
        while True:
            self._again = False
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Key mail pass failed error=%s", type(exc).__name__)
            if not self._again:
                return

    async def run_once(self) -> int:
        """One pass: up to ``BATCH_SIZE`` due mails, each in its own transaction that is
        committed right after the send (a later failure cannot undo an earlier mail).
        Returns the number of mails sent."""
        sent = 0
        for _ in range(BATCH_SIZE):
            async with self._session_factory() as session:
                rows = await repo.claim_pending(session, self._clock(), 1)
                if not rows:
                    break
                row = rows[0]
                done = await self._process(session, row)
                await session.commit()
            if done is not None:
                # After the commit: "success" means the token is stored.
                self.last_success_at = done
                sent += 1
        return sent

    async def _process(self, session: AsyncSession, row: ApiKeyRequest) -> datetime | None:
        """Send one mail; the time it was sent, or None (retry later, failed or dropped)."""
        if not await self._still_wanted(session, row):
            row.status = "dropped"
            logger.info("Key mail dropped request=%d purpose=%s", row.id, row.purpose)
            return None
        token = secrets.token_urlsafe(32)
        make = key_link_message if row.purpose == "key" else delete_link_message
        try:
            await self._mailer().send(make(self._settings, row.email, token))
        except MailerError as exc:
            self._failed(row, exc.smtp_code)
            return None
        except Exception as exc:
            # Not an SMTP failure (a bad address, a bug): no retry; only the class is logged.
            row.status = "failed"
            row.attempts += 1
            logger.error(
                "Key mail error request=%d purpose=%s error=%s",
                row.id,
                row.purpose,
                type(exc).__name__,
            )
            return None
        now = self._clock()
        row.token_hash = hash_token(token)
        row.sent_at = now
        row.expires_at = now + timedelta(minutes=self._settings.key_token_ttl_minutes)
        row.status = "sent"
        logger.info("Key mail sent request=%d purpose=%s", row.id, row.purpose)
        return now

    async def _still_wanted(self, session: AsyncSession, row: ApiKeyRequest) -> bool:
        """A key mail is not sent to a blocked account, a deletion mail not to a deleted one."""
        account = await repo.get_account_by_email(session, row.email)
        if row.purpose == "key":
            return account is None or account.status != "blocked"
        return account is not None

    def _failed(self, row: ApiKeyRequest, smtp_code: int | None) -> None:
        now = self._clock()
        self.last_error_at = now
        row.attempts += 1
        if row.attempts >= self._settings.key_mail_max_attempts:
            row.status = "failed"
        else:
            schedule = self._settings.key_mail_retry_schedule
            pause = schedule[min(row.attempts - 1, len(schedule) - 1)]
            row.next_attempt_at = now + timedelta(minutes=pause)
        logger.warning(
            "Key mail failed request=%d purpose=%s attempt=%d smtp_code=%s status=%s",
            row.id,
            row.purpose,
            row.attempts,
            smtp_code,
            row.status,
        )

    async def status(self) -> MailStatus:
        now = self._clock()
        async with self._session_factory() as session:
            pending, sent, failed, last_sent = await repo.mail_counters(
                session, now - timedelta(hours=24)
            )
        return MailStatus(
            pending=pending,
            sent_24h=sent,
            failed_24h=failed,
            global_limit=self._settings.key_signup_global_per_day,
            last_success_at=self.last_success_at or last_sent,
            last_error_at=self.last_error_at,
        )


async def send_key_mail(outbox: KeyMailOutbox) -> None:
    """Scheduler job: one pass; a failure is logged (class only) and retried next time."""
    try:
        await outbox.run_once()
    except Exception as exc:
        logger.error("Key mail job failed error=%s", type(exc).__name__)
