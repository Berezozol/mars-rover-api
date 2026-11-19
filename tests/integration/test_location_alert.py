"""Alert on an unchecked location source (stage 11, ADR-8; AlertMonitor.check()).

Fires when `succeeded_at` of a file of Perseverance or Curiosity is empty or older than
LOCATION_STALE_DAYS (3); an unchanged file that is checked successfully is not an alert. One
message on the transition to "not checked", one on recovery, nothing in between. The photo
freshness of both rovers is kept fresh in every test, so only the location state matters.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.alerts import AlertMonitor
from tests._locations import ROVER_KINDS, insert_dataset
from tests.integration._freshness import SYNCED_ROVERS, add_photos, add_run

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

Factory = async_sessionmaker[AsyncSession]
PREFIX = "[mars-rover-api]"
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)


class FakeNotifier:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send(self, text: str) -> bool:
        self.texts.append(text)
        return True


def monitor_for(factory: Factory, settings: Settings, notifier: Any, **extra: Any) -> AlertMonitor:
    values = settings.model_copy(
        update={
            "telegram_bot_token": SecretStr("555000:AAMonitor-Secret-Token"),
            "telegram_chat_id": "4242",
            "sync_stale_hours": 24,
            "photos_stale_days": 21,
            "location_stale_days": 3,
            **extra,
        }
    )
    return AlertMonitor(factory, values, notifier)


async def make_photos_fresh(factory: Factory, now: datetime = NOW, first_sol: int = 100) -> None:
    for rover in SYNCED_ROVERS:
        await add_run(factory, rover, finished_at=now - H)
        await add_photos(factory, rover, [now.date() - D], first_sol=first_sol)


async def seed_locations(
    factory: Factory,
    *,
    succeeded_ago: timedelta | None = H,
    changed_ago: timedelta = 60 * D,
    only: tuple[str, str] | None = None,
    stale_ago: timedelta | None = None,
    created_ago: timedelta = 60 * D,
) -> None:
    """Every file succeeded `succeeded_ago` ago; `only` gets `stale_ago` instead."""
    for rover, kind in ROVER_KINDS:
        ago = stale_ago if (rover, kind) == only else succeeded_ago
        await insert_dataset(
            factory,
            rover,
            kind,
            checked_at=NOW - H,
            succeeded_at=None if ago is None else NOW - ago,
            changed_at=NOW - changed_ago,
            items=10,
            max_sol=2000,
            created_at=NOW - created_ago,
        )


async def set_succeeded(factory: Factory, rover: str, kind: str, when: datetime) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE location_datasets SET succeeded_at = :t WHERE rover_id = :r AND kind = :k"
            ),
            {"t": when, "r": rover, "k": kind},
        )
        await session.commit()


def assert_messages(texts: list[str]) -> None:
    for message in texts:
        assert message.startswith(PREFIX), message


async def test_alert_recent_successful_checks_send_nothing(
    clean_db: Factory, settings: Settings
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)

    await monitor.check(NOW)
    await monitor.check(NOW + H)

    assert notifier.texts == []


async def test_alert_unchanged_content_with_successful_checks_is_not_an_alert(
    clean_db: Factory, settings: Settings
) -> None:
    """The content has not changed for 60 days (the M20 file stood still for weeks)."""
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, succeeded_ago=2 * H, changed_ago=60 * D)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)

    await monitor.check(NOW)
    # A day later every check succeeded again (changed_at stays 60+ days old).
    later = NOW + D
    for rover, kind in ROVER_KINDS:
        await set_succeeded(clean_db, rover, kind, later - H)
    await make_photos_fresh(clean_db, later, first_sol=200)
    await monitor.check(later)

    assert notifier.texts == []


@pytest.mark.parametrize(
    ("age", "alert"),
    [
        pytest.param(71 * H, False, id="71h-fresh"),
        pytest.param(73 * H, True, id="73h-stale"),
        pytest.param(10 * D, True, id="10d-stale"),
    ],
)
async def test_alert_threshold_is_three_days_since_the_last_successful_check(
    clean_db: Factory, settings: Settings, age: timedelta, alert: bool
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, only=("curiosity", "traverse"), stale_ago=age)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert bool(notifier.texts) is alert
    if alert:
        assert_messages(notifier.texts)
        assert "curiosity" in "\n".join(notifier.texts).lower()


async def test_alert_names_the_rover_of_the_stale_file(
    clean_db: Factory, settings: Settings
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, only=("perseverance", "waypoints"), stale_ago=4 * D)
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert len(notifier.texts) == 1
    assert "perseverance" in notifier.texts[0].lower()
    assert "curiosity" not in notifier.texts[0].lower()


@pytest.mark.parametrize(
    ("created_ago", "alert"),
    [
        pytest.param(71 * H, False, id="created-71h-ago-silent"),
        pytest.param(73 * H, True, id="created-73h-ago-alerts"),
    ],
)
async def test_alert_never_succeeded_file_counts_from_its_creation(
    clean_db: Factory, settings: Settings, created_ago: timedelta, alert: bool
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db)
    # A failed first attempt: no success yet, the row was created `created_ago` ago.
    async with clean_db() as session:
        await session.execute(
            text(
                "UPDATE location_datasets SET succeeded_at = NULL, changed_at = NULL, "
                "created_at = :c, last_error = 'boom' "
                "WHERE rover_id = 'curiosity' AND kind = 'waypoints'"
            ),
            {"c": NOW - created_ago},
        )
        await session.commit()
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert bool(notifier.texts) is alert
    if alert:
        assert "curiosity" in chr(10).join(notifier.texts).lower()


async def test_alert_is_sent_once_and_not_repeated(clean_db: Factory, settings: Settings) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, only=("perseverance", "traverse"), stale_ago=4 * D)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)
    await monitor.check(NOW)
    assert len(notifier.texts) == 1

    await monitor.check(NOW + H)
    await monitor.check(NOW + 2 * H)

    assert len(notifier.texts) == 1


async def test_alert_recovery_sends_one_message_then_silence(
    clean_db: Factory, settings: Settings
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, only=("perseverance", "traverse"), stale_ago=4 * D)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)
    await monitor.check(NOW)
    assert len(notifier.texts) == 1

    await set_succeeded(clean_db, "perseverance", "traverse", NOW - H)
    await monitor.check(NOW)

    assert len(notifier.texts) == 2
    assert_messages(notifier.texts)
    await monitor.check(NOW + H)
    assert len(notifier.texts) == 2


async def test_alert_respects_location_stale_days_setting(
    clean_db: Factory, settings: Settings
) -> None:
    await make_photos_fresh(clean_db)
    await seed_locations(clean_db, only=("curiosity", "traverse"), stale_ago=4 * D)
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier, location_stale_days=7).check(NOW)

    assert notifier.texts == []
