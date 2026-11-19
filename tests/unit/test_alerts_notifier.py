"""Telegram notifier (stage 9, monitoring; agreed interface in app/services/alerts.py).

``TelegramNotifier.send(text) -> bool``: POST ``{TELEGRAM_API_BASE}/bot{token}/sendMessage``
with JSON ``chat_id`` and ``text``; network errors and non-2xx -> ``False`` without raising.
The bot token must never reach the logs. Telegram is mocked with respx (the strict
``upstream_router``); an unmocked request fails the test.

Built from settings with ``TelegramNotifier.from_settings`` (``None`` = alerts disabled).
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.services.alerts import TelegramNotifier

pytestmark = pytest.mark.unit

TOKEN = "123456789:AAFake-Telegram-Token_value"
CHAT_ID = "-1001234567"
API_BASE = "https://telegram.example.test"
SEND_URL = f"{API_BASE}/bot{TOKEN}/sendMessage"
TEXT = "[mars-rover-api] test message"


def telegram_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "telegram_bot_token": SecretStr(TOKEN),
        "telegram_chat_id": CHAT_ID,
        "telegram_api_base": API_BASE,
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


async def make_notifier(settings: Settings) -> Any:
    instance = TelegramNotifier.from_settings(settings)
    assert instance is not None
    return instance


async def close(notifier: Any) -> None:
    closer = getattr(notifier, "aclose", None) or getattr(notifier, "close", None)
    if closer is not None:
        result = closer()
        if hasattr(result, "__await__"):
            await result


@pytest.fixture
async def notifier() -> AsyncIterator[Any]:
    instance = await make_notifier(telegram_settings())
    try:
        yield instance
    finally:
        await close(instance)


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture everything the app logs; httpx at WARNING as configure_logging sets it."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="httpx")
    return caplog


def assert_token_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    assert TOKEN not in caplog.text
    secret_part = TOKEN.split(":", 1)[1]
    assert secret_part not in caplog.text
    for record in caplog.records:
        assert secret_part not in record.getMessage()
        assert secret_part not in str(record.args)


async def test_send_posts_chat_id_and_text_returns_true(
    notifier: Any, upstream_router: respx.MockRouter, logs: pytest.LogCaptureFixture
) -> None:
    route = upstream_router.post(SEND_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    assert await notifier.send(TEXT) is True

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    payload = json.loads(request.content)
    assert str(payload["chat_id"]) == CHAT_ID
    assert payload["text"] == TEXT
    assert_token_not_logged(logs)


async def test_send_uses_configured_api_base(upstream_router: respx.MockRouter) -> None:
    base = "https://tg-proxy.example.test/custom"
    route = upstream_router.post(f"{base}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    instance = await make_notifier(telegram_settings(telegram_api_base=base))
    try:
        assert await instance.send(TEXT) is True
    finally:
        await close(instance)

    assert route.called


async def test_send_keeps_unicode_and_newlines(
    notifier: Any, upstream_router: respx.MockRouter
) -> None:
    text = "[mars-rover-api] curiosity: sync failed\nsol 4321 — ошибка"
    route = upstream_router.post(SEND_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    await notifier.send(text)

    assert json.loads(route.calls.last.request.content)["text"] == text


@pytest.mark.parametrize(
    "status",
    [400, 401, 403, 404, 429, 500, 502],
    ids=lambda s: f"http-{s}",
)
async def test_send_non_2xx_returns_false_without_token_in_logs(
    notifier: Any,
    upstream_router: respx.MockRouter,
    logs: pytest.LogCaptureFixture,
    status: int,
) -> None:
    upstream_router.post(SEND_URL).mock(
        return_value=httpx.Response(status, json={"ok": False, "description": "Bad Request"})
    )

    assert await notifier.send(TEXT) is False

    assert_token_not_logged(logs)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
    ids=["connect-error", "read-timeout", "protocol-error"],
)
async def test_send_network_error_returns_false_without_token_in_logs(
    notifier: Any,
    upstream_router: respx.MockRouter,
    logs: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    upstream_router.post(SEND_URL).mock(side_effect=error)

    assert await notifier.send(TEXT) is False

    assert_token_not_logged(logs)


async def test_send_error_mentioning_url_does_not_log_token(
    notifier: Any, upstream_router: respx.MockRouter, logs: pytest.LogCaptureFixture
) -> None:
    """httpx errors often carry the request URL, which contains the token."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    upstream_router.post(SEND_URL).mock(side_effect=boom)

    assert await notifier.send(TEXT) is False

    assert_token_not_logged(logs)


async def test_notifier_repr_does_not_expose_token(notifier: Any) -> None:
    assert TOKEN.split(":", 1)[1] not in repr(notifier)
    assert TOKEN.split(":", 1)[1] not in str(notifier)


@pytest.mark.parametrize(
    "overrides",
    [
        {"telegram_bot_token": None},
        {"telegram_chat_id": None},
        {"telegram_chat_id": "   "},
    ],
    ids=["no-token", "no-chat-id", "blank-chat-id"],
)
def test_notifier_without_credentials_is_disabled(overrides: dict[str, Any]) -> None:
    assert TelegramNotifier.from_settings(telegram_settings(**overrides)) is None
