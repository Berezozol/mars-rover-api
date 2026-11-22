"""No key value in the application logs, end to end (stage 20A, AC-32).

Requests with a key in `X-Api-Key` and in `?api_key=` (valid, unknown, revoked, blocked, rate
limited, failing handler; v1 and v2) run through the real app while every log record of every
level is collected. Neither the message, the arguments, the exception text nor the extra
attributes of a record may contain the key. The access log of uvicorn is emulated the way
tests/unit/test_log_redaction.py does it (the ASGI transport has no server access log).
"""

import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests._apikeys import (
    BOOM_V2,
    IP_A,
    SMALL_V1,
    SMALL_V2,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    new_key,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text_of(self, record: logging.LogRecord) -> str:
        parts = [record.getMessage(), str(record.args), record.exc_text or ""]
        if record.exc_info:
            parts.append(str(record.exc_info[1]))
        parts.extend(str(value) for value in record.__dict__.values())
        return "\n".join(parts)


@pytest.fixture
def logs() -> Iterator[Collector]:
    """A handler on the root logger (the app's `configure_logging` drops existing handlers, so
    it is attached after the app is built, see `attached`)."""
    collector = Collector()
    root = logging.getLogger()
    saved_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        root.removeHandler(collector)
        root.setLevel(saved_level)


def attached(collector: Collector) -> None:
    root = logging.getLogger()
    if collector not in root.handlers:
        root.addHandler(collector)
    root.setLevel(logging.DEBUG)


def assert_no_key(collector: Collector, *keys: str) -> None:
    for record in collector.records:
        text = collector.text_of(record)
        for key in keys:
            assert key not in text, (record.name, record.levelname, text)
            assert key[3:] not in text, (record.name, record.levelname, text)


async def test_ac32_valid_key_in_header_and_query_is_not_logged(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    attached(logs)
    async with env.client(IP_A) as client:
        await client.get(SMALL_V2, headers={"X-Api-Key": key.value})
        await client.get(SMALL_V2, params={"api_key": key.value})
        await client.get(SMALL_V1, params={"api_key": key.value})
        await client.get(SMALL_V1, headers={"X-Api-Key": key.value})
        await client.get("/api/v2/rovers", headers={"X-Api-Key": key.value})
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.5:50000",
        "GET",
        f"{SMALL_V2}?api_key={key.value}",
        "1.1",
        200,
    )

    assert_no_key(logs, key.value)


async def test_ac32_rejected_keys_are_not_logged(make_keys_app: MakeApp, logs: Collector) -> None:
    env: KeysApp = await make_keys_app()
    unknown = new_key()
    revoked = (
        await create_key(
            env.factory, await create_account(env.factory, "r@example.org"), revoked=True
        )
    ).value
    blocked = (
        await create_key(
            env.factory,
            await create_account(env.factory, "b@example.org", blocked=True, block_reason="abuse"),
        )
    ).value
    attached(logs)
    async with env.client(IP_A) as client:
        for key in (unknown, revoked, blocked):
            await client.get(SMALL_V2, headers={"X-Api-Key": key})
            await client.get(SMALL_V2, params={"api_key": key})
            await client.get(SMALL_V1, headers={"X-Api-Key": key})
            await client.get(SMALL_V1, params={"api_key": key})

    assert_no_key(logs, unknown, revoked, blocked)


async def test_ac32_lockout_and_rate_limit_responses_do_not_log_the_key(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=2, api_key_auth_max_failures=2)
    key = await account_with_key(env.factory)
    stranger = new_key()
    attached(logs)
    async with env.client(IP_A, {"X-Api-Key": key.value}) as keyed:
        for _ in range(3):
            await keyed.get(SMALL_V2)  # the third one is 429
    async with env.client("203.0.113.99") as client:
        for _ in range(3):  # two failures, then the lockout
            await client.get(SMALL_V2, headers={"X-Api-Key": stranger})
        await client.get(SMALL_V2, headers={"X-Api-Key": key.value})

    assert_no_key(logs, key.value, stranger)


async def test_ac32_a_failing_handler_does_not_log_the_key(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    attached(logs)
    async with env.client(IP_A) as client:
        response = await client.get(BOOM_V2, headers={"X-Api-Key": key.value})
        by_query = await client.get(BOOM_V2, params={"api_key": key.value})

    assert response.status_code == 500
    assert by_query.status_code == 500
    assert any(r.levelno >= logging.ERROR for r in logs.records), "the failure was not logged"
    assert_no_key(logs, key.value)


async def test_ac32_admin_key_issue_does_not_log_the_new_key(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    env: KeysApp = await make_keys_app()
    attached(logs)
    async with env.admin() as admin:
        response = await admin.post("/api/v2/admin/accounts", json={"email": "log@example.org"})
    assert response.status_code == 201, response.text
    issued = response.json()["key"]

    assert_no_key(logs, issued)
