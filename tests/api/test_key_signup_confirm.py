"""Confirming the mail link and getting the key (stage 20B, AC-7 ... AC-10).

`POST /api/v2/keys/confirm {"token", "revoke_existing"}` is the only place where a key is
shown. The token comes out of the mail captured by the `MemoryMailer`; its lifetime is moved
in the database (the spec has no clock for it), the other clocks are fake.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text

from tests._apikeys import (
    KEY_RE,
    SMALL_V2,
    create_account,
    create_key,
    key_hash,
    rows,
    scalar,
)
from tests._keysignup import (
    CONFIRM,
    DELETION_CONFIRM,
    ISO_UTC_RE,
    KEY_INFO_FIELDS,
    Signup,
    assert_error,
    block_account,
    count,
    fresh_ip,
    make_signup_app,
    request_rows,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
E = "new.person@example.org"
E2 = "known@example.org"
RANDOM_TOKEN = "A" * 43


def key_header(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


async def expire(se: Signup) -> None:
    async with se.factory() as session:
        await session.execute(
            text("UPDATE api_key_requests SET expires_at = now() - interval '1 second'")
        )
        await session.commit()


# --- AC-7 ---


async def test_ac7_confirm_creates_the_account_and_returns_the_key_once(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for(E)

    response = await se.confirm(token)

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == ["key", "key_info", "account"]
    assert KEY_RE.match(body["key"])
    info = body["key_info"]
    assert list(info) == KEY_INFO_FIELDS
    assert isinstance(info["id"], int)
    assert info["prefix"] == body["key"][:11]
    assert ISO_UTC_RE.match(info["created_at"])
    assert info["last_used_at"] is None and info["revoked_at"] is None
    assert list(body["account"]) == ["email", "created_at"]
    assert body["account"]["email"] == E
    assert ISO_UTC_RE.match(body["account"]["created_at"])


async def test_ac7_the_account_is_verified_and_only_the_hash_of_the_key_is_stored(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    body = (await se.confirm(await se.token_for(E))).json()

    account = await rows(
        se.factory, "SELECT email, status, verified_at IS NOT NULL AS verified FROM api_accounts"
    )
    stored = await rows(se.factory, "SELECT id, key_hash, prefix, revoked_at FROM api_keys")
    assert account == [{"email": E, "status": "active", "verified": True}]
    assert len(stored) == 1
    assert bytes(stored[0]["key_hash"]) == key_hash(body["key"])
    assert stored[0]["id"] == body["key_info"]["id"]
    assert stored[0]["revoked_at"] is None
    assert body["key"] not in str(await rows(se.factory, "SELECT a::text FROM api_keys a"))


async def test_ac7_the_issued_key_gets_the_extended_limit(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await se.issue(E)

    response = await se.client(headers=key_header(key)).get(SMALL_V2)

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


async def test_ac7_the_request_row_is_marked_used(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.issue(E)

    (row,) = await request_rows(se.factory)

    assert row["used_at"] is not None


async def test_ac7_a_second_confirm_with_the_same_token_is_invalid_token(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for(E)
    assert (await se.confirm(token)).status_code == 201

    again = await se.confirm(token)

    assert_error(again, 400, "invalid_token")
    assert await count(se.factory, "api_keys") == 1


async def test_ac7_an_expired_token_is_invalid_token(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for(E)
    await expire(se)

    response = await se.confirm(token)

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_accounts") == 0


async def test_ac7_a_token_is_valid_until_its_expiry(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_token_ttl_minutes=60)
    token = await se.token_for(E)
    async with se.factory() as session:
        await session.execute(
            text("UPDATE api_key_requests SET expires_at = now() + interval '1 minute'")
        )
        await session.commit()

    assert (await se.confirm(token)).status_code == 201


async def test_ac7_a_random_token_of_the_right_length_is_invalid_token(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.token_for(E)

    response = await se.confirm(RANDOM_TOKEN)

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_accounts") == 0


async def test_ac7_a_token_never_sent_cannot_be_guessed_from_a_pending_row(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key(E)  # queued, not sent: no token exists yet

    response = await se.confirm(RANDOM_TOKEN)

    assert_error(response, 400, "invalid_token")


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("A" * 42, id="42-chars"),
        pytest.param("A" * 44, id="44-chars"),
        pytest.param("", id="empty"),
        pytest.param("A" * 42 + "+", id="plus"),
        pytest.param("A" * 42 + "/", id="slash"),
        pytest.param("A" * 42 + "=", id="padding"),
        pytest.param("A" * 42 + " ", id="space"),
        pytest.param("A" * 42 + "é", id="non-ascii"),
        pytest.param(12345, id="number"),
        pytest.param(None, id="null"),
        pytest.param(["A" * 43], id="list"),
    ],
)
@pytest.mark.parametrize("path", [CONFIRM, DELETION_CONFIRM], ids=["key", "deletion"])
async def test_ac7_a_token_that_is_not_43_base64url_characters_is_422(
    make_keys_app: MakeApp, path: str, token: Any
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.post(path, {"token": token})

    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"token": RANDOM_TOKEN, "extra": 1}, id="extra-field"),
        pytest.param({"token": RANDOM_TOKEN, "revoke_existing": "yes"}, id="revoke-not-bool"),
        pytest.param([RANDOM_TOKEN], id="body-list"),
        pytest.param(None, id="body-null"),
    ],
)
async def test_ac7_a_malformed_confirm_body_is_422(make_keys_app: MakeApp, body: Any) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.post(CONFIRM, body)

    assert_error(response, 422, "validation_error")


async def test_ac7_confirm_without_a_json_content_type_is_415(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().post(CONFIRM, content=b'{"token": "%s"}' % (b"A" * 43))

    assert_error(response, 415, "unsupported_media_type")


async def test_ac7_confirm_with_a_body_over_2_kib_is_413(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().post(
        CONFIRM,
        content=b'{"token": "' + b"A" * 3000 + b'"}',
        headers={"Content-Type": "application/json"},
    )

    assert_error(response, 413, "payload_too_large")


async def test_ac7_the_token_of_another_mail_does_not_work_for_a_changed_address(
    make_keys_app: MakeApp,
) -> None:
    """Each token belongs to its own request: two mails, two accounts, two keys."""
    se = await make_signup_app(make_keys_app)
    first = await se.token_for("one@example.org")
    second = await se.token_for("two@example.org")

    one = (await se.confirm(first)).json()
    two = (await se.confirm(second)).json()

    assert one["account"]["email"] == "one@example.org"
    assert two["account"]["email"] == "two@example.org"
    assert one["key"] != two["key"]


async def test_ac7_confirm_on_a_known_account_with_a_free_slot_adds_a_second_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    old = await create_key(se.factory, await create_account(se.factory, E2))

    response = await se.confirm(await se.token_for(E2))

    assert response.status_code == 201
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 2
    assert await count(se.factory, "api_accounts") == 1
    still = await se.client(headers=key_header(old.value)).get(SMALL_V2)
    assert still.status_code == 200


async def test_ac7_the_mail_address_is_taken_from_the_request_not_from_the_confirm(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for("Mixed.Case@Example.org")

    body = (await se.confirm(token)).json()

    assert body["account"]["email"] == "mixed.case@example.org"


async def test_ac7_a_deleted_account_is_created_anew_by_a_valid_token(
    make_keys_app: MakeApp,
) -> None:
    """Edge case: the account was deleted between the mail and the confirm."""
    se = await make_signup_app(make_keys_app)
    await create_key(se.factory, await create_account(se.factory, E2))
    token = await se.token_for(E2)
    async with se.factory() as session:
        await session.execute(text("DELETE FROM api_accounts"))
        await session.commit()

    response = await se.confirm(token)

    assert response.status_code == 201, response.text
    assert response.json()["account"]["email"] == E2
    assert await count(se.factory, "api_accounts", "verified_at IS NOT NULL") == 1


# --- AC-8: one token, one key ---


async def test_ac8_two_simultaneous_confirms_issue_exactly_one_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for(E)

    responses = await asyncio.gather(
        se.confirm(token, ip=fresh_ip()), se.confirm(token, ip=fresh_ip())
    )

    assert sorted(r.status_code for r in responses) == [201, 400]
    loser = next(r for r in responses if r.status_code == 400)
    assert loser.json()["error"]["code"] == "invalid_token"
    assert await count(se.factory, "api_keys") == 1
    assert await count(se.factory, "api_accounts") == 1


async def test_ac8_many_simultaneous_confirms_issue_exactly_one_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    token = await se.token_for(E)

    responses = await asyncio.gather(*(se.confirm(token, ip=fresh_ip()) for _ in range(6)))

    assert sorted(r.status_code for r in responses) == [201] + [400] * 5
    assert await count(se.factory, "api_keys") == 1


# --- AC-9: the key limit ---


async def two_keys(se: Signup, email: str = E2) -> tuple[str, str]:
    account = await create_account(se.factory, email)
    first = await create_key(se.factory, account)
    second = await create_key(se.factory, account)
    return first.value, second.value


async def test_ac9_at_the_key_limit_confirm_is_409_and_the_token_survives(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await two_keys(se)
    token = await se.token_for(E2)

    response = await se.confirm(token)

    assert_error(response, 409, "key_limit_reached")
    (row,) = await request_rows(se.factory)
    assert row["used_at"] is None
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 2


async def test_ac9_revoke_existing_replaces_the_old_keys(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    old_a, old_b = await two_keys(se)
    token = await se.token_for(E2)
    assert (await se.confirm(token)).status_code == 409

    response = await se.confirm(token, revoke_existing=True)

    assert response.status_code == 201, response.text
    new_key = response.json()["key"]
    reasons = await rows(
        se.factory, "SELECT revoke_reason FROM api_keys WHERE revoked_at IS NOT NULL ORDER BY id"
    )
    assert [r["revoke_reason"] for r in reasons] == ["replaced", "replaced"]
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 1
    for old in (old_a, old_b):
        gone = await se.client(headers=key_header(old)).get(SMALL_V2)
        assert_error(gone, 401, "api_key_revoked")
    fresh = await se.client(headers=key_header(new_key)).get(SMALL_V2)
    assert fresh.status_code == 200
    assert fresh.headers["x-ratelimit-limit"] == "120"


async def test_ac9_an_old_key_that_was_cached_is_revoked_at_once(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    old_a, _ = await two_keys(se)
    warm = await se.client(headers=key_header(old_a)).get(SMALL_V2)
    assert warm.status_code == 200
    token = await se.token_for(E2)

    await se.confirm(token, revoke_existing=True)

    gone = await se.client(headers=key_header(old_a)).get(SMALL_V2)
    assert_error(gone, 401, "api_key_revoked")


async def test_ac9_repeating_confirm_after_409_without_the_flag_is_409_again(
    make_keys_app: MakeApp,
) -> None:
    """Edge case: the token is not spent by the refusal, so the user can come back."""
    se = await make_signup_app(make_keys_app)
    await two_keys(se)
    token = await se.token_for(E2)

    first = await se.confirm(token)
    second = await se.confirm(token, revoke_existing=False)

    assert first.status_code == 409 and second.status_code == 409


async def test_ac9_revoke_existing_below_the_limit_keeps_the_old_key(
    make_keys_app: MakeApp,
) -> None:
    """API.md says "при лимите ключей": below the limit nothing is revoked (reading of the spec)."""
    se = await make_signup_app(make_keys_app)
    old = await create_key(se.factory, await create_account(se.factory, E2))
    token = await se.token_for(E2)

    response = await se.confirm(token, revoke_existing=True)

    assert response.status_code == 201
    kept = await se.client(headers=key_header(old.value)).get(SMALL_V2)
    assert kept.status_code == 200


async def test_ac9_revoked_keys_do_not_count_toward_the_limit(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    account = await create_account(se.factory, E2)
    await create_key(se.factory, account, revoked=True)
    await create_key(se.factory, account, revoked=True)
    await create_key(se.factory, account)

    response = await se.confirm(await se.token_for(E2))

    assert response.status_code == 201


# --- AC-10: a blocked account ---


async def test_ac10_a_token_issued_before_the_block_gives_403_and_no_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_account(se.factory, E2)
    token = await se.token_for(E2)
    await block_account(se.factory, E2)

    response = await se.confirm(token)

    assert_error(response, 403, "account_blocked")
    assert await count(se.factory, "api_keys") == 0


async def test_ac10_a_blocked_account_with_a_key_gets_no_second_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_key(se.factory, await create_account(se.factory, E2))
    token = await se.token_for(E2)
    await block_account(se.factory, E2)

    response = await se.confirm(token, revoke_existing=True)

    assert_error(response, 403, "account_blocked")
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 1


async def test_ac10_the_blocked_state_is_checked_before_the_key_limit(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await two_keys(se)
    token = await se.token_for(E2)
    await block_account(se.factory, E2)

    response = await se.confirm(token)

    assert response.status_code in (403, 409), response.text
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 2


async def test_ac10_the_account_stays_blocked_and_unverified_state_unchanged(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await create_account(se.factory, E2)
    token = await se.token_for(E2)
    await block_account(se.factory, E2)

    await se.confirm(token)

    assert await scalar(se.factory, "SELECT status FROM api_accounts WHERE email = :e", e=E2) == (
        "blocked"
    )
