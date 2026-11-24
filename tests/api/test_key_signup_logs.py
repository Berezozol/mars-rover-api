"""No address, token, key or SMTP password in the logs, end to end (stage 20B, AC-20).

The whole flow runs through the real app with every log record of every level collected: key
request, sending, confirming, account view, own key, deletion by key, deletion by mail, and the
failure paths (invalid token, bad body, mail errors whose text contains the address). The
access log of uvicorn is emulated as in tests/api/test_api_keys_logs.py (the ASGI transport has
no server). Neither message, arguments, exception text nor extra attributes of a record may
contain a secret.
"""

import contextlib
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app.services.mail import MailerError, MemoryMailer
from tests._keysignup import (
    ACCOUNT,
    ACCOUNT_KEYS,
    CONFIRM,
    DELETION_CONFIRM,
    DELETION_REQUESTS,
    KEY_MAIL,
    REQUESTS,
    SMTP_PASSWORD,
    Collector,
    Signup,
    assert_none_logged,
    make_signup_app,
    token_of,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
EMAIL = "leaky.person@example.org"
LOCAL = "leaky.person"
SECOND = "second.person@example.net"


@pytest.fixture
def logs() -> Iterator[Collector]:
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
    """The app's `configure_logging` drops existing handlers: attach after it is built."""
    root = logging.getLogger()
    if collector not in root.handlers:
        root.addHandler(collector)
    root.setLevel(logging.DEBUG)


class LeakyMailer(MemoryMailer):
    """Fails like a real SMTP error would: with the address in the text of the error."""

    async def send(self, message: Any) -> None:
        error = MailerError(550)
        error.args = (f"550 5.1.1 <{message['To']}>: recipient rejected, user {LOCAL}",)
        raise error


async def test_ac20_the_whole_flow_leaves_no_secret_in_the_logs(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    se = await make_signup_app(make_keys_app)
    attached(logs)

    await se.request_key(EMAIL)
    await se.run()
    token = token_of(se.mailer.sent[0])
    confirmed = await se.confirm(token)
    key = confirmed.json()["key"]
    keyed = se.client(headers={"X-Api-Key": key})
    await keyed.get(ACCOUNT)
    second = (await keyed.post(ACCOUNT_KEYS)).json()
    await keyed.delete(f"{ACCOUNT_KEYS}/{second['key_info']['id']}")
    await se.confirm(token)  # an invalid token
    await se.confirm("Z" * 43)  # a random token
    await se.post(REQUESTS, {"email": "not an address"})  # a validation error
    await se.post(DELETION_REQUESTS, {"email": SECOND})  # an unknown address
    await se.post(DELETION_REQUESTS, {"email": EMAIL})
    await se.run()
    delete_token = token_of(se.mailer.sent[-1])
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.5:50000", "POST", CONFIRM, "1.1", 201
    )
    await se.post(DELETION_CONFIRM, {"token": delete_token})
    await se.env.admin().get(KEY_MAIL)

    assert logs.records, "nothing was logged at all: the collector is not attached"
    assert_none_logged(
        logs,
        EMAIL,
        LOCAL,
        SECOND,
        token,
        delete_token,
        key,
        key[3:],
        second["key"],
        SMTP_PASSWORD,
    )


async def test_ac20_the_sender_logs_ids_and_outcomes_but_not_addresses(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    se = await make_signup_app(make_keys_app)
    attached(logs)

    await se.request_key(EMAIL)
    await se.run()

    assert_none_logged(logs, EMAIL, LOCAL, token_of(se.mailer.sent[0]))


async def test_ac20_a_mailer_error_with_the_address_logs_only_the_class_and_the_code(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    se = await make_signup_app(make_keys_app, mailer=LeakyMailer())
    attached(logs)
    await se.request_key(EMAIL)

    await se.run()

    assert_none_logged(logs, EMAIL, LOCAL, "recipient rejected")
    assert "550" in logs.everything(), "the SMTP code must be logged"


async def test_ac20_every_retry_of_a_failing_mail_stays_clean(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    from tests._keysignup import make_due

    se = await make_signup_app(make_keys_app, mailer=LeakyMailer(), key_mail_max_attempts=3)
    attached(logs)
    await se.request_key(EMAIL)

    for _ in range(3):
        await se.run()
        await make_due(se.factory)

    assert_none_logged(logs, EMAIL, LOCAL, "recipient rejected")
    assert any(r.levelno >= logging.WARNING for r in logs.records), "a failure must be visible"


async def test_ac20_a_failing_pass_does_not_leak_through_the_exception_log(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    class Exploding(MemoryMailer):
        async def send(self, message: Any) -> None:
            raise RuntimeError(f"cannot reach {message['To']} with {SMTP_PASSWORD}")

    se = await make_signup_app(make_keys_app, mailer=Exploding())
    attached(logs)
    await se.request_key(EMAIL)

    # The pass may let an unexpected error out; the scheduler job logs only its class.
    with contextlib.suppress(RuntimeError):
        await se.run()

    assert_none_logged(logs, SMTP_PASSWORD)


async def test_ac20_the_scheduler_job_logs_only_the_class_of_an_unexpected_error(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    from app.services.key_mail import send_key_mail

    class Exploding(MemoryMailer):
        async def send(self, message: Any) -> None:
            raise RuntimeError(f"cannot reach {message['To']}")

    se: Signup = await make_signup_app(make_keys_app, mailer=Exploding())
    attached(logs)
    await se.request_key(EMAIL)

    await send_key_mail(se.outbox)

    assert_none_logged(logs, EMAIL, LOCAL)


async def test_ac20_rejected_requests_do_not_log_the_body(
    make_keys_app: MakeApp, logs: Collector
) -> None:
    se = await make_signup_app(make_keys_app)
    attached(logs)

    await se.post(REQUESTS, {"email": EMAIL, "extra": "field"})
    await se.post(REQUESTS, {"email": EMAIL + " "})
    await se.post(CONFIRM, {"token": "short" + LOCAL})
    await se.client().post(
        REQUESTS,
        content=b'{"email": "' + EMAIL.encode() + b'"}',
        headers={"Content-Type": "text/plain"},
    )

    assert_none_logged(logs, EMAIL, LOCAL, "short" + LOCAL)
