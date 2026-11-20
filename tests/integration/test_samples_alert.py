"""Alert on an unchecked samples page (stage 13, D9; AlertMonitor.check()).

Fires when `succeeded_at` of the Perseverance samples state is empty or older than
SAMPLES_STALE_DAYS (3); a page that has not changed for weeks but is checked successfully is
not an alert; a rejected page is not a success. One message on the transition to "not
checked", one on recovery, nothing in between. Photo and location freshness are kept fresh in
every test, so only the samples state matters.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings
from tests._locations import ROVER_KINDS
from tests._samples import Factory, insert_dataset
from tests.integration.test_location_alert import (
    NOW,
    FakeNotifier,
    assert_messages,
    make_photos_fresh,
    monitor_for,
    seed_locations,
)
from tests.integration.test_location_alert import set_succeeded as set_location_succeeded

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

H = timedelta(hours=1)
D = timedelta(days=1)


async def seed_samples(
    factory: Factory,
    *,
    succeeded_ago: timedelta | None = H,
    changed_ago: timedelta | None = 60 * D,
    created_ago: timedelta = 60 * D,
    last_error: str | None = None,
) -> None:
    await insert_dataset(
        factory,
        checked_at=NOW - H,
        succeeded_at=None if succeeded_ago is None else NOW - succeeded_ago,
        changed_at=None if changed_ago is None else NOW - changed_ago,
        created_at=NOW - created_ago,
        items=30,
        last_error=last_error,
    )


async def set_succeeded(factory: Factory, when: datetime) -> None:
    async with factory() as session:
        await session.execute(
            text("UPDATE sample_datasets SET succeeded_at = :t WHERE rover_id = 'perseverance'"),
            {"t": when},
        )
        await session.commit()


async def prepare(factory: Factory) -> None:
    await make_photos_fresh(factory)
    await seed_locations(factory)


def mentions_samples(texts: list[str]) -> bool:
    return "sample" in "\n".join(texts).lower()


async def test_alert_recent_successful_checks_send_nothing(
    clean_db: Factory, settings: Settings
) -> None:
    await prepare(clean_db)
    await seed_samples(clean_db)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)

    await monitor.check(NOW)
    await monitor.check(NOW + H)

    assert notifier.texts == []


async def test_alert_unchanged_content_with_successful_checks_is_not_an_alert(
    clean_db: Factory, settings: Settings
) -> None:
    """The page has not changed for 60 days (it is updated months behind the mission)."""
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=2 * H, changed_ago=60 * D)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)

    await monitor.check(NOW)
    later = NOW + D
    await set_succeeded(clean_db, later - H)
    for rover, kind in ROVER_KINDS:  # keep the location sources fresh a day later
        await set_location_succeeded(clean_db, rover, kind, later - H)
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
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=age)
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert bool(notifier.texts) is alert
    if alert:
        assert len(notifier.texts) == 1
        assert_messages(notifier.texts)
        assert mentions_samples(notifier.texts)


async def test_alert_for_a_rejected_page_counts_from_the_last_success(
    clean_db: Factory, settings: Settings
) -> None:
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=4 * D, last_error="layout changed")
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert len(notifier.texts) == 1
    assert mentions_samples(notifier.texts)


@pytest.mark.parametrize(
    ("created_ago", "alert"),
    [
        pytest.param(71 * H, False, id="created-71h-ago-silent"),
        pytest.param(73 * H, True, id="created-73h-ago-alerts"),
    ],
)
async def test_alert_never_succeeded_state_counts_from_its_creation(
    clean_db: Factory, settings: Settings, created_ago: timedelta, alert: bool
) -> None:
    await prepare(clean_db)
    await seed_samples(
        clean_db,
        succeeded_ago=None,
        changed_ago=None,
        created_ago=created_ago,
        last_error="boom",
    )
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert bool(notifier.texts) is alert
    if alert:
        assert mentions_samples(notifier.texts)


async def test_alert_is_sent_once_and_not_repeated(clean_db: Factory, settings: Settings) -> None:
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=4 * D)
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
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=4 * D)
    notifier = FakeNotifier()
    monitor = monitor_for(clean_db, settings, notifier)
    await monitor.check(NOW)
    assert len(notifier.texts) == 1

    await set_succeeded(clean_db, NOW - H)
    await monitor.check(NOW)

    assert len(notifier.texts) == 2
    assert_messages(notifier.texts)
    assert mentions_samples(notifier.texts[1:])
    await monitor.check(NOW + H)
    assert len(notifier.texts) == 2


async def test_alert_respects_samples_stale_days_setting(
    clean_db: Factory, settings: Settings
) -> None:
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=4 * D)
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier, samples_stale_days=7).check(NOW)

    assert notifier.texts == []


async def test_alert_for_samples_is_independent_of_the_location_alert(
    clean_db: Factory, settings: Settings
) -> None:
    """Stale samples and fresh locations: exactly one message, and it names the samples."""
    await prepare(clean_db)
    await seed_samples(clean_db, succeeded_ago=5 * D)
    notifier = FakeNotifier()

    await monitor_for(clean_db, settings, notifier).check(NOW)

    assert len(notifier.texts) == 1
    assert "waypoints" not in notifier.texts[0].lower()
