"""Telegram alerts: stale data and failed or partial syncs (stage 9).

The bot token is part of the Telegram URL, so neither the URL nor httpx exception texts are
ever logged here (the httpx logger itself is redacted in app.core.logging).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import LocationDataset, SampleDataset
from app.repositories import locations as locations_repo
from app.repositories import samples as samples_repo
from app.services.health import RoverFreshness, get_sync_freshness
from app.services.sync import SyncResult
from app.sources.locations import KINDS, LOCATION_MISSIONS
from app.sources.samples import SAMPLES_ROVER

logger = logging.getLogger(__name__)

PREFIX = "[mars-rover-api]"
SEND_TIMEOUT_SECONDS = 10.0
MAX_ERROR_CHARS = 300
ALERT_STATUSES = frozenset({"failed", "partial"})


class TelegramNotifier:
    """Sends a text message to one chat; failures are logged and never raised.

    Uses its own short-lived httpx client, not app/sources/http.py: Telegram is not a data
    source (no upstream retries, rate limit or User-Agent policy apply).
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        api_base: str = "https://api.telegram.org",
        *,
        timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramNotifier | None":
        """A notifier if both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, else None."""
        if not settings.alerts_enabled:
            return None
        chat_id = (settings.telegram_chat_id or "").strip()
        return cls(settings.telegram_token_value, chat_id, settings.telegram_api_base)

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    async def send(self, text: str) -> bool:
        """True if Telegram accepted the message."""
        payload = {"chat_id": self._chat_id, "text": text}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
        except Exception as exc:
            # Any error (also InvalidURL, UnicodeEncodeError): its text or traceback may
            # carry the URL with the token, so only the type is logged.
            logger.warning("Telegram alert not sent: %s", type(exc).__name__)
            return False
        if not response.is_success:
            logger.warning("Telegram alert not sent: HTTP %d", response.status_code)
            return False
        return True


@dataclass(frozen=True)
class LocationHealth:
    """Whether the position files of a rover are being checked successfully."""

    rover: str
    stale: bool
    # Oldest last successful check over the rover's files; None if one never succeeded.
    last_success_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class SamplesHealth:
    """Whether the samples page is being checked successfully."""

    stale: bool
    # Last successful check; None if there never was one.
    last_success_at: datetime | None
    last_error: str | None


class AlertMonitor:
    """Alerts on freshness transitions (per rover) and on failed or partial syncs.

    The rover position files (stage 11) have their own per-rover state: stale when a file
    was not checked successfully for LOCATION_STALE_DAYS (a rejected file counts as a failed
    check; unchanged content does not matter). Off with LOCATION_SYNC_ENABLED=false.

    The last known state lives in memory: after a restart a rover that is already stale is
    reported once more. A state change is remembered only once its message was sent, so an
    unsent alert is retried on the next check.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        notifier: TelegramNotifier,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._notifier = notifier
        # Rover -> stale at the last reported check; absent = fresh.
        self._stale: dict[str, bool] = {}
        self._location_stale: dict[str, bool] = {}
        self._samples_stale = False

    async def check(self, now: datetime | None = None) -> str | None:
        """Check freshness and alert on changes; returns the text sent (None if nothing)."""
        now = now or datetime.now(UTC)
        texts = [
            await self._check_photos(now),
            await self._check_locations(now),
            await self._check_samples(now),
        ]
        sent = [text for text in texts if text]
        return "\n".join(sent) if sent else None

    async def _check_photos(self, now: datetime) -> str | None:
        try:
            async with self._session_factory() as session:
                freshness = await get_sync_freshness(session, self._settings, now)
        except Exception as exc:
            logger.warning("Freshness check for alerts failed: %s", type(exc).__name__)
            return None
        changed = [r for r in freshness.rovers if r.stale != self._stale.get(r.rover, False)]
        if not changed:
            return None
        text = freshness_message(changed)
        if not await self._notifier.send(text):
            return None
        self._stale.update({rover.rover: rover.stale for rover in changed})
        return text

    async def _check_locations(self, now: datetime) -> str | None:
        if not self._settings.location_sync_enabled:
            return None
        try:
            async with self._session_factory() as session:
                datasets = await locations_repo.list_datasets(session)
        except Exception as exc:
            logger.warning("Location freshness check for alerts failed: %s", type(exc).__name__)
            return None
        stale_after = timedelta(days=self._settings.location_stale_days)
        health = location_health(datasets, now, stale_after)
        changed = [h for h in health if h.stale != self._location_stale.get(h.rover, False)]
        if not changed:
            return None
        text = "\n".join(f"{PREFIX} {_location_line(h, stale_after)}" for h in changed)
        if not await self._notifier.send(text):
            return None
        self._location_stale.update({h.rover: h.stale for h in changed})
        return text

    async def _check_samples(self, now: datetime) -> str | None:
        if not self._settings.samples_sync_enabled:
            return None
        try:
            async with self._session_factory() as session:
                dataset = await samples_repo.get_dataset(session, SAMPLES_ROVER)
        except Exception as exc:
            logger.warning("Samples freshness check for alerts failed: %s", type(exc).__name__)
            return None
        stale_after = timedelta(days=self._settings.samples_stale_days)
        health = samples_health(dataset, now, stale_after)
        if health.stale == self._samples_stale:
            return None
        text = f"{PREFIX} {_samples_line(health, stale_after)}"
        if not await self._notifier.send(text):
            return None
        self._samples_stale = health.stale
        return text

    async def on_sync_finished(self, run: SyncResult) -> str | None:
        """Alert on a failed or partial run; returns the text sent (None if nothing).

        Not retried: if Telegram does not accept it, the alert is lost (logged as a warning).
        A sync that keeps failing is still reported by the freshness check (sync stale).
        """
        if run.status not in ALERT_STATUSES:
            return None
        text = sync_message(run)
        return text if await self._notifier.send(text) else None


def freshness_message(changed: Sequence[RoverFreshness]) -> str:
    lines = [f"{PREFIX} {_freshness_line(rover)}" for rover in changed]
    return "\n".join(lines)


def _freshness_line(rover: RoverFreshness) -> str:
    details = (
        f"last successful sync {_when(rover.last_success_at)}, "
        f"newest photo {rover.newest_photo_date or 'none'}"
    )
    if not rover.stale:
        return f"{rover.rover}: data recovered ({details})"
    reasons = [
        name
        for name, flag in (("sync stale", rover.sync_stale), ("photos stale", rover.photos_stale))
        if flag
    ]
    return f"{rover.rover}: {', '.join(reasons)} ({details})"


def location_health(
    datasets: Sequence[LocationDataset], now: datetime, stale_after: timedelta
) -> list[LocationHealth]:
    """Health of the position files per rover.

    Stale: the oldest of coalesce(succeeded_at, created_at) over the files is older than
    ``stale_after``. A rover whose files were never attempted is not stale.
    """
    by_key = {(d.rover_id, d.kind): d for d in datasets}
    result = []
    for rover in LOCATION_MISSIONS:
        rows = [row for kind in KINDS if (row := by_key.get((rover, kind))) is not None]
        if not rows:
            # Never attempted (fresh install, loading off): nothing to report yet.
            result.append(LocationHealth(rover, False, None, None))
            continue
        successes = [row.succeeded_at for row in rows]
        oldest = None if None in successes else min(s for s in successes if s is not None)
        # A file that never succeeded counts from the day its row was created.
        reference = min(row.succeeded_at or row.created_at for row in rows)
        errors = [row.last_error for row in rows if row.last_error]
        stale = now - reference > stale_after
        result.append(LocationHealth(rover, stale, oldest, errors[0] if errors else None))
    return result


def _location_line(health: LocationHealth, stale_after: timedelta) -> str:
    if not health.stale:
        return f"{health.rover}: position data checks recovered"
    line = (
        f"{health.rover}: position data not checked successfully for over "
        f"{stale_after.days} days (last success {_when(health.last_success_at)})"
    )
    if health.last_error:
        line += f"\nerror: {_truncate(health.last_error)}"
    return line


def samples_health(
    dataset: SampleDataset | None, now: datetime, stale_after: timedelta
) -> SamplesHealth:
    """Stale: coalesce(succeeded_at, created_at) is older than ``stale_after``.

    A page that was never attempted (no state row: fresh install, loading off) is not stale.
    """
    if dataset is None:
        return SamplesHealth(False, None, None)
    reference = dataset.succeeded_at or dataset.created_at
    return SamplesHealth(now - reference > stale_after, dataset.succeeded_at, dataset.last_error)


def _samples_line(health: SamplesHealth, stale_after: timedelta) -> str:
    if not health.stale:
        return "perseverance: samples page checks recovered"
    line = (
        "perseverance: samples page not checked successfully for over "
        f"{stale_after.days} days (last success {_when(health.last_success_at)})"
    )
    if health.last_error:
        line += f"\nerror: {_truncate(health.last_error)}"
    return line


def sync_message(run: SyncResult) -> str:
    sols = f"{run.from_sol}..{run.to_sol}" if run.from_sol is not None else "unknown"
    text = (
        f"{PREFIX} sync {run.status}: rover={run.rover_id} mode={run.mode} sols={sols} "
        f"failed_sols={len(run.failed_sols)} run_id={run.run_id}"
    )
    if run.error:
        text += f"\nerror: {_truncate(run.error)}"
    return text


def _when(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else "never"


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_ERROR_CHARS else text[: MAX_ERROR_CHARS - 3] + "..."
