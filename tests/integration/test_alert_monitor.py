"""Alert monitor (stage 9 monitoring; agreed interface in app/services/alerts.py).

- ``check(now)``: a message on ok -> stale (also on the first call if already stale), a
  recovery message on stale -> ok, nothing when the state is unchanged.
- ``on_sync_finished(run)``: ``failed`` or ``partial`` -> a message; ``success`` -> none.
- Every text starts with ``[mars-rover-api]``; the bot token never appears; at most 300
  characters of a run's error text are quoted.

The notifier is a fake with the same ``send`` contract, except in the end-to-end tests that
go through ``TelegramNotifier.from_settings`` and respx. ``on_sync_finished`` receives the
``SyncResult`` the sync service hands to its listeners.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.alerts import AlertMonitor, TelegramNotifier
from app.services.sync import SyncResult
from tests.integration._freshness import SYNCED_ROVERS, add_photos, add_run

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]

PREFIX = "[mars-rover-api]"
TOKEN = "555000:AAMonitor-Secret-Token"
CHAT_ID = "4242"
API_BASE = "https://telegram.example.test"
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)


class FakeNotifier:
    def __init__(self, result: bool = True) -> None:
        self.texts: list[str] = []
        self.result = result

    async def send(self, text: str) -> bool:
        self.texts.append(text)
        return self.result


def alert_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "telegram_bot_token": SecretStr(TOKEN),
            "telegram_chat_id": CHAT_ID,
            "telegram_api_base": API_BASE,
            "sync_stale_hours": 24,
            "photos_stale_days": 21,
        }
    )


def make_monitor(factory: Factory, settings: Settings, notifier: Any) -> Any:
    return AlertMonitor(factory, alert_settings(settings), notifier)


def assert_messages(texts: list[str]) -> None:
    for text in texts:
        assert text.startswith(PREFIX), text
        assert TOKEN not in text
        assert TOKEN.split(":", 1)[1] not in text


async def make_fresh(factory: Factory, rover_id: str, now: datetime = NOW) -> None:
    await add_run(factory, rover_id, finished_at=now - H)
    # Distinct sols per call, so a second call adds a newer sol instead of reusing one.
    await add_photos(factory, rover_id, [now.date() - D], first_sol=100 + (now - NOW).days)


def make_run(**values: Any) -> SyncResult:
    """The finished run as the sync service reports it to its listeners."""
    row: dict[str, Any] = {
        "run_id": 17,
        "rover_id": "curiosity",
        "mode": "incremental",
        "from_sol": 4300,
        "to_sol": 4310,
        "status": "failed",
        "photos_upserted": 0,
        "failed_sols": [],
        "error": "UpstreamError: 503 from source",
    }
    row.update(values)
    return SyncResult(**row)


# --- check(now): state transitions ---------------------------------------------------------


async def test_check_first_call_when_fresh_sends_nothing(
    clean_db: Factory, settings: Settings
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)
    await monitor.check(NOW + H)

    assert notifier.texts == []


async def test_check_first_call_when_already_stale_sends_alert(
    clean_db: Factory, settings: Settings
) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert notifier.texts, "already stale on start must alert"
    assert_messages(notifier.texts)


async def test_check_same_stale_state_does_not_repeat(
    clean_db: Factory, settings: Settings
) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)
    await monitor.check(NOW)
    sent = len(notifier.texts)

    await monitor.check(NOW + H)
    await monitor.check(NOW + 2 * H)

    assert len(notifier.texts) == sent


async def test_check_ok_to_stale_sends_alert_naming_the_rover(
    clean_db: Factory, settings: Settings
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    # Perseverance keeps syncing; Curiosity's last success is the one above.
    later = NOW + 2 * D
    await make_fresh(clean_db, "perseverance", later)
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)
    await monitor.check(NOW)
    assert notifier.texts == []

    await monitor.check(later)

    assert notifier.texts
    assert_messages(notifier.texts)
    joined = "\n".join(notifier.texts).lower()
    assert "curiosity" in joined


async def test_check_stale_to_ok_sends_recovery_once(clean_db: Factory, settings: Settings) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)
    await monitor.check(NOW)
    alerts = len(notifier.texts)
    assert alerts

    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    await monitor.check(NOW)

    recovery = notifier.texts[alerts:]
    assert recovery, "stale -> ok must send a recovery message"
    assert_messages(recovery)

    await monitor.check(NOW + H)
    assert len(notifier.texts) == alerts + len(recovery)


async def test_check_photos_stale_alone_triggers_alert(
    clean_db: Factory, settings: Settings
) -> None:
    await make_fresh(clean_db, "curiosity")
    await add_run(clean_db, "perseverance", finished_at=NOW - H)
    await add_photos(clean_db, "perseverance", [NOW.date() - 30 * D])
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert notifier.texts
    assert_messages(notifier.texts)
    assert "perseverance" in "\n".join(notifier.texts).lower()


async def test_check_mer_rovers_never_alert(clean_db: Factory, settings: Settings) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    await add_photos(clean_db, "spirit", [datetime(2010, 3, 21).date()])
    await add_run(clean_db, "opportunity", status="failed", finished_at=NOW - 40 * D)
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert notifier.texts == []


async def test_check_failed_send_does_not_raise(clean_db: Factory, settings: Settings) -> None:
    notifier = FakeNotifier(result=False)
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert notifier.texts


async def test_check_failed_send_is_retried_on_next_check(
    clean_db: Factory, settings: Settings
) -> None:
    """The state is remembered only after a successful send: an unsent alert is resent."""
    notifier = FakeNotifier(result=False)
    monitor = make_monitor(clean_db, settings, notifier)
    await monitor.check(NOW)
    unsent = list(notifier.texts)
    assert unsent

    notifier.result = True
    await monitor.check(NOW)
    resent = notifier.texts[len(unsent) :]
    assert resent == unsent, "the same alert must be sent once Telegram is back"

    await monitor.check(NOW)
    assert len(notifier.texts) == 2 * len(unsent), "after a delivered alert: silence"


# --- on_sync_finished(run) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "extra"),
    [
        pytest.param("failed", {"failed_sols": []}, id="failed"),
        pytest.param("partial", {"failed_sols": [4301, 4305], "error": None}, id="partial"),
    ],
)
async def test_on_sync_finished_failed_or_partial_sends_message(
    clean_db: Factory, settings: Settings, status: str, extra: dict[str, Any]
) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.on_sync_finished(make_run(status=status, **extra))

    assert len(notifier.texts) == 1
    assert_messages(notifier.texts)
    text = notifier.texts[0].lower()
    assert "curiosity" in text
    assert status in text


async def test_on_sync_finished_success_sends_nothing(
    clean_db: Factory, settings: Settings
) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.on_sync_finished(make_run(status="success", error=None))

    assert notifier.texts == []


async def test_on_sync_finished_long_error_is_truncated(
    clean_db: Factory, settings: Settings
) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(clean_db, settings, notifier)
    error = "E" * 5000

    await monitor.on_sync_finished(make_run(status="failed", error=error))

    [text] = notifier.texts
    assert text.startswith(PREFIX)
    assert "E" * 301 not in text, "at most 300 characters of the error text"
    assert len(text) < 1000


async def test_on_sync_finished_failed_send_does_not_raise(
    clean_db: Factory, settings: Settings
) -> None:
    notifier = FakeNotifier(result=False)
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.on_sync_finished(make_run(status="failed"))

    assert len(notifier.texts) == 1


# --- end to end through Telegram -----------------------------------------------------------


async def test_on_sync_finished_posts_to_telegram_without_token_in_logs(
    clean_db: Factory,
    settings: Settings,
    upstream_router: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="httpx")
    route = upstream_router.post(f"{API_BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notifier = TelegramNotifier.from_settings(alert_settings(settings))
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.on_sync_finished(make_run(status="failed", error="boom"))

    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert str(payload["chat_id"]) == CHAT_ID
    assert payload["text"].startswith(PREFIX)
    assert TOKEN.split(":", 1)[1] not in caplog.text


async def test_check_telegram_down_does_not_raise_nor_log_token(
    clean_db: Factory,
    settings: Settings,
    upstream_router: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="httpx")
    route = upstream_router.post(f"{API_BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    notifier = TelegramNotifier.from_settings(alert_settings(settings))
    monitor = make_monitor(clean_db, settings, notifier)

    await monitor.check(NOW)

    assert route.called
    assert TOKEN.split(":", 1)[1] not in caplog.text
