"""Periodic incremental sync of every syncable rover (APScheduler, inside the app)."""

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.services.alerts import AlertMonitor
from app.services.location_sync import LocationSyncService
from app.services.sync import SyncResult, SyncService
from app.sources.http import UpstreamClient
from app.sources.registry import SYNCABLE_ROVERS, build_adapter

logger = logging.getLogger(__name__)

JOB_ID = "sync-all"


class SyncScheduler:
    """One interval job that syncs the rovers one after another."""

    def __init__(
        self,
        service: SyncService,
        client: UpstreamClient,
        settings: Settings,
        locations: LocationSyncService | None = None,
    ) -> None:
        self._service = service
        self._client = client
        self._settings = settings
        self._locations = locations
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._current: asyncio.Task[list[SyncResult]] | None = None

    def start(self) -> None:
        """Schedule the job (first run right away) and start; call inside the event loop."""
        self._scheduler.add_job(
            self._job,
            "interval",
            minutes=self._settings.sync_interval_minutes,
            id=JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            next_run_time=datetime.now(UTC),
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Sync scheduler started interval_minutes=%d", self._settings.sync_interval_minutes
        )

    async def shutdown(self) -> None:
        """Stop scheduling and cancel a sync that is still running."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        task = self._current
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Sync job failed while shutting down")
        logger.info("Sync scheduler stopped")

    async def _job(self) -> None:
        task = asyncio.ensure_future(self.run_once())
        self._current = task
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
            # Cancelled by shutdown(): not a job failure.
            logger.info("Scheduled sync cancelled by shutdown")
        finally:
            self._current = None

    async def run_once(self) -> list[SyncResult]:
        """Incremental sync of every syncable rover; one failure does not stop the others.

        Then the rover position files that are due (at most once a day each); their failures
        never affect the photo results.
        """
        results: list[SyncResult] = []
        for rover_id in SYNCABLE_ROVERS:
            try:
                adapter = build_adapter(rover_id, self._client, self._settings)
                results.append(await self._service.run(adapter))
            except SyncInProgress:
                logger.info("Scheduled sync skipped rover=%s: already in progress", rover_id)
            except Exception:
                logger.exception("Scheduled sync failed rover=%s", rover_id)
        await self._refresh_locations()
        return results

    async def _refresh_locations(self) -> None:
        if self._locations is None:
            return
        try:
            await self._locations.refresh_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled location refresh failed")


ALERT_JOB_ID = "alert-check"


class AlertScheduler:
    """Freshness check for alerts every ALERT_CHECK_INTERVAL_MINUTES (first one at start)."""

    def __init__(self, monitor: AlertMonitor, settings: Settings) -> None:
        self._monitor = monitor
        self._settings = settings
        self._scheduler = AsyncIOScheduler(timezone=UTC)

    def start(self) -> None:
        """Schedule the check and start; call inside the event loop."""
        self._scheduler.add_job(
            self._job,
            "interval",
            minutes=self._settings.alert_check_interval_minutes,
            id=ALERT_JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            next_run_time=datetime.now(UTC),
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Alert scheduler started interval_minutes=%d",
            self._settings.alert_check_interval_minutes,
        )

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _job(self) -> None:
        try:
            await self._monitor.check(datetime.now(UTC))
        except Exception:
            logger.exception("Alert check failed")
