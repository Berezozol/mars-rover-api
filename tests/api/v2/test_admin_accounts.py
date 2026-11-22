"""Admin endpoints for accounts and keys (stage 20A, AC-29, AC-30, AC-31; docs/API.md "Admin",
"Учётные записи и ключи (этап 20)").

- `GET /admin/accounts?q=&status=&page=&per_page=`: search by part of the e-mail (any case) or
  by key prefix (`si_...`), newest first, `{"items", "page", "per_page", "total"}`.
- `GET /admin/accounts/{id}`: the item + `keys` + `usage_daily` for 30 days, no hashes, no log of
  single requests.
- `POST /admin/accounts {"email", "note"}`: 201 `{"key", "key_info", "account"}`; a known e-mail
  gets a new key of the existing account; `409 key_limit_reached` at 2 active keys.
- block / unblock / key revoke / account delete; all without `X-Admin-Token` -> 401 (checked
  before the body and before the existence of the id).
"""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests._apikeys import (
    ADMIN_TOKEN,
    KEY_RE,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    key_hash,
    rows,
    scalar,
)
from tests.api.v2.conftest import assert_error, assert_validation_error

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
ACCOUNTS = "/api/v2/admin/accounts"
ITEM_KEYS = [
    "id",
    "email",
    "status",
    "created_at",
    "verified_at",
    "blocked_at",
    "block_reason",
    "active_keys",
    "requests_30d",
]
KEY_INFO_KEYS = ["id", "prefix", "created_at", "last_used_at", "revoked_at"]
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
T0 = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


async def add_usage(env: KeysApp, key_id: int, account_id: int, day: date, **counts: int) -> None:
    async with env.factory() as session:
        await session.execute(
            text(
                "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests, rejected) "
                "VALUES (:a, :k, :d, :requests, :rejected)"
            ),
            {"a": account_id, "k": key_id, "d": day, "requests": 0, "rejected": 0, **counts},
        )
        await session.commit()


def assert_item(item: dict[str, Any]) -> None:
    assert list(item)[: len(ITEM_KEYS)] == ITEM_KEYS, list(item)
    assert isinstance(item["id"], int)
    assert ISO_Z.match(item["created_at"]), item["created_at"]


# --- AC-29: issue and search ---


async def test_ac29_issue_key_by_hand_returns_201_with_the_key_once(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post(ACCOUNTS, json={"email": "new@example.org", "note": "friend"})

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == ["key", "key_info", "account"]
    assert KEY_RE.fullmatch(body["key"])
    assert list(body["key_info"]) == KEY_INFO_KEYS
    assert body["key_info"]["prefix"] == body["key"][:11]
    assert body["key_info"]["last_used_at"] is None
    assert body["key_info"]["revoked_at"] is None
    assert ISO_Z.match(body["key_info"]["created_at"])
    assert list(body["account"]) == ["email", "created_at"]
    assert body["account"]["email"] == "new@example.org"
    stored = await rows(env.factory, "SELECT email, status, verified_at, note FROM api_accounts")
    assert len(stored) == 1
    assert (stored[0]["email"], stored[0]["status"], stored[0]["note"]) == (
        "new@example.org",
        "active",
        "friend",
    )
    assert stored[0]["verified_at"] is not None  # verified at once: no e-mail round trip


async def test_ac29_the_key_is_shown_only_in_the_issue_response(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        issued = (await admin.post(ACCOUNTS, json={"email": "new@example.org"})).json()
        listing = await admin.get(ACCOUNTS)
        detail = await admin.get(f"{ACCOUNTS}/{listing.json()['items'][0]['id']}")

    for response in (listing, detail):
        assert issued["key"] not in response.text
        assert key_hash(issued["key"]).hex() not in response.text
    assert detail.json()["keys"][0]["prefix"] == issued["key"][:11]


async def test_ac29_issue_works_without_a_note(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post(ACCOUNTS, json={"email": "new@example.org"})

    assert response.status_code == 201, response.text


async def test_ac29_issued_key_is_usable_at_once(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        key = (await admin.post(ACCOUNTS, json={"email": "new@example.org"})).json()["key"]
    async with env.client(headers={"X-Api-Key": key}) as client:
        response = await client.get("/api/v2/rovers")

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


async def test_ac29_a_known_email_gets_a_second_key_of_the_same_account(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        first = await admin.post(ACCOUNTS, json={"email": "same@example.org"})
        second = await admin.post(ACCOUNTS, json={"email": "same@example.org"})

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["key"] != second.json()["key"]
    assert await scalar(env.factory, "SELECT count(*) FROM api_accounts") == 1
    assert await scalar(env.factory, "SELECT count(*) FROM api_keys") == 2


async def test_ac29_a_third_active_key_is_409_key_limit_reached(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        for _ in range(2):
            assert (
                await admin.post(ACCOUNTS, json={"email": "same@example.org"})
            ).status_code == 201
        third = await admin.post(ACCOUNTS, json={"email": "same@example.org"})

    assert_error(third, 409, "key_limit_reached")
    assert await scalar(env.factory, "SELECT count(*) FROM api_keys") == 2


async def test_ac29_a_revoked_key_does_not_count_towards_the_limit(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "same@example.org")
    await create_key(env.factory, account_id, revoked=True)
    await create_key(env.factory, account_id)
    async with env.admin() as admin:
        response = await admin.post(ACCOUNTS, json={"email": "same@example.org"})

    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-email"),
        pytest.param({"email": "no-at-sign"}, id="no-at"),
        pytest.param({"email": "a@b@example.org"}, id="two-ats"),
        pytest.param({"email": "a b@example.org"}, id="space"),
        pytest.param({"email": "a@nodot"}, id="domain-without-dot"),
        pytest.param({"email": "a" * 250 + "@x.org"}, id="over-254"),
        pytest.param({"email": 5}, id="not-a-string"),
        pytest.param({"email": "a@example.org", "note": "n" * 501}, id="note-over-500"),
    ],
)
async def test_ac29_issue_rejects_a_bad_body_with_422(make_keys_app: MakeApp, body: Any) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post(ACCOUNTS, json=body)

    assert_error(response, 422, "validation_error")
    assert await scalar(env.factory, "SELECT count(*) FROM api_accounts") == 0


async def seed_three(env: KeysApp) -> dict[str, int]:
    ids = {}
    for index, email in enumerate(("alice@example.org", "bob@sample.net", "carol@example.org")):
        ids[email] = await create_account(env.factory, email, created_at=T0 + timedelta(days=index))
    return ids


def emails(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["email"] for item in response.json()["items"]]


async def test_ac29_list_is_paginated_newest_first(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    await seed_three(env)
    async with env.admin() as admin:
        response = await admin.get(ACCOUNTS)
        page_two = await admin.get(ACCOUNTS, params={"per_page": 2, "page": 2})

    body = response.json()
    assert list(body) == ["items", "page", "per_page", "total"]
    assert (body["page"], body["per_page"], body["total"]) == (1, 25, 3)
    assert emails(response) == ["carol@example.org", "bob@sample.net", "alice@example.org"]
    for item in body["items"]:
        assert_item(item)
    assert response.headers["cache-control"] == "no-store"
    assert emails(page_two) == ["alice@example.org"]
    assert page_two.json()["total"] == 3
    assert page_two.json()["per_page"] == 2


async def test_ac29_search_by_part_of_the_email(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    await seed_three(env)
    async with env.admin() as admin:
        by_domain = await admin.get(ACCOUNTS, params={"q": "exam"})
        upper = await admin.get(ACCOUNTS, params={"q": "EXAM"})
        by_name = await admin.get(ACCOUNTS, params={"q": "bob"})
        nothing = await admin.get(ACCOUNTS, params={"q": "nobody"})

    assert emails(by_domain) == ["carol@example.org", "alice@example.org"]
    assert by_domain.json()["total"] == 2
    assert emails(upper) == emails(by_domain)
    assert emails(by_name) == ["bob@sample.net"]
    assert nothing.json() == {"items": [], "page": 1, "per_page": 25, "total": 0}


async def test_ac29_search_by_key_prefix(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    ids = await seed_three(env)
    await create_key(env.factory, ids["bob@sample.net"], key="si_AbCd1234" + "x" * 24)
    await create_key(env.factory, ids["alice@example.org"], key="si_ZzZz9999" + "y" * 24)
    async with env.admin() as admin:
        found = await admin.get(ACCOUNTS, params={"q": "si_AbCd1234"})
        none = await admin.get(ACCOUNTS, params={"q": "si_Nope0000"})

    assert emails(found) == ["bob@sample.net"]
    assert emails(none) == []


async def test_ac29_search_by_prefix_also_finds_a_revoked_key(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    ids = await seed_three(env)
    await create_key(
        env.factory, ids["bob@sample.net"], key="si_Revoked12" + "x" * 23, revoked=True
    )
    async with env.admin() as admin:
        found = await admin.get(ACCOUNTS, params={"q": "si_Revoked1"})

    assert emails(found) == ["bob@sample.net"]


async def test_ac29_status_filter(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    await create_account(env.factory, "ok@example.org", created_at=T0)
    await create_account(
        env.factory, "bad@example.org", blocked=True, block_reason="abuse", created_at=T0
    )
    async with env.admin() as admin:
        blocked = await admin.get(ACCOUNTS, params={"status": "blocked"})
        active = await admin.get(ACCOUNTS, params={"status": "active"})
        wrong = await admin.get(ACCOUNTS, params={"status": "frozen"})

    assert emails(blocked) == ["bad@example.org"]
    assert emails(active) == ["ok@example.org"]
    assert blocked.json()["items"][0]["status"] == "blocked"
    assert blocked.json()["items"][0]["block_reason"] == "abuse"
    assert blocked.json()["items"][0]["blocked_at"] is not None
    assert_validation_error(wrong, "status")


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"per_page": 101}, "per_page", id="per-page-101"),
        pytest.param({"per_page": 0}, "per_page", id="per-page-0"),
        pytest.param({"page": 0}, "page", id="page-0"),
        pytest.param({"q": "x" * 255}, "q", id="q-over-254"),
    ],
)
async def test_ac29_list_validates_parameters(
    make_keys_app: MakeApp, params: dict[str, Any], field: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.get(ACCOUNTS, params=params)

    assert_validation_error(response, field)


async def test_ac29_item_counts_active_keys_and_30_day_requests(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "busy@example.org")
    live = await create_key(env.factory, account_id)
    dead = await create_key(env.factory, account_id, revoked=True)
    today = datetime.now(UTC).date()
    await add_usage(env, live.id, account_id, today, requests=10)
    await add_usage(env, live.id, account_id, today - timedelta(days=5), requests=5)
    await add_usage(env, dead.id, account_id, today - timedelta(days=2), requests=1)
    await add_usage(env, dead.id, account_id, today - timedelta(days=45), requests=1000)
    async with env.admin() as admin:
        response = await admin.get(ACCOUNTS)

    [item] = response.json()["items"]
    assert item["active_keys"] == 1
    assert item["requests_30d"] == 16


# --- AC-30: block, unblock, revoke, delete ---


async def test_ac30_block_returns_the_item_with_status_blocked(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.admin() as admin:
        response = await admin.post(
            f"{ACCOUNTS}/{key.account_id}/block", json={"reason": "scraping"}
        )

    assert response.status_code == 200, response.text
    item = response.json()
    assert_item(item)
    assert item["id"] == key.account_id
    assert item["status"] == "blocked"
    assert item["block_reason"] == "scraping"
    assert ISO_Z.match(item["blocked_at"])
    assert response.headers["cache-control"] == "no-store"
    stored = await rows(env.factory, "SELECT status, block_reason, blocked_at FROM api_accounts")
    assert (stored[0]["status"], stored[0]["block_reason"]) == ("blocked", "scraping")
    assert stored[0]["blocked_at"] is not None


async def test_ac30_unblock_returns_the_item_with_status_active(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "x@example.org", blocked=True, block_reason="r")
    async with env.admin() as admin:
        response = await admin.post(f"{ACCOUNTS}/{account_id}/unblock")

    assert response.status_code == 200, response.text
    item = response.json()
    assert item["status"] == "active"
    assert item["blocked_at"] is None
    assert await scalar(env.factory, "SELECT status FROM api_accounts") == "active"


async def test_ac30_block_reason_of_501_characters_is_422(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory)
    async with env.admin() as admin:
        too_long = await admin.post(f"{ACCOUNTS}/{account_id}/block", json={"reason": "r" * 501})
        longest = await admin.post(f"{ACCOUNTS}/{account_id}/block", json={"reason": "r" * 500})

    assert_error(too_long, 422, "validation_error")
    assert longest.status_code == 200


async def test_ac30_revoke_key_returns_the_key_and_marks_the_reason_admin(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.admin() as admin:
        response = await admin.post(f"/api/v2/admin/keys/{key.id}/revoke")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == key.id
    assert body["prefix"] == key.prefix
    assert ISO_Z.match(body["revoked_at"])
    assert "key_hash" not in body
    stored = await rows(env.factory, "SELECT revoked_at, revoke_reason FROM api_keys")
    assert stored[0]["revoked_at"] is not None
    assert stored[0]["revoke_reason"] == "admin"


async def test_ac30_delete_account_returns_204_and_removes_everything_of_it(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory, "gone@example.org")
    keep = await account_with_key(env.factory, "stay@example.org")
    await add_usage(env, key.id, key.account_id, date(2026, 12, 1), requests=3)
    await add_usage(env, keep.id, keep.account_id, date(2026, 12, 1), requests=4)
    async with env.admin() as admin:
        response = await admin.delete(f"{ACCOUNTS}/{key.account_id}")
        again = await admin.get(f"{ACCOUNTS}/{key.account_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert_error(again, 404, "not_found")
    assert await scalar(env.factory, "SELECT email FROM api_accounts") == "stay@example.org"
    assert await scalar(env.factory, "SELECT count(*) FROM api_keys") == 1
    assert await scalar(env.factory, "SELECT count(*) FROM api_key_usage_daily") == 1


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("GET", f"{ACCOUNTS}/{{id}}", {}, id="detail"),
        pytest.param("POST", f"{ACCOUNTS}/{{id}}/block", {"json": {"reason": "x"}}, id="block"),
        pytest.param("POST", f"{ACCOUNTS}/{{id}}/unblock", {}, id="unblock"),
        pytest.param("DELETE", f"{ACCOUNTS}/{{id}}", {}, id="delete"),
        pytest.param("POST", "/api/v2/admin/keys/{id}/revoke", {}, id="revoke"),
    ],
)
async def test_ac30_an_unknown_id_is_404_not_found(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.request(method, path.format(id=987654), **kwargs)

    assert_error(response, 404, "not_found")


ADMIN_ROUTES = [
    pytest.param("GET", ACCOUNTS, {}, id="list"),
    pytest.param("GET", f"{ACCOUNTS}/1", {}, id="detail"),
    pytest.param("POST", ACCOUNTS, {"json": {"email": "a@example.org"}}, id="issue"),
    pytest.param("POST", f"{ACCOUNTS}/1/block", {"json": {"reason": "x"}}, id="block"),
    pytest.param("POST", f"{ACCOUNTS}/1/unblock", {}, id="unblock"),
    pytest.param("POST", "/api/v2/admin/keys/1/revoke", {}, id="revoke"),
    pytest.param("DELETE", f"{ACCOUNTS}/1", {}, id="delete"),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), ADMIN_ROUTES)
async def test_ac30_without_the_admin_token_every_route_is_401(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client("198.51.100.77") as client:
        response = await client.request(method, path, **kwargs)

    assert_error(response, 401, "unauthorized")


@pytest.mark.parametrize(("method", "path", "kwargs"), ADMIN_ROUTES)
async def test_ac30_with_a_wrong_admin_token_every_route_is_401(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client("198.51.100.78", {"X-Admin-Token": ADMIN_TOKEN + "x"}) as client:
        response = await client.request(method, path, **kwargs)

    assert_error(response, 401, "unauthorized")


async def test_ac30_the_token_is_checked_before_the_body_and_before_the_id(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client("198.51.100.79") as client:
        bad_body = await client.post(ACCOUNTS, json={"email": "nope"})
        unknown_id = await client.post(f"{ACCOUNTS}/987654/block", json={"reason": "x"})

    assert bad_body.status_code == 401
    assert unknown_id.status_code == 401


async def test_ac30_an_api_key_is_not_an_admin_token(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client("198.51.100.80", {"X-Api-Key": key.value}) as client:
        response = await client.get(ACCOUNTS)

    assert response.status_code == 401


# --- AC-31: detail ---


async def test_ac31_detail_has_keys_and_30_days_of_usage_and_no_request_log(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "detail@example.org")
    live = await create_key(env.factory, account_id)
    dead = await create_key(env.factory, account_id, revoked=True, revoke_reason="owner")
    today = datetime.now(UTC).date()
    inside = today - timedelta(days=3)
    await add_usage(env, live.id, account_id, today, requests=312, rejected=4)
    await add_usage(env, live.id, account_id, inside, requests=7)
    await add_usage(env, dead.id, account_id, inside, requests=2, rejected=1)
    await add_usage(env, live.id, account_id, today - timedelta(days=60), requests=9999)
    async with env.admin() as admin:
        response = await admin.get(f"{ACCOUNTS}/{account_id}")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body)[: len(ITEM_KEYS)] == ITEM_KEYS
    assert list(body)[len(ITEM_KEYS) :] == ["keys", "usage_daily"]
    assert body["email"] == "detail@example.org"
    assert (body["active_keys"], body["requests_30d"]) == (1, 321)
    keys = {k["id"]: k for k in body["keys"]}
    assert set(keys) == {live.id, dead.id}
    for item in keys.values():
        assert list(item) == [
            "id",
            "prefix",
            "created_at",
            "last_used_at",
            "revoked_at",
            "revoke_reason",
        ]
    assert keys[live.id]["revoked_at"] is None
    assert keys[live.id]["revoke_reason"] is None
    assert keys[dead.id]["revoke_reason"] == "owner"
    assert keys[dead.id]["prefix"] == dead.prefix
    usage = {u["day"]: (u["requests"], u["rejected"]) for u in body["usage_daily"]}
    assert usage == {today.isoformat(): (312, 4), inside.isoformat(): (9, 1)}
    for entry in body["usage_daily"]:
        assert list(entry) == ["day", "requests", "rejected"]


async def test_ac31_detail_has_no_hashes_and_no_per_request_journal(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.admin() as admin:
        response = await admin.get(f"{ACCOUNTS}/{key.account_id}")

    text_ = response.text
    assert key.value not in text_
    assert key_hash(key.value).hex() not in text_
    assert "key_hash" not in text_
    for forbidden in ("requests_log", "request_log", "journal", "ip"):
        assert forbidden not in response.json(), forbidden


async def test_ac31_detail_of_an_account_without_usage_has_an_empty_list(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory)
    async with env.admin() as admin:
        response = await admin.get(f"{ACCOUNTS}/{account_id}")

    body = response.json()
    assert body["keys"] == []
    assert body["usage_daily"] == []
    assert (body["active_keys"], body["requests_30d"]) == (0, 0)


async def test_ac31_detail_shows_the_block(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, blocked=True, block_reason="abuse")
    async with env.admin() as admin:
        body = (await admin.get(f"{ACCOUNTS}/{account_id}")).json()

    assert body["status"] == "blocked"
    assert body["block_reason"] == "abuse"
    assert body["verified_at"] is not None
