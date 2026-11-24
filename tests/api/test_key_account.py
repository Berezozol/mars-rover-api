"""The account of a key holder and deleting the data (stage 20B, AC-15 ... AC-19, AC-27).

`/api/v2/account*` identify the caller by the key (`X-Api-Key`); without a key the answer is
`401 api_key_required`. Deleting without a key goes through a mail link (`purpose = 'delete'`).
AC-19 (CORS) and AC-27 (admin `key-mail`) are here too.
"""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text

from tests._apikeys import (
    KEY_RE,
    SMALL_V2,
    account_with_key,
    create_account,
    create_key,
    new_key,
    rows,
)
from tests._keysignup import (
    ACCEPTED,
    ACCOUNT,
    ACCOUNT_KEYS,
    CONFIRM,
    DELETION_CONFIRM,
    DELETION_REQUESTS,
    ISO_UTC_RE,
    KEY_INFO_FIELDS,
    KEY_MAIL,
    REQUESTS,
    Signup,
    assert_error,
    count,
    fresh_ip,
    insert_request,
    last_mail_to,
    link_of,
    make_signup_app,
    recipients,
    request_rows,
    sha256,
    token_of,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
E2 = "known@example.org"
OTHER = "other@example.org"


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


# --- AC-15: GET /account ---


async def test_ac15_the_account_shows_email_keys_usage_and_limits(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == ["email", "status", "created_at", "keys", "usage", "limits"]
    assert body["email"] == E2
    assert body["status"] == "active"
    assert ISO_UTC_RE.match(body["created_at"])
    assert body["keys"] == [
        {
            "id": key.id,
            "prefix": key.prefix,
            "created_at": body["keys"][0]["created_at"],
            "last_used_at": None,
            "revoked_at": None,
        }
    ]
    assert list(body["keys"][0]) == KEY_INFO_FIELDS
    assert ISO_UTC_RE.match(body["keys"][0]["created_at"])
    assert body["usage"] == {"today": 0, "last_30_days": 0}
    assert body["limits"] == {"per_minute": 120, "per_hour": 10000, "concurrent": 8}


async def test_ac15_neither_a_key_value_nor_a_hash_is_in_the_answer(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert key.value not in response.text
    assert key.value[3:] not in response.text
    assert sha256(key.value).hex() not in response.text
    assert "key_hash" not in response.text


async def test_ac15_the_key_may_come_in_the_query_string(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client().get(ACCOUNT, params={"api_key": key.value})

    assert response.status_code == 200
    assert response.json()["email"] == E2


async def test_ac15_keys_are_active_ones_and_the_recently_revoked_newest_first(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    account = await create_account(se.factory, E2)
    old_revoked = await create_key(se.factory, account, revoked=True)
    recently_revoked = await create_key(se.factory, account, revoked=True)
    active = await create_key(se.factory, account)
    async with se.factory() as session:
        await session.execute(
            text("UPDATE api_keys SET revoked_at = now() - interval '40 days' WHERE id = :i"),
            {"i": old_revoked.id},
        )
        await session.execute(
            text("UPDATE api_keys SET revoked_at = now() - interval '5 days' WHERE id = :i"),
            {"i": recently_revoked.id},
        )
        await session.commit()

    response = await se.client(headers=hdr(active.value)).get(ACCOUNT)

    listed = response.json()["keys"]
    assert [k["id"] for k in listed] == [active.id, recently_revoked.id]
    assert listed[0]["revoked_at"] is None
    assert ISO_UTC_RE.match(listed[1]["revoked_at"])


async def test_ac15_usage_counts_today_and_the_last_30_days(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)
    async with se.factory() as session:
        for days_ago, requests in ((0, 7), (10, 100), (29, 3), (45, 1000)):
            await session.execute(
                text(
                    "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests) VALUES "
                    "(:a, :k, (now() AT TIME ZONE 'utc')::date - CAST(:d AS int), :r)"
                ),
                {"a": key.account_id, "k": key.id, "d": days_ago, "r": requests},
            )
        await session.commit()

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert response.json()["usage"] == {"today": 7, "last_30_days": 110}


async def test_ac15_two_keys_of_one_account_share_the_usage(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    account = await create_account(se.factory, E2)
    first = await create_key(se.factory, account)
    second = await create_key(se.factory, account)
    async with se.factory() as session:
        for key_id, requests in ((first.id, 4), (second.id, 6)):
            await session.execute(
                text(
                    "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests) VALUES "
                    "(:a, :k, (now() AT TIME ZONE 'utc')::date, :r)"
                ),
                {"a": account, "k": key_id, "r": requests},
            )
        await session.commit()

    response = await se.client(headers=hdr(first.value)).get(ACCOUNT)

    assert response.json()["usage"]["today"] == 10
    assert len(response.json()["keys"]) == 2


async def test_ac15_only_the_own_account_is_shown(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    mine = await account_with_key(se.factory, E2)
    await account_with_key(se.factory, OTHER)

    body = (await se.client(headers=hdr(mine.value)).get(ACCOUNT)).json()

    assert body["email"] == E2
    assert [k["id"] for k in body["keys"]] == [mine.id]


async def test_ac15_without_a_key_is_401_api_key_required(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().get(ACCOUNT)

    assert_error(response, 401, "api_key_required")


async def test_ac15_an_unknown_key_is_401_invalid_api_key(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client(headers=hdr(new_key())).get(ACCOUNT)

    assert_error(response, 401, "invalid_api_key")


async def test_ac15_a_revoked_key_is_401_api_key_revoked(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await create_key(se.factory, await create_account(se.factory, E2), revoked=True)

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert_error(response, 401, "api_key_revoked")


async def test_ac15_a_blocked_account_is_403_account_blocked(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await create_key(
        se.factory, await create_account(se.factory, E2, blocked=True, block_reason="abuse")
    )

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert_error(response, 403, "account_blocked")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("POST", ACCOUNT_KEYS, id="new-key"),
        pytest.param("DELETE", f"{ACCOUNT_KEYS}/1", id="revoke-key"),
        pytest.param("DELETE", ACCOUNT, id="delete-account"),
    ],
)
async def test_ac15_every_account_route_without_a_key_is_401_api_key_required(
    make_keys_app: MakeApp, method: str, path: str
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().request(method, path)

    assert_error(response, 401, "api_key_required")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("GET", ACCOUNT, id="account"),
        pytest.param("POST", ACCOUNT_KEYS, id="new-key"),
        pytest.param("DELETE", f"{ACCOUNT_KEYS}/1", id="revoke-key"),
        pytest.param("DELETE", ACCOUNT, id="delete-account"),
    ],
)
async def test_ac15_a_blocked_account_cannot_use_any_account_route(
    make_keys_app: MakeApp, method: str, path: str
) -> None:
    se = await make_signup_app(make_keys_app)
    key = await create_key(
        se.factory, await create_account(se.factory, E2, blocked=True, block_reason="abuse")
    )

    response = await se.client(headers=hdr(key.value)).request(method, path)

    assert_error(response, 403, "account_blocked")
    assert await count(se.factory, "api_accounts") == 1


async def test_ac15_the_account_routes_work_when_only_the_mail_signup_is_off(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_enabled=False, smtp_host=None)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).get(ACCOUNT)

    assert response.status_code == 200
    assert response.json()["email"] == E2


# --- AC-16: own keys ---


async def test_ac16_a_new_key_is_issued_for_the_account(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).post(ACCOUNT_KEYS)

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == ["key", "key_info"]
    assert KEY_RE.match(body["key"]) and body["key"] != key.value
    assert list(body["key_info"]) == KEY_INFO_FIELDS
    assert body["key_info"]["prefix"] == body["key"][:11]
    assert body["key_info"]["revoked_at"] is None
    works = await se.client(headers=hdr(body["key"])).get(SMALL_V2)
    assert works.status_code == 200 and works.headers["x-ratelimit-limit"] == "120"
    listed = (await se.client(headers=hdr(key.value)).get(ACCOUNT)).json()["keys"]
    assert len(listed) == 2


async def test_ac16_the_third_active_key_is_409_key_limit_reached(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)
    client = se.client(headers=hdr(key.value))
    assert (await client.post(ACCOUNT_KEYS)).status_code == 201

    response = await client.post(ACCOUNT_KEYS)

    assert_error(response, 409, "key_limit_reached")
    assert await count(se.factory, "api_keys") == 2


async def test_ac16_revoking_a_key_frees_a_slot(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)
    client = se.client(headers=hdr(key.value))
    second = (await client.post(ACCOUNT_KEYS)).json()
    assert (await client.delete(f"{ACCOUNT_KEYS}/{second['key_info']['id']}")).status_code == 204

    response = await client.post(ACCOUNT_KEYS)

    assert response.status_code == 201


async def test_ac16_a_key_is_revoked_by_its_owner_and_stops_working_at_once(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    account = await create_account(se.factory, E2)
    main = await create_key(se.factory, account)
    spare = await create_key(se.factory, account)
    assert (await se.client(headers=hdr(spare.value)).get(SMALL_V2)).status_code == 200

    response = await se.client(headers=hdr(main.value)).delete(f"{ACCOUNT_KEYS}/{spare.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    reasons = await rows(
        se.factory, "SELECT id, revoke_reason, revoked_at FROM api_keys ORDER BY id"
    )
    assert reasons[1]["revoke_reason"] == "owner" and reasons[1]["revoked_at"] is not None
    assert reasons[0]["revoked_at"] is None
    gone = await se.client(headers=hdr(spare.value)).get(SMALL_V2)
    assert_error(gone, 401, "api_key_revoked")
    assert (await se.client(headers=hdr(main.value)).get(SMALL_V2)).status_code == 200


async def test_ac16_the_key_of_the_request_itself_can_be_revoked(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).delete(f"{ACCOUNT_KEYS}/{key.id}")

    assert response.status_code == 204
    again = await se.client(headers=hdr(key.value)).get(ACCOUNT)
    assert_error(again, 401, "api_key_revoked")


async def test_ac16_a_foreign_key_id_is_404_and_the_key_stays_active(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    mine = await account_with_key(se.factory, E2)
    foreign = await account_with_key(se.factory, OTHER)

    response = await se.client(headers=hdr(mine.value)).delete(f"{ACCOUNT_KEYS}/{foreign.id}")

    assert_error(response, 404, "not_found")
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 2
    assert (await se.client(headers=hdr(foreign.value)).get(SMALL_V2)).status_code == 200


@pytest.mark.parametrize("key_id", [999_999, 2**63 - 1], ids=["missing", "max-id"])
async def test_ac16_a_missing_key_id_is_404_not_found(make_keys_app: MakeApp, key_id: int) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).delete(f"{ACCOUNT_KEYS}/{key_id}")

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize(
    "key_id",
    ["0", "-1", str(2**63), str(2**70), "abc", "1.5", "1e3"],
    ids=["zero", "negative", "2-63", "2-70", "letters", "float", "exponent"],
)
async def test_ac16_a_key_id_outside_1_to_2_63_minus_1_is_422(
    make_keys_app: MakeApp, key_id: str
) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).delete(f"{ACCOUNT_KEYS}/{key_id}")

    assert_error(response, 422, "validation_error")
    assert await count(se.factory, "api_keys", "revoked_at IS NULL") == 1


# --- AC-17: DELETE /account ---


async def full_account(se: Signup, email: str = E2) -> Any:
    """An account with a key, usage counters and a mail request of its address."""
    key = await account_with_key(se.factory, email)
    async with se.factory() as session:
        await session.execute(
            text(
                "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests) "
                "VALUES (:a, :k, (now() AT TIME ZONE 'utc')::date, 5)"
            ),
            {"a": key.account_id, "k": key.id},
        )
        await session.commit()
    await insert_request(se.factory, email, status="sent", expires_in_hours=0.5)
    return key


async def test_ac17_deleting_the_account_removes_every_trace_of_it(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await full_account(se)
    other = await full_account(se, OTHER)

    response = await se.client(headers=hdr(key.value)).delete(ACCOUNT)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert await count(se.factory, "api_accounts", "email = :e", e=E2) == 0
    assert await count(se.factory, "api_keys", "id = :i", i=key.id) == 0
    assert await count(se.factory, "api_key_usage_daily", "account_id = :a", a=key.account_id) == 0
    assert await count(se.factory, "api_key_requests", "email = :e", e=E2) == 0
    # the other account is untouched
    assert await count(se.factory, "api_accounts", "email = :e", e=OTHER) == 1
    assert await count(se.factory, "api_keys", "id = :i", i=other.id) == 1
    assert (
        await count(se.factory, "api_key_usage_daily", "account_id = :a", a=other.account_id) == 1
    )
    assert await count(se.factory, "api_key_requests", "email = :e", e=OTHER) == 1


async def test_ac17_the_key_of_a_deleted_account_is_401_invalid_api_key(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    key = await full_account(se)
    client = se.client(headers=hdr(key.value))
    assert (await client.get(SMALL_V2)).status_code == 200  # the key is cached now

    await client.delete(ACCOUNT)

    gone = await se.client(headers=hdr(key.value)).get(SMALL_V2)
    assert_error(gone, 401, "invalid_api_key")


async def test_ac17_every_key_of_the_account_goes_with_it(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    account = await create_account(se.factory, E2)
    first = await create_key(se.factory, account)
    second = await create_key(se.factory, account)
    third = await create_key(se.factory, account, revoked=True)

    await se.client(headers=hdr(first.value)).delete(ACCOUNT)

    assert await count(se.factory, "api_keys") == 0
    for key in (second, third):
        response = await se.client(headers=hdr(key.value)).get(SMALL_V2)
        assert_error(response, 401, "invalid_api_key")


async def test_ac17_a_deleted_address_can_sign_up_again(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)
    await se.client(headers=hdr(key.value)).delete(ACCOUNT)

    new_key_value = await se.issue(E2)

    assert KEY_RE.match(new_key_value) and new_key_value != key.value


# --- AC-18: deleting without a key ---


async def test_ac18_the_answer_is_the_same_for_a_known_and_an_unknown_address(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)

    known = await se.post(DELETION_REQUESTS, {"email": E2})
    unknown = await se.post(DELETION_REQUESTS, {"email": "nobody@example.org"})

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == ACCEPTED
    assert known.content == unknown.content
    drop = {"x-request-id"}
    assert {k: v for k, v in known.headers.items() if k not in drop} == {
        k: v for k, v in unknown.headers.items() if k not in drop
    }


async def test_ac18_only_a_known_address_gets_the_mail_with_the_delete_link(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    await se.post(DELETION_REQUESTS, {"email": "nobody@example.org"})
    await se.post(DELETION_REQUESTS, {"email": E2})

    await se.run()

    assert recipients(se.mailer) == [E2]
    message = se.mailer.sent[0]
    assert link_of(message) == f"https://solindex.example/keys/delete#token={token_of(message)}"
    rows_ = await request_rows(se.factory)
    assert [(r["email"], r["purpose"], r["status"]) for r in rows_] == [
        ("nobody@example.org", "delete", "skipped"),
        (E2, "delete", "sent"),
    ]


async def test_ac18_a_blocked_account_can_still_ask_to_delete_its_data(
    make_keys_app: MakeApp,
) -> None:
    """The spec writes nothing for an unknown address only; a block must not trap the data."""
    se = await make_signup_app(make_keys_app)
    await create_account(se.factory, E2, blocked=True, block_reason="abuse")

    await se.post(DELETION_REQUESTS, {"email": E2})
    await se.run()

    assert recipients(se.mailer) == [E2]


async def test_ac18_the_delete_token_removes_the_data(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await full_account(se)
    token = await se.token_for(E2, "delete")

    response = await se.post(DELETION_CONFIRM, {"token": token})

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert await count(se.factory, "api_accounts") == 0
    assert await count(se.factory, "api_keys") == 0
    assert await count(se.factory, "api_key_usage_daily") == 0
    assert await count(se.factory, "api_key_requests", "email = :e", e=E2) == 0
    gone = await se.client(headers=hdr(key.value)).get(SMALL_V2)
    assert_error(gone, 401, "invalid_api_key")


async def test_ac18_the_delete_token_works_once(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    token = await se.token_for(E2, "delete")
    assert (await se.post(DELETION_CONFIRM, {"token": token})).status_code == 204

    again = await se.post(DELETION_CONFIRM, {"token": token})

    assert_error(again, 400, "invalid_token")


async def test_ac18_an_expired_delete_token_is_invalid_token(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    token = await se.token_for(E2, "delete")
    async with se.factory() as session:
        await session.execute(
            text("UPDATE api_key_requests SET expires_at = now() - interval '1 second'")
        )
        await session.commit()

    response = await se.post(DELETION_CONFIRM, {"token": token})

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_accounts") == 1


async def test_ac18_a_random_token_is_invalid_token(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)

    response = await se.post(DELETION_CONFIRM, {"token": "B" * 43})

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_accounts") == 1


async def test_ac18_a_key_token_is_not_accepted_for_deletion(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    key_token = await se.token_for(E2, "key")

    response = await se.post(DELETION_CONFIRM, {"token": key_token})

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_accounts") == 1
    assert await count(se.factory, "api_keys") == 1


async def test_ac18_a_deletion_token_is_not_accepted_for_a_key(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    delete_token = await se.token_for(E2, "delete")

    response = await se.post(CONFIRM, {"token": delete_token})

    assert_error(response, 400, "invalid_token")
    assert await count(se.factory, "api_keys") == 1
    assert await count(se.factory, "api_accounts") == 1


async def test_ac18_two_simultaneous_deletions_both_end_cleanly(make_keys_app: MakeApp) -> None:
    import asyncio

    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    token = await se.token_for(E2, "delete")

    responses = await asyncio.gather(
        se.post(DELETION_CONFIRM, {"token": token}, fresh_ip()),
        se.post(DELETION_CONFIRM, {"token": token}, fresh_ip()),
    )

    assert sorted(r.status_code for r in responses) == [204, 400]
    assert await count(se.factory, "api_accounts") == 0


async def test_ac18_a_deletion_request_for_an_address_with_a_pending_key_mail_works(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await account_with_key(se.factory, E2)
    await se.post(REQUESTS, {"email": E2})
    await se.post(DELETION_REQUESTS, {"email": E2})

    await se.run()

    assert len(se.mailer.sent) == 2
    assert {r["purpose"] for r in await request_rows(se.factory)} == {"key", "delete"}
    assert link_of(last_mail_to(se.mailer, E2))


# --- AC-19: no CORS for the routes of the stage ---

CORS_ROUTES = [
    pytest.param("POST", REQUESTS, {"email": "a@example.org"}, id="keys-requests"),
    pytest.param("POST", CONFIRM, {"token": "A" * 43}, id="keys-confirm"),
    pytest.param("POST", DELETION_REQUESTS, {"email": "a@example.org"}, id="deletion-requests"),
    pytest.param("POST", DELETION_CONFIRM, {"token": "A" * 43}, id="deletion-confirm"),
    pytest.param("POST", ACCOUNT_KEYS, None, id="account-keys-post"),
    pytest.param("DELETE", f"{ACCOUNT_KEYS}/1", None, id="account-keys-delete"),
    pytest.param("DELETE", ACCOUNT, None, id="account-delete"),
]


@pytest.mark.parametrize(("method", "path", "body"), CORS_ROUTES)
async def test_ac19_a_cross_site_request_gets_no_allow_origin_header(
    make_keys_app: MakeApp, method: str, path: str, body: Any
) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).request(
        method, path, json=body, headers={"Origin": "https://evil.example"}
    )

    assert "access-control-allow-origin" not in response.headers, dict(response.headers)
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    ("path", "method"),
    [
        pytest.param(REQUESTS, "POST", id="keys-requests"),
        pytest.param(CONFIRM, "POST", id="keys-confirm"),
        pytest.param(DELETION_REQUESTS, "POST", id="deletion-requests"),
        pytest.param(DELETION_CONFIRM, "POST", id="deletion-confirm"),
        pytest.param(ACCOUNT_KEYS, "POST", id="account-keys"),
        pytest.param(ACCOUNT, "DELETE", id="account-delete"),
    ],
)
async def test_ac19_a_preflight_does_not_allow_the_method(
    make_keys_app: MakeApp, path: str, method: str
) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().options(
        path,
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    allowed = response.headers.get("access-control-allow-methods", "")
    assert method not in [m.strip() for m in allowed.split(",")], dict(response.headers)


async def test_ac19_a_simple_get_of_the_account_is_not_open_to_other_sites_either(
    make_keys_app: MakeApp,
) -> None:
    """The account holds an e-mail: unlike the photo routes it must not be readable cross-site."""
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).get(
        ACCOUNT, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers, dict(response.headers)


# --- AC-14: the switches ---

MAIL_ROUTES = [
    pytest.param("POST", REQUESTS, {"email": "a@example.org"}, id="keys-requests"),
    pytest.param("POST", CONFIRM, {"token": "A" * 43}, id="keys-confirm"),
    pytest.param("POST", DELETION_REQUESTS, {"email": "a@example.org"}, id="deletion-requests"),
    pytest.param("POST", DELETION_CONFIRM, {"token": "A" * 43}, id="deletion-confirm"),
]


@pytest.mark.parametrize(("method", "path", "body"), MAIL_ROUTES)
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"key_signup_enabled": False}, id="signup-off"),
        pytest.param({"key_signup_enabled": False, "smtp_host": None}, id="signup-off-no-smtp"),
        pytest.param({"api_keys_enabled": False}, id="keys-off"),
    ],
)
async def test_ac14_mail_routes_do_not_exist_when_signup_is_unavailable(
    make_keys_app: MakeApp, method: str, path: str, body: Any, overrides: dict[str, Any]
) -> None:
    se = await make_signup_app(make_keys_app, **overrides)

    response = await se.client().request(method, path, json=body)

    assert response.status_code == 404, response.text
    assert await count(se.factory, "api_key_requests") == 0


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("GET", ACCOUNT, id="account"),
        pytest.param("POST", ACCOUNT_KEYS, id="new-key"),
        pytest.param("DELETE", f"{ACCOUNT_KEYS}/1", id="revoke-key"),
        pytest.param("DELETE", ACCOUNT, id="delete-account"),
    ],
)
async def test_ac14_account_routes_do_not_exist_when_keys_are_off(
    make_keys_app: MakeApp, method: str, path: str
) -> None:
    se = await make_signup_app(make_keys_app, api_keys_enabled=False)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).request(method, path)

    assert response.status_code == 404


async def test_ac14_the_admin_key_mail_route_does_not_exist_when_keys_are_off(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, api_keys_enabled=False)

    response = await se.env.admin().get(KEY_MAIL)

    assert response.status_code == 404


async def test_ac14_routes_exist_when_everything_is_on(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.post(REQUESTS, {"email": "a@example.org"})

    assert response.status_code == 202


# --- AC-27: admin key-mail ---

KEY_MAIL_FIELDS = [
    "pending",
    "sent_24h",
    "failed_24h",
    "global_limit",
    "last_success_at",
    "last_error_at",
]


async def test_ac27_an_empty_queue_has_zero_counters_and_no_times(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=100)

    response = await se.env.admin().get(KEY_MAIL)

    assert response.status_code == 200, response.text
    assert sorted(response.json()) == sorted(KEY_MAIL_FIELDS)
    assert response.json() == {
        "pending": 0,
        "sent_24h": 0,
        "failed_24h": 0,
        "global_limit": 100,
        "last_success_at": None,
        "last_error_at": None,
    }
    assert response.headers["cache-control"] == "no-store"


async def test_ac27_counters_follow_the_queue(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app, key_signup_global_per_day=42)
    await insert_request(se.factory, "p1@example.org", status="pending")
    await insert_request(se.factory, "p2@example.org", status="pending")
    await insert_request(se.factory, "s1@example.org", status="sent", expires_in_hours=0.5)
    await insert_request(se.factory, "s2@example.org", status="sent", created_hours_ago=2)
    await insert_request(
        se.factory, "s3@example.org", status="sent", created_hours_ago=30, expires_in_hours=-20
    )
    await insert_request(se.factory, "f1@example.org", status="failed", created_hours_ago=1)
    await insert_request(se.factory, "f2@example.org", status="failed", created_hours_ago=40)

    body = (await se.env.admin().get(KEY_MAIL)).json()

    assert body["global_limit"] == 42
    assert body["sent_24h"] == 2
    assert body["failed_24h"] == 1
    assert body["pending"] == 2


async def test_ac27_last_success_and_last_error_are_filled_by_the_sender(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key("a@example.org")
    await se.run()
    se.mailer.fail_next = 1
    await se.request_key("b@example.org")
    await se.run()

    body = (await se.env.admin().get(KEY_MAIL)).json()

    assert ISO_UTC_RE.match(body["last_success_at"]), body
    assert ISO_UTC_RE.match(body["last_error_at"]), body
    assert body["sent_24h"] == 1
    assert body["pending"] == 1


async def test_ac27_the_answer_has_no_addresses(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    await se.request_key("secret.person@example.org")
    await se.run()
    await se.request_key("other.person@example.org")

    response = await se.env.admin().get(KEY_MAIL)

    assert "@" not in response.text
    assert "secret.person" not in response.text
    assert "example.org" not in response.text


async def test_ac27_without_the_admin_token_is_401(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client().get(KEY_MAIL)

    assert response.status_code == 401


async def test_ac27_a_wrong_admin_token_is_401(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)

    response = await se.client(headers={"X-Admin-Token": "wrong-token-0123456789"}).get(KEY_MAIL)

    assert response.status_code == 401


async def test_ac27_an_account_key_is_not_an_admin_token(make_keys_app: MakeApp) -> None:
    se = await make_signup_app(make_keys_app)
    key = await account_with_key(se.factory, E2)

    response = await se.client(headers=hdr(key.value)).get(KEY_MAIL)

    assert response.status_code == 401
