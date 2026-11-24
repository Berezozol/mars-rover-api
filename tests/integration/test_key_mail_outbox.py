"""The mail queue of stage 20B: retries, restarts, parallel passes, cleanup (AC-11, AC-13, AC-24).

The queue is the table `api_key_requests`. The sender is `KeyMailOutbox.run_once()`; SMTP is a
`MemoryMailer` (`fail_next` makes the next sends fail with `MailerError`). Time is not slept: a
retry is made due by moving `next_attempt_at` in the database, and the schedule is checked as
the distance from "now" of the database.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.services.api_keys import cleanup_api_keys
from app.services.mail import MailerError, MemoryMailer
from tests._apikeys import Factory
from tests._keysignup import (
    SlowMailer,
    count,
    insert_request,
    make_due,
    make_signup_app,
    recipients,
    request_rows,
    seconds_until_next_attempt,
    token_of,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
E = "new.person@example.org"


# --- AC-11: failures and the retry schedule ---


async def test_ac11_a_failed_send_keeps_the_row_pending_and_counts_the_attempt(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    se.mailer.fail_next = 1

    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["token_hash"] is None and row["sent_at"] is None and row["expires_at"] is None
    assert se.mailer.sent == []


async def test_ac11_the_retries_follow_1_5_15_minutes_and_the_fourth_failure_is_final(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(
        make_keys_app, key_mail_retry_minutes="1,5,15", key_mail_max_attempts=4
    )
    await se.request_key(E)
    se.mailer.fail_next = 4
    (row,) = await request_rows(se.factory)

    waits = []
    for _ in range(3):
        await se.run()
        waits.append(await seconds_until_next_attempt(se.factory, row["id"]))
        await make_due(se.factory)
    await se.run()

    for seconds, minutes in zip(waits, (1, 5, 15), strict=True):
        assert minutes * 60 - 30 <= seconds <= minutes * 60 + 5, waits
    (final,) = await request_rows(se.factory)
    assert final["status"] == "failed"
    assert final["attempts"] == 4
    assert final["token_hash"] is None
    assert se.mailer.sent == []
    assert se.mailer.fail_next == 0


async def test_ac11_the_pause_after_each_failure_is_exactly_the_schedule(
    make_keys_app: MakeApp,
) -> None:
    """With the sender's clock fixed the next attempt is `now + 1, 5, 15` minutes."""
    se = await make_signup_app(make_keys_app, key_mail_retry_minutes="1,5,15")
    await se.request_key(E)
    se.mailer.fail_next = 3
    at = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    pauses = []
    for _ in range(3):
        se.freeze_outbox_clock(at)
        await se.run()
        (row,) = await request_rows(se.factory)
        pauses.append(row["next_attempt_at"] - at)
        at = row["next_attempt_at"]

    assert pauses == [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15)]


async def test_ac11_a_retry_before_its_time_does_not_send_or_count(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    se.mailer.fail_next = 1
    await se.run()

    await se.run()  # not due yet

    (row,) = await request_rows(se.factory)
    assert row["attempts"] == 1 and row["status"] == "pending"
    assert se.mailer.sent == []


async def test_ac11_a_retry_that_succeeds_sends_one_mail_with_a_working_token(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    se.mailer.fail_next = 2
    await se.run()
    await make_due(se.factory)
    await se.run()
    await make_due(se.factory)

    await se.run()

    assert recipients(se.mailer) == [E]
    (row,) = await request_rows(se.factory)
    assert row["status"] == "sent" and row["attempts"] in (2, 3)
    assert row["token_hash"] is not None
    response = await se.confirm(token_of(se.mailer.sent[0]))
    assert response.status_code == 201


async def test_ac11_a_token_lives_from_the_moment_the_mail_went_out(make_keys_app: MakeApp) -> None:
    """The lifetime counts from sending, not from the request (a delayed mail is still usable)."""
    se = await make_signup_app(make_keys_app, key_token_ttl_minutes=60)
    await se.request_key(E)
    async with se.factory() as session:
        from sqlalchemy import text

        await session.execute(
            text("UPDATE api_key_requests SET created_at = now() - interval '2 hours'")
        )
        await session.commit()

    await se.run()

    assert (
        await _scalar(se, "SELECT expires_at > now() + interval '55 minutes' FROM api_key_requests")
        is True
    )


async def _scalar(se: Any, sql: str) -> Any:
    from tests._apikeys import scalar

    return await scalar(se.factory, sql)


@pytest.mark.parametrize("max_attempts", [1, 2], ids=["one-attempt", "two-attempts"])
async def test_ac11_key_mail_max_attempts_is_respected(
    make_keys_app: MakeApp, max_attempts: int
) -> None:
    se = await make_signup_app(make_keys_app, key_mail_max_attempts=max_attempts)
    await se.request_key(E)
    se.mailer.fail_next = 10

    for _ in range(max_attempts):
        await se.run()
        await make_due(se.factory)
    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "failed"
    assert row["attempts"] == max_attempts
    assert se.mailer.fail_next == 10 - max_attempts


async def test_ac11_a_failed_row_is_not_picked_up_again(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_mail_max_attempts=1)
    await se.request_key(E)
    se.mailer.fail_next = 1
    await se.run()

    await make_due(se.factory)
    await se.run()
    await se.run()

    assert se.mailer.sent == []
    assert (await request_rows(se.factory))[0]["status"] == "failed"


async def test_ac11_one_failing_mail_does_not_stop_the_others(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    for i in range(3):
        await se.request_key(f"u{i}@example.org")
    se.mailer.fail_next = 1

    await se.run()

    assert len(se.mailer.sent) == 2
    statuses = sorted(r["status"] for r in await request_rows(se.factory))
    assert statuses == ["pending", "sent", "sent"]


async def test_ac11_a_mailer_error_with_an_address_in_it_does_not_break_the_sender(
    make_keys_app: MakeApp,
) -> None:
    class Leaky(MemoryMailer):
        async def send(self, message: Any) -> None:
            raise MailerError(550)

    se = await make_signup_app(make_keys_app, mailer=Leaky())
    await se.request_key(E)

    await se.run()

    (row,) = await request_rows(se.factory)
    assert row["status"] == "pending" and row["attempts"] == 1


async def test_ac11_a_token_is_not_created_while_the_mail_has_not_gone(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    se.mailer.fail_next = 1
    await se.run()

    response = await se.confirm("A" * 43)

    assert response.status_code == 400
    assert await count(se.factory, "api_key_requests", "token_hash IS NOT NULL") == 0


# --- AC-13: restart and parallel passes ---


async def test_ac13_pending_rows_are_sent_by_a_restarted_application(
    make_keys_app: MakeApp,
) -> None:
    first = await make_signup_app(make_keys_app)
    for i in range(3):
        await first.request_key(f"u{i}@example.org")
    assert first.mailer.sent == []

    restarted = await make_signup_app(make_keys_app)
    await restarted.run()

    assert sorted(recipients(restarted.mailer)) == [f"u{i}@example.org" for i in range(3)]
    assert {r["status"] for r in await request_rows(restarted.factory)} == {"sent"}


async def test_ac13_two_passes_at_once_send_every_mail_exactly_once(
    make_keys_app: MakeApp,
) -> None:
    mailer = SlowMailer()
    se = await make_signup_app(make_keys_app, mailer=mailer)
    addresses = [f"u{i}@example.org" for i in range(6)]
    for address in addresses:
        await se.request_key(address)

    await asyncio.gather(se.run(), se.run())

    assert sorted(recipients(mailer)) == addresses
    assert {r["status"] for r in await request_rows(se.factory)} == {"sent"}


async def test_ac13_two_application_instances_do_not_double_send(make_keys_app: MakeApp) -> None:
    mailer = SlowMailer()
    first = await make_signup_app(make_keys_app, mailer=mailer)
    second = await make_signup_app(make_keys_app, mailer=mailer)
    addresses = [f"u{i}@example.org" for i in range(5)]
    for address in addresses:
        await first.request_key(address)

    await asyncio.gather(first.run(), second.run())

    assert sorted(recipients(mailer)) == addresses


async def test_ac13_a_second_pass_finds_nothing_to_send(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)
    await se.run()

    await se.run()

    assert recipients(se.mailer) == [E]


async def test_ac13_a_pass_takes_at_most_ten_rows_and_the_next_one_the_rest(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=100)
    for i in range(12):
        await insert_request(se.factory, f"u{i}@example.org", status="pending")

    await se.run()
    after_first = len(se.mailer.sent)
    await se.run()

    assert after_first <= 12
    assert len(se.mailer.sent) == 12


@pytest.mark.parametrize("enabled", [True, False], ids=["job-on", "job-off"])
async def test_ac13_the_key_scheduler_runs_send_key_mail_only_when_enabled(
    make_keys_app: MakeApp, enabled: bool
) -> None:
    """The job `send_key_mail` picks up retries and what a restart left (AC-13); with
    `KEY_MAIL_JOB_ENABLED=false` (the tests' setting) it is not scheduled."""
    se = await make_signup_app(make_keys_app, key_mail_job_enabled=enabled)
    scheduler = se.env.app.state.api_key_scheduler

    assert scheduler is not None
    ids = {job.id for job in scheduler._scheduler.get_jobs()}
    assert any("mail" in job_id for job_id in ids) is enabled, ids


# --- AC-24: cleanup of the queue ---


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


async def left(factory: Factory) -> set[str]:
    return {r["email"] for r in await request_rows(factory)}


async def test_ac24_cleanup_drops_old_sent_failed_and_pending_rows_only(
    settings: Settings, session_factory: Factory, now: datetime
) -> None:
    await insert_request(session_factory, "sent-old@x.org", status="sent", expires_in_hours=-25)
    await insert_request(session_factory, "sent-recent@x.org", status="sent", expires_in_hours=-23)
    await insert_request(session_factory, "sent-live@x.org", status="sent", expires_in_hours=0.5)
    await insert_request(session_factory, "failed-old@x.org", status="failed", created_hours_ago=25)
    await insert_request(session_factory, "failed-new@x.org", status="failed", created_hours_ago=1)
    await insert_request(session_factory, "pend-old@x.org", status="pending", created_hours_ago=25)
    await insert_request(session_factory, "pend-new@x.org", status="pending", created_hours_ago=1)

    await cleanup_api_keys(session_factory, settings, now)

    assert await left(session_factory) == {
        "sent-recent@x.org",
        "sent-live@x.org",
        "failed-new@x.org",
        "pend-new@x.org",
    }


@pytest.mark.parametrize("purpose", ["key", "delete"])
async def test_ac24_both_purposes_are_cleaned(
    settings: Settings, session_factory: Factory, now: datetime, purpose: str
) -> None:
    await insert_request(
        session_factory, "old@x.org", purpose=purpose, status="sent", expires_in_hours=-48
    )
    await insert_request(
        session_factory, "new@x.org", purpose=purpose, status="sent", expires_in_hours=1
    )

    await cleanup_api_keys(session_factory, settings, now)

    assert await left(session_factory) == {"new@x.org"}


async def test_ac24_a_failed_row_is_judged_by_created_at_not_by_expires_at(
    settings: Settings, session_factory: Factory, now: datetime
) -> None:
    """A failed row has no `expires_at` (no token was ever made)."""
    await insert_request(session_factory, "failed-old@x.org", status="failed", created_hours_ago=26)
    await insert_request(session_factory, "failed-new@x.org", status="failed", created_hours_ago=2)
    assert all(r["expires_at"] is None for r in await request_rows(session_factory))

    await cleanup_api_keys(session_factory, settings, now)

    assert await left(session_factory) == {"failed-new@x.org"}


async def test_ac24_cleanup_does_not_touch_accounts_or_keys(
    settings: Settings, session_factory: Factory, now: datetime
) -> None:
    from tests._apikeys import account_with_key

    await account_with_key(session_factory, "keep@example.org")
    await insert_request(session_factory, "old@x.org", status="pending", created_hours_ago=30)

    await cleanup_api_keys(session_factory, settings, now)

    assert await count(session_factory, "api_accounts") == 1
    assert await count(session_factory, "api_keys") == 1
    assert await count(session_factory, "api_key_requests") == 0


async def test_ac24_the_cut_off_follows_the_now_argument(
    settings: Settings, session_factory: Factory, now: datetime
) -> None:
    """Time is injected: a day later the fresh rows are old."""
    from datetime import timedelta

    await insert_request(session_factory, "failed@x.org", status="failed", created_hours_ago=1)
    await cleanup_api_keys(session_factory, settings, now)
    assert await left(session_factory) == {"failed@x.org"}

    await cleanup_api_keys(session_factory, settings, now + timedelta(hours=30))

    assert await left(session_factory) == set()
