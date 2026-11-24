"""Findings of the stage 20B review and security review as tests (AC-3, AC-4, AC-11, AC-27).

- S1: an address is an addr-spec only; strings that a mail library reads as several or
  different recipients are `422`, and the mail has exactly one recipient.
- S2 / MAJOR 1: a failure that is not `MailerError` on one request of a batch must not undo or
  repeat the mails already sent, and must not stop the ones after it.
- S5: Gmail dot and `+tag` variants are one mailbox for the per-address limit.
- MINOR 4: a mail dropped because the account was blocked or deleted is not a failure in
  the admin `key-mail` counters.
"""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text

from app.services.mail import MailerError, MemoryMailer
from tests._apikeys import account_with_key, create_account
from tests._keysignup import (
    DELETION_REQUESTS,
    KEY_MAIL,
    REQUESTS,
    assert_error,
    block_account,
    count,
    make_signup_app,
    recipient,
    recipients,
    request_rows,
    token_of,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
E = "new.person@example.org"

MULTI_RECIPIENT = [
    pytest.param("v@x.example,=?utf-8?q?b=40y.example?=", id="comma-and-encoded-word"),
    pytest.param("v@x.example,b@y.example", id="comma"),
    pytest.param("x<v@x.example>", id="display-name"),
    pytest.param("<v@x.example>", id="angle-brackets"),
    pytest.param("v@x.example(1)", id="comment"),
    pytest.param("():v@x.example", id="group-syntax"),
    pytest.param("=?utf-8?q?a=0d=0ab?=@x.example", id="encoded-crlf-in-local-part"),
    pytest.param("a;b@x.example", id="semicolon"),
    pytest.param('"a b"@x.example', id="quoted-local-part"),
    pytest.param("a@x.example;b@y.example", id="semicolon-list"),
    pytest.param("a@[127.0.0.1]", id="address-literal"),
    pytest.param("a@x_y.example", id="underscore-in-domain"),
    pytest.param("a@.example.org", id="leading-dot-domain"),
    pytest.param("a@example..org", id="empty-label"),
    pytest.param(".a@example.org", id="leading-dot-local"),
    pytest.param("a..b@example.org", id="double-dot-local"),
]


@pytest.mark.parametrize("path", [REQUESTS, DELETION_REQUESTS], ids=["key", "deletion"])
@pytest.mark.parametrize("email", MULTI_RECIPIENT)
async def test_ac4_s1_an_address_that_is_not_one_plain_mailbox_is_422(
    make_keys_app: MakeApp, path: str, email: str
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.post(path, {"email": email})

    assert_error(response, 422, "validation_error")
    assert await count(se.factory, "api_key_requests") == 0


async def test_ac4_s1_a_mail_has_exactly_one_recipient(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key("A.b+tag@Example.org")

    await se.run()

    (message,) = se.mailer.sent
    assert recipient(message) == "a.b+tag@example.org"
    assert str(message["To"]) == "a.b+tag@example.org"
    assert message.get_all("To") == ["a.b+tag@example.org"]
    assert message["Cc"] is None and message["Bcc"] is None


# --- S2 / MAJOR 1 ---


class PoisonedMailer(MemoryMailer):
    """The `poison`-th call (1-based) fails with something that is not a `MailerError`."""

    def __init__(self, poison: int) -> None:
        super().__init__()
        self.poison = poison
        self.calls = 0

    async def send(self, message: Any) -> None:
        self.calls += 1
        if self.calls == self.poison:
            raise ValueError(f"cannot build a mail for {message['To']}")
        await super().send(message)


async def test_ac11_a_failure_on_the_second_mail_keeps_the_first_and_sends_the_third(
    make_keys_app: MakeApp,
) -> None:
    mailer = PoisonedMailer(poison=2)
    se = await make_signup_app(make_keys_app, mailer=mailer)
    for name in ("one", "two", "three"):
        await se.request_key(f"{name}@example.org")

    await se.run()

    assert recipients(mailer) == ["one@example.org", "three@example.org"]
    by_email = {r["email"]: r for r in await request_rows(se.factory)}
    assert by_email["one@example.org"]["status"] == "sent"
    assert by_email["one@example.org"]["token_hash"] is not None
    assert by_email["two@example.org"]["status"] == "failed"
    assert by_email["two@example.org"]["token_hash"] is None
    assert by_email["three@example.org"]["status"] == "sent"
    # the link of the first mail works
    response = await se.confirm(token_of(mailer.sent[0]))
    assert response.status_code == 201, response.text
    assert response.json()["account"]["email"] == "one@example.org"


async def test_ac11_a_later_pass_does_not_resend_what_went_out(make_keys_app: MakeApp) -> None:
    mailer = PoisonedMailer(poison=2)
    se = await make_signup_app(make_keys_app, mailer=mailer)
    for name in ("one", "two", "three"):
        await se.request_key(f"{name}@example.org")
    await se.run()

    await se.run()
    await se.run()

    assert recipients(mailer) == ["one@example.org", "three@example.org"]


async def test_ac11_a_failure_that_is_not_a_mailer_error_is_not_retried(
    make_keys_app: MakeApp,
) -> None:
    mailer = PoisonedMailer(poison=1)
    se = await make_signup_app(make_keys_app, mailer=mailer)
    await se.request_key(E)
    await se.run()

    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "failed"
    assert mailer.calls == 1


async def test_ac11_a_mailer_error_on_one_mail_of_a_batch_still_lets_the_others_go(
    make_keys_app: MakeApp,
) -> None:
    class SecondFails(MemoryMailer):
        calls = 0

        async def send(self, message: Any) -> None:
            self.calls += 1
            if self.calls == 2:
                raise MailerError(451)
            await super().send(message)

    mailer = SecondFails()
    se = await make_signup_app(make_keys_app, mailer=mailer)
    for name in ("one", "two", "three"):
        await se.request_key(f"{name}@example.org")

    await se.run()

    assert recipients(mailer) == ["one@example.org", "three@example.org"]
    by_email = {r["email"]: r for r in await request_rows(se.factory)}
    assert by_email["two@example.org"]["status"] == "pending"
    assert by_email["two@example.org"]["attempts"] == 1


# --- S5: Gmail variants ---


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("a.b@gmail.com", "ab+1@gmail.com", id="dot-and-tag"),
        pytest.param("ab@gmail.com", "a.b@gmail.com", id="plain-and-dot"),
        pytest.param("ab+x@gmail.com", "ab+y@gmail.com", id="two-tags"),
        pytest.param("ab@gmail.com", "ab@googlemail.com", id="googlemail"),
    ],
)
async def test_ac3_s5_gmail_variants_share_one_address_limit(
    make_keys_app: MakeApp, first: str, second: str
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)

    first_answer = await se.request_key(first)
    second_answer = await se.request_key(second)
    await se.run()

    assert first_answer.json() == second_answer.json()
    assert recipients(se.mailer) == [first]
    queued = await request_rows(se.factory)
    # stored as given; the second is skipped
    assert [(r["email"], r["status"]) for r in queued] == [(first, "sent"), (second, "skipped")]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("a.b@example.org", "ab@example.org", id="dots-count-elsewhere"),
        pytest.param("ab+1@example.org", "ab+2@example.org", id="tags-count-elsewhere"),
        pytest.param("ab@gmail.com", "ac@gmail.com", id="different-gmail-mailboxes"),
    ],
)
async def test_ac3_s5_other_addresses_keep_their_own_limit(
    make_keys_app: MakeApp, first: str, second: str
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)

    await se.request_key(first)
    await se.request_key(second)
    await se.run()

    assert sorted(recipients(se.mailer)) == sorted([first, second])


async def test_ac3_s5_the_stored_gmail_address_is_not_changed(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    await se.request_key("A.B+Tag@Gmail.com")
    await se.run()

    assert recipients(se.mailer) == ["a.b+tag@gmail.com"]


# --- MINOR 4: dropped is not failed ---


async def test_ac27_a_mail_dropped_for_a_blocked_account_is_not_a_failure(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, "known@example.org")
    await se.request_key("known@example.org")
    await block_account(se.factory, "known@example.org")

    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "dropped"
    assert row["token_hash"] is None and se.mailer.sent == []
    body = (await se.env.admin().get(KEY_MAIL)).json()
    assert body["failed_24h"] == 0
    assert body["pending"] == 0
    assert body["last_error_at"] is None


async def test_ac27_a_deletion_mail_dropped_for_a_deleted_account_is_not_a_failure(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_account(se.factory, "gone@example.org")
    await se.post(DELETION_REQUESTS, {"email": "gone@example.org"})
    async with se.factory() as session:
        await session.execute(text("DELETE FROM api_accounts"))
        await session.commit()

    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "dropped"
    assert (await se.env.admin().get(KEY_MAIL)).json()["failed_24h"] == 0


async def test_ac27_a_real_failure_still_counts_next_to_a_dropped_mail(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_mail_max_attempts=1)
    await account_with_key(se.factory, "known@example.org")
    await se.request_key("known@example.org")
    await block_account(se.factory, "known@example.org")
    await se.request_key("other@example.org")
    se.mailer.fail_next = 1

    await se.run()

    body = (await se.env.admin().get(KEY_MAIL)).json()
    assert body["failed_24h"] == 1
    statuses = sorted(r["status"] for r in await request_rows(se.factory))
    assert statuses == ["dropped", "failed"]


async def test_ac27_skipped_requests_are_not_pending_or_failed(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key("bot@example.org", website="x")

    body = (await se.env.admin().get(KEY_MAIL)).json()

    assert body["pending"] == 0 and body["failed_24h"] == 0 and body["sent_24h"] == 0
