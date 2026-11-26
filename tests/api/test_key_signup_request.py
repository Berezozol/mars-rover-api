"""Asking for a key by e-mail (stage 20B, AC-2 ... AC-6, AC-12, AC-23).

`POST /api/v2/keys/requests` only queues a mail and answers `202 {"status": "accepted",
"mail_delayed": <bool>}`; the sender (`KeyMailOutbox.run_once()`, "run_outbox()" in the spec)
creates the token when the mail goes out. The answer must not depend on the address (AC-3).
Notation: E - a new address, E2 - an address of an active account, EB - an address of a
blocked account. SMTP is a `MemoryMailer`; the clock of the sign-up limiter is fake.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests._apikeys import IP_A, account_with_key, create_account, create_key, scalar
from tests._keysignup import (
    ACCEPTED,
    BASE_URL,
    DELETION_REQUESTS,
    MAIL_FROM,
    MAIL_FROM_NAME,
    REQUESTS,
    SMTP_PASSWORD,
    assert_error,
    block_account,
    count,
    dump_tables,
    insert_request,
    last_mail_to,
    link_of,
    make_due,
    make_signup_app,
    recipients,
    request_rows,
    retry_after_seconds,
    sha256,
    token_of,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
E = "new.person@example.org"
E2 = "known@example.org"
EB = "blocked@example.org"


# --- AC-2 ---


async def test_ac2_request_answers_202_and_queues_one_pending_row(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.request_key(E)

    assert response.status_code == 202, response.text
    assert response.json() == ACCEPTED
    assert response.headers["cache-control"] == "no-store"
    queued = await request_rows(se.factory)
    assert len(queued) == 1
    assert queued[0]["email"] == E
    assert queued[0]["purpose"] == "key"
    assert queued[0]["status"] == "pending"
    assert queued[0]["token_hash"] is None
    assert queued[0]["attempts"] == 0
    assert se.mailer.sent == [], "the answer must not wait for the mail"


async def test_ac2_sender_mails_a_link_and_stores_only_the_hash_of_the_token(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_token_ttl_minutes=60)
    await se.request_key(E)

    await se.run()

    assert recipients(se.mailer) == [E]
    message = se.mailer.sent[0]
    token = token_of(message)
    assert len(token) == 43
    assert link_of(message) == f"{BASE_URL}/keys/confirm#token={token}"
    (row,) = await request_rows(se.factory)
    assert row["status"] == "sent"
    assert row["token_hash"] is not None and bytes(row["token_hash"]) == sha256(token)
    assert row["sent_at"] is not None and row["used_at"] is None
    assert (
        await scalar(
            se.factory,
            "SELECT expires_at = sent_at + make_interval(mins => 60) FROM api_key_requests",
        )
        is True
    )
    assert token not in await dump_tables(se.factory)


@pytest.mark.parametrize("minutes", [5, 90], ids=["short-ttl", "long-ttl"])
async def test_ac2_token_lifetime_follows_key_token_ttl_minutes(
    make_keys_app: MakeApp, minutes: int
) -> None:
    se = await make_signup_app(make_keys_app, key_token_ttl_minutes=minutes)
    await se.request_key(E)
    await se.run()

    assert (
        await scalar(
            se.factory,
            "SELECT expires_at - sent_at = make_interval(mins => :m) FROM api_key_requests",
            m=minutes,
        )
        is True
    )


async def test_ac2_the_mail_is_plain_utf8_text_from_the_configured_sender(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, mail_reply_to="help@sender.example")
    await se.request_key(E)
    await se.run()

    message = se.mailer.sent[0]
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert MAIL_FROM in str(message["From"])
    assert MAIL_FROM_NAME in str(message["From"])
    assert E in str(message["To"])
    assert str(message["Subject"]).strip() != ""
    assert "help@sender.example" in str(message["Reply-To"])


async def test_ac2_mail_for_a_known_address_is_sent_too(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)

    await se.request_key(E2)
    await se.run()

    assert recipients(se.mailer) == [E2]
    assert (await request_rows(se.factory))[0]["status"] == "sent"


async def test_ac2_each_mail_gets_its_own_token(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    await se.request_key(E)
    await se.run()

    tokens = [token_of(m) for m in se.mailer.sent]
    assert len(tokens) == 2 and len(set(tokens)) == 2


# --- AC-3: the same answer for every address ---


def comparable(response: httpx.Response) -> tuple[int, bytes, list[tuple[str, str]]]:
    headers = [(k, v) for k, v in sorted(response.headers.items()) if k != "x-request-id"]
    return response.status_code, response.content, headers


async def test_ac3_every_kind_of_address_gets_the_same_answer(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)
    await account_with_key(se.factory, E2)
    await create_account(se.factory, EB, blocked=True, block_reason="abuse")
    await se.request_key(E)  # E is now at its daily limit

    answers = {
        "new": await se.request_key("fresh@example.org"),
        "known": await se.request_key(E2),
        "blocked": await se.request_key(EB),
        "over-limit": await se.request_key(E),
        "honeypot": await se.request_key("bot@example.org", website="http://spam.example"),
    }

    reference = comparable(answers["new"])
    assert reference[0] == 202
    assert json.loads(reference[1]) == ACCEPTED
    for name, response in answers.items():
        assert comparable(response) == reference, name


async def test_ac3_mails_go_only_to_new_and_known_addresses_within_the_limit(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)
    await account_with_key(se.factory, E2)
    await create_account(se.factory, EB, blocked=True, block_reason="abuse")

    for email in (E, E2, EB, E):
        await se.request_key(email)
    await se.request_key("bot@example.org", website="x")
    await se.run()

    assert sorted(recipients(se.mailer)) == sorted([E, E2])
    queued = await request_rows(se.factory)
    sent = [r["email"] for r in queued if r["status"] == "sent"]
    assert sorted(sent) == sorted([E, E2])
    # Every valid request leaves a row (equal work for every address); the unwanted ones are
    # `skipped`: the blocked address, the second E and the trap.
    skipped = sorted(r["email"] for r in queued if r["status"] == "skipped")
    assert skipped == sorted([EB, E, "bot@example.org"])


@pytest.mark.parametrize("website", ["x", "http://spam.example", " "], ids=["x", "url", "space"])
async def test_ac3_a_filled_trap_field_gets_202_and_no_mail(
    make_keys_app: MakeApp, website: str
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.request_key(E, website=website)

    assert response.status_code == 202 and response.json() == ACCEPTED
    await se.run()
    assert se.mailer.sent == []
    queued = await request_rows(se.factory)
    assert [(r["email"], r["status"]) for r in queued] == [(E, "skipped")]
    assert queued[0]["token_hash"] is None


async def test_ac3_an_empty_trap_field_is_a_normal_request(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.request_key(E, website="")

    assert response.status_code == 202
    await se.run()
    assert recipients(se.mailer) == [E]


async def test_ac3_the_limit_per_address_is_three_mails_a_day_by_default(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)

    codes = [(await se.request_key(E)).status_code for _ in range(5)]
    await se.run()

    assert codes == [202] * 5
    assert recipients(se.mailer) == [E] * 3


async def test_ac3_key_and_deletion_mails_share_the_limit_of_an_address(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=2)
    await account_with_key(se.factory, E2)

    await se.request_key(E2)
    await se.post(DELETION_REQUESTS, {"email": E2})
    third = await se.post(DELETION_REQUESTS, {"email": E2})
    await se.run()

    assert third.status_code == 202 and third.json() == ACCEPTED
    assert len(se.mailer.sent) == 2


async def test_ac3_old_requests_do_not_count_against_the_daily_limit(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)
    await insert_request(se.factory, E, status="sent", created_hours_ago=25, expires_in_hours=-24)

    await se.request_key(E)
    await se.run()

    assert recipients(se.mailer) == [E]


async def test_ac3_the_address_is_case_insensitive_and_stored_in_lower_case(
    make_keys_app: MakeApp,
) -> None:
    """Edge case of the spec: `User@Example.org` and `user@example.org` are one address."""
    se = await make_signup_app(make_keys_app, key_signup_per_email_per_day=1)

    await se.request_key("User@Example.org")
    await se.request_key("user@example.org")
    await se.run()

    queued = await request_rows(se.factory)
    assert [(r["email"], r["status"]) for r in queued] == [
        ("user@example.org", "sent"),
        ("user@example.org", "skipped"),
    ]
    assert recipients(se.mailer) == ["user@example.org"]


async def test_ac3_a_known_account_is_found_whatever_the_case_of_the_request(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_account(se.factory, EB, blocked=True, block_reason="abuse")

    await se.request_key("Blocked@Example.ORG")
    await se.run()

    assert se.mailer.sent == []


async def test_ac3_a_blocked_account_after_the_request_gets_no_mail(
    make_keys_app: MakeApp,
) -> None:
    """Edge case: blocked between the request and the sending - no mail and no token."""
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    await se.request_key(E2)
    await block_account(se.factory, E2)

    await se.run()

    assert se.mailer.sent == []
    (row,) = await request_rows(se.factory)
    assert row["status"] != "sent"
    assert row["token_hash"] is None


# --- AC-4: validation ---

BLOCKED_DOMAINS = "spam.example,Other-Spam.example"

BAD_EMAILS = [
    pytest.param("no-at.example.org", id="no-at"),
    pytest.param("a@b@example.org", id="two-at"),
    pytest.param("a b@example.org", id="space-inside"),
    pytest.param(" a@example.org", id="leading-space"),
    pytest.param("a@example.org ", id="trailing-space"),
    pytest.param("a\r@example.org", id="cr"),
    pytest.param("a@example.org\nBcc: x@example.org", id="lf-header-injection"),
    pytest.param("a\x00@example.org", id="nul"),
    pytest.param("a" * 243 + "@example.org", id="longer-than-254"),
    pytest.param("a@localhost", id="domain-without-dot"),
    pytest.param("a@", id="empty-domain"),
    pytest.param("@example.org", id="empty-local-part"),
    pytest.param("", id="empty"),
    pytest.param("üser@example.org", id="non-ascii-local"),
    pytest.param("a@exämple.org", id="non-ascii-domain"),
    pytest.param("a@spam.example", id="blocked-domain"),
    pytest.param("a@SPAM.example", id="blocked-domain-upper-case"),
    pytest.param("a@other-spam.example", id="blocked-domain-listed-in-mixed-case"),
]


@pytest.mark.parametrize("email", BAD_EMAILS)
async def test_ac4_a_bad_address_is_422_and_nothing_is_queued(
    make_keys_app: MakeApp, email: str
) -> None:
    se = await make_signup_app(make_keys_app, key_email_blocked_domains=BLOCKED_DOMAINS)

    response = await se.request_key(email)

    assert_error(response, 422, "validation_error")
    assert await count(se.factory, "api_key_requests") == 0


async def test_ac4_an_address_of_exactly_254_characters_is_accepted(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    email = "a" * 242 + "@example.org"
    assert len(email) == 254

    response = await se.request_key(email)

    assert response.status_code == 202


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-email"),
        pytest.param({"email": None}, id="email-null"),
        pytest.param({"email": 5}, id="email-number"),
        pytest.param({"email": ["a@example.org"]}, id="email-list"),
        pytest.param({"email": "a@example.org", "extra": 1}, id="extra-field"),
        pytest.param({"email": "a@example.org", "website": 5}, id="trap-not-a-string"),
        pytest.param([], id="body-list"),
        pytest.param(["a@example.org"], id="body-list-with-email"),
        pytest.param("a@example.org", id="body-string"),
        pytest.param(42, id="body-number"),
        pytest.param(None, id="body-null"),
    ],
)
@pytest.mark.parametrize("path", [REQUESTS, DELETION_REQUESTS], ids=["key", "deletion"])
async def test_ac4_a_malformed_body_is_422(make_keys_app: MakeApp, path: str, body: Any) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.post(path, body)

    assert_error(response, 422, "validation_error")
    assert await count(se.factory, "api_key_requests") == 0


@pytest.mark.parametrize(
    "email",
    ["User.Name+tag@Example.ORG", "a@sub.example.org", "a_b-c@example-site.org"],
)
async def test_ac4_ordinary_addresses_are_accepted(make_keys_app: MakeApp, email: str) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.request_key(email)

    assert response.status_code == 202
    assert [r["email"] for r in await request_rows(se.factory)] == [email.lower()]


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-content-type"),
        pytest.param({"Content-Type": "text/plain"}, id="text-plain"),
        pytest.param({"Content-Type": "application/x-www-form-urlencoded"}, id="form"),
    ],
)
@pytest.mark.parametrize("path", [REQUESTS, DELETION_REQUESTS], ids=["key", "deletion"])
async def test_ac4_a_body_that_is_not_declared_json_is_415(
    make_keys_app: MakeApp, path: str, headers: dict[str, str]
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().post(path, content=b'{"email": "a@example.org"}', headers=headers)

    assert_error(response, 415, "unsupported_media_type")
    assert await count(se.factory, "api_key_requests") == 0


async def test_ac4_json_with_a_charset_parameter_is_accepted(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().post(
        REQUESTS,
        content=b'{"email": "a@example.org"}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 202


@pytest.mark.parametrize("path", [REQUESTS, DELETION_REQUESTS], ids=["key", "deletion"])
async def test_ac4_a_body_over_2_kib_is_413(make_keys_app: MakeApp, path: str) -> None:
    se = await make_signup_app(make_keys_app)
    body = json.dumps({"email": "a@example.org", "website": "x" * 3000}).encode()
    assert len(body) > 2048

    response = await se.client().post(
        path, content=body, headers={"Content-Type": "application/json"}
    )

    assert_error(response, 413, "payload_too_large")
    assert await count(se.factory, "api_key_requests") == 0


async def test_ac4_a_body_of_exactly_2_kib_is_not_too_large(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    filler = 2048 - len(json.dumps({"email": "a@example.org", "website": ""}).encode())
    body = json.dumps({"email": "a@example.org", "website": "x" * filler}).encode()
    assert len(body) == 2048

    response = await se.client().post(
        REQUESTS, content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 202  # the trap field is filled: accepted without a mail


# --- AC-5: limit per IP ---


async def test_ac5_requests_over_the_hourly_limit_of_an_ip_get_429(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=3)

    codes = [(await se.request_key(f"u{i}@example.org", IP_A)).status_code for i in range(3)]
    blocked = await se.request_key("u3@example.org", IP_A)

    assert codes == [202, 202, 202]
    assert_error(blocked, 429, "rate_limited")
    assert retry_after_seconds(blocked) <= 3600
    assert await count(se.factory, "api_key_requests") == 3


async def test_ac5_another_ip_is_not_affected(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=2)
    for i in range(2):
        await se.request_key(f"u{i}@example.org", IP_A)

    other = await se.request_key("other@example.org", "203.0.113.77")

    assert other.status_code == 202


async def test_ac5_the_window_slides_after_an_hour(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=2)
    for i in range(2):
        await se.request_key(f"u{i}@example.org", IP_A)
    assert (await se.request_key("late@example.org", IP_A)).status_code == 429

    se.env.clock.advance(3601)

    assert (await se.request_key("late@example.org", IP_A)).status_code == 202


async def test_ac5_deletion_requests_use_the_same_budget_of_the_ip(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=2)
    await se.request_key("a@example.org", IP_A)
    await se.post(DELETION_REQUESTS, {"email": "b@example.org"}, IP_A)

    third = await se.post(DELETION_REQUESTS, {"email": "c@example.org"}, IP_A)

    assert_error(third, 429, "rate_limited")


async def test_ac5_the_request_is_also_charged_to_the_ordinary_ip_limit(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)

    first = await se.request_key("a@example.org", IP_A)
    second = await se.request_key("b@example.org", IP_A)

    assert first.headers["x-ratelimit-limit"] == "60"
    assert int(first.headers["x-ratelimit-remaining"]) == 59
    assert int(second.headers["x-ratelimit-remaining"]) == 58


@pytest.mark.parametrize(
    ("first", "second", "second_blocked"),
    [
        pytest.param("2001:db8:1:2::1", "2001:db8:1:2:ffff::5", True, id="same-64"),
        pytest.param("2001:db8:1:2::1", "2001:db8:1:3::1", True, id="different-64-same-48"),
        pytest.param("2001:db8:1:2::1", "2001:db8:1:ffff::1", True, id="48-edge"),
        pytest.param("2001:db8:1:2::1", "2001:db8:2:2::1", False, id="different-48"),
    ],
)
async def test_ac5_ipv6_is_counted_per_48_network(
    make_keys_app: MakeApp, first: str, second: str, second_blocked: bool
) -> None:
    """HIGH-2 (2026-11-26): "one IP" of an IPv6 client is a whole /48 network."""
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=1)
    assert (await se.request_key("a@example.org", first)).status_code == 202

    other = await se.request_key("b@example.org", second)

    assert (other.status_code == 429) is second_blocked


# --- AC-6: global daily limit ---


async def test_ac6_over_the_global_limit_any_request_gets_503_mail_unavailable(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=3)
    for i in range(3):
        assert (await se.request_key(f"u{i}@example.org")).status_code == 202

    blocked = await se.request_key("another@example.org")
    known = await se.request_key("u0@example.org")

    assert_error(blocked, 503, "mail_unavailable")
    assert retry_after_seconds(blocked) >= 1
    assert_error(known, 503, "mail_unavailable")
    assert await count(se.factory, "api_key_requests") == 3


async def test_ac6_deletion_requests_are_blocked_by_the_global_limit_too(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=1)
    await se.request_key("u@example.org")

    blocked = await se.post(DELETION_REQUESTS, {"email": "v@example.org"})

    assert_error(blocked, 503, "mail_unavailable")


async def test_ac6_the_limit_is_read_from_the_database_after_a_restart(
    make_keys_app: MakeApp,
) -> None:
    first = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    for i in range(2):
        await first.request_key(f"u{i}@example.org")

    restarted = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    blocked = await restarted.request_key("late@example.org")

    assert_error(blocked, 503, "mail_unavailable")


async def test_ac6_skipped_requests_count_in_the_global_limit_but_send_no_mail(
    make_keys_app: MakeApp,
) -> None:
    """Every valid request writes a row (a `skipped` one when no mail is wanted) and the
    server limit counts all rows, so the 503 does not depend on the address."""
    se = await make_signup_app(
        make_keys_app, key_signup_global_per_day=3, key_signup_per_email_per_day=1
    )
    first = await se.request_key(E)
    over_address_limit = await se.request_key(E)
    trap = await se.request_key("bot@example.org", website="x")

    blocked = await se.request_key("second@example.org")
    await se.run()

    assert [first.status_code, over_address_limit.status_code, trap.status_code] == [202] * 3
    assert_error(blocked, 503, "mail_unavailable")
    assert recipients(se.mailer) == [E]
    assert sorted(r["status"] for r in await request_rows(se.factory)) == [
        "sent",
        "skipped",
        "skipped",
    ]


async def test_ac6_a_full_limit_does_not_tell_whether_an_account_exists(
    make_keys_app: MakeApp,
) -> None:
    """Enumeration (S3): with the limit full, a deletion request for a known and for an
    unknown address both answer 503; unknown ones fill the limit like known ones."""
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    await account_with_key(se.factory, E2)
    for _ in range(2):
        assert (
            await se.post(DELETION_REQUESTS, {"email": "nobody@example.org"})
        ).status_code == 202

    known = await se.post(DELETION_REQUESTS, {"email": E2})
    unknown = await se.post(DELETION_REQUESTS, {"email": "other@example.org"})

    assert_error(known, 503, "mail_unavailable")
    assert_error(unknown, 503, "mail_unavailable")
    assert [r["status"] for r in await request_rows(se.factory)] == ["skipped", "skipped"]


NOON = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("status", ["pending", "sent", "failed", "skipped", "dropped"])
async def test_ac6_rows_of_every_status_count(make_keys_app: MakeApp, status: str) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=1)
    se.freeze_signup_clock(NOON)
    await insert_request(
        se.factory, "a@example.org", status=status, created_at=NOON - timedelta(hours=2)
    )

    response = await se.request_key("fresh@example.org")

    assert_error(response, 503, "mail_unavailable")


async def test_ac6_a_full_limit_answers_with_the_seconds_to_midnight_utc(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    now = datetime(2026, 12, 1, 18, 30, 15, tzinfo=UTC)
    se.freeze_signup_clock(now)
    for i in range(2):
        assert (await se.request_key(f"u{i}@example.org")).status_code == 202

    response = await se.request_key("late@example.org")

    assert_error(response, 503, "mail_unavailable")
    assert response.headers["retry-after"] == str(5 * 3600 + 29 * 60 + 45)
    error = response.json()["error"]
    assert error["details"] == {"resets_at": "2026-12-02T00:00:00Z"}
    assert "2026-12-02" in error["message"]


@pytest.mark.parametrize(
    ("now", "seconds", "resets_at"),
    [
        pytest.param(
            datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC),
            86400,
            "2026-12-02T00:00:00Z",
            id="just-after-midnight",
        ),
        pytest.param(
            datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC),
            1,
            "2026-12-02T00:00:00Z",
            id="last-second",
        ),
        pytest.param(
            datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC),
            43200,
            "2027-01-01T00:00:00Z",
            id="new-year",
        ),
    ],
)
async def test_ac6_the_reset_time_is_the_next_utc_midnight(
    make_keys_app: MakeApp, now: datetime, seconds: int, resets_at: str
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=1)
    se.freeze_signup_clock(now)
    await se.request_key("a@example.org")

    response = await se.request_key("b@example.org")

    assert_error(response, 503, "mail_unavailable")
    assert response.headers["retry-after"] == str(seconds)
    assert response.json()["error"]["details"] == {"resets_at": resets_at}


async def test_ac6_after_midnight_utc_requests_are_accepted_again(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    se.freeze_signup_clock(datetime(2026, 12, 1, 23, 59, 0, tzinfo=UTC))
    for i in range(2):
        await se.request_key(f"u{i}@example.org")
    assert_error(await se.request_key("x@example.org"), 503, "mail_unavailable")

    se.freeze_signup_clock(datetime(2026, 12, 2, 0, 0, 0, tzinfo=UTC))
    accepted = await se.request_key("x@example.org")

    assert accepted.status_code == 202
    assert accepted.json() == ACCEPTED


async def test_ac6_rows_of_yesterday_do_not_count_even_a_minute_before_midnight(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    se.freeze_signup_clock(NOON)
    yesterday_late = datetime(2026, 11, 30, 23, 59, 0, tzinfo=UTC)
    await insert_request(se.factory, "a@example.org", status="sent", created_at=yesterday_late)
    await insert_request(se.factory, "b@example.org", status="pending", created_at=yesterday_late)
    await insert_request(
        se.factory, "c@example.org", status="failed", created_at=yesterday_late - timedelta(days=3)
    )

    response = await se.request_key("fresh@example.org")

    assert response.status_code == 202


async def test_ac6_only_todays_rows_count(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=2)
    se.freeze_signup_clock(NOON)
    await insert_request(
        se.factory, "y@example.org", status="sent", created_at=NOON - timedelta(days=1)
    )
    await insert_request(
        se.factory, "a@example.org", status="pending", created_at=datetime(2026, 12, 1, tzinfo=UTC)
    )

    assert (await se.request_key("b@example.org")).status_code == 202
    assert_error(await se.request_key("c@example.org"), 503, "mail_unavailable")


async def test_ac6_the_default_global_limit_is_50_a_day(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    se.freeze_signup_clock(NOON)
    for i in range(50):
        await insert_request(
            se.factory, f"u{i}@example.org", status="skipped", created_at=NOON - timedelta(hours=1)
        )

    response = await se.request_key("fresh@example.org")

    assert_error(response, 503, "mail_unavailable")
    assert (await se.env.admin().get("/api/v2/admin/key-mail")).json()["global_limit"] == 50


# --- AC-12: mail_delayed ---


async def test_ac12_after_a_failed_send_every_answer_says_mail_delayed(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    await create_account(se.factory, EB, blocked=True, block_reason="abuse")
    assert (await se.request_key(E)).json() == ACCEPTED
    se.mailer.fail_next = 1

    await se.run()

    for email in ("fresh@example.org", E2, EB):
        response = await se.request_key(email)
        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "mail_delayed": True}, email
    trap = await se.request_key("bot@example.org", website="x")
    assert trap.json() == {"status": "accepted", "mail_delayed": True}
    deletion = await se.post(DELETION_REQUESTS, {"email": "fresh@example.org"})
    assert deletion.json() == {"status": "accepted", "mail_delayed": True}


async def test_ac12_a_successful_send_after_the_failure_clears_the_flag(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    se.mailer.fail_next = 1
    await se.run()
    assert (await se.request_key("x@example.org")).json()["mail_delayed"] is True

    await make_due(se.factory)
    await se.run()  # the retry of the first mail and the second request go out

    assert recipients(se.mailer)[0] == E
    assert (await se.request_key("y@example.org")).json() == ACCEPTED


async def test_ac12_the_flag_goes_off_after_the_degraded_window_without_a_success(
    make_keys_app: MakeApp,
) -> None:
    """`KEY_MAIL_DEGRADED_MINUTES` (15): an old failure no longer says "delayed".

    Uses the private `_clock` of the outbox (a public `clock` attribute would be better).
    """
    se = await make_signup_app(make_keys_app, key_mail_degraded_minutes=15)
    await se.request_key(E)
    start = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    se.freeze_outbox_clock(start)
    se.mailer.fail_next = 1
    await se.run()

    se.freeze_outbox_clock(start + timedelta(minutes=14))
    inside = await se.request_key("a@example.org")
    se.freeze_outbox_clock(start + timedelta(minutes=16))
    outside = await se.request_key("b@example.org")

    assert inside.json()["mail_delayed"] is True
    assert outside.json()["mail_delayed"] is False


async def test_ac12_the_flag_is_off_while_nothing_has_failed(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    await se.run()

    assert (await se.request_key("y@example.org")).json() == ACCEPTED


# --- AC-23: the link never takes its host from the request ---


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8443", "api.solindex.example"])
async def test_ac23_the_link_uses_public_base_url_whatever_the_host_header(
    make_keys_app: MakeApp, host: str
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client(headers={"Host": host}).post(REQUESTS, json={"email": E})
    assert response.status_code == 202, response.text
    await se.run()

    message = last_mail_to(se.mailer, E)
    assert link_of(message).startswith(f"{BASE_URL}/keys/confirm#token=")
    assert "evil.example" not in str(message)
    assert "api.solindex.example" not in str(message)


async def test_ac23_x_forwarded_host_does_not_change_the_link_either(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)

    await se.client(headers={"X-Forwarded-Host": "evil.example"}).post(REQUESTS, json={"email": E})
    await se.run()

    message = last_mail_to(se.mailer, E)
    assert link_of(message).startswith(BASE_URL + "/")
    assert "evil.example" not in str(message)


async def test_ac23_the_deletion_link_is_built_the_same_way(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)

    await se.client(headers={"Host": "evil.example"}).post(DELETION_REQUESTS, json={"email": E2})
    await se.run()

    message = last_mail_to(se.mailer, E2)
    assert link_of(message).startswith(f"{BASE_URL}/keys/delete#token=")
    assert "evil.example" not in str(message)


# --- nothing about the secrets in the stored rows ---


async def test_ac2_the_smtp_password_is_not_in_the_mail_or_the_tables(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_key(se.factory, await create_account(se.factory, E2))
    await se.request_key(E)
    await se.run()

    assert SMTP_PASSWORD not in str(se.mailer.sent[0])
    assert SMTP_PASSWORD not in await dump_tables(se.factory)
