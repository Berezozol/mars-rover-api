"""Helpers for the stage 20A API key tests (docs/proposals/stage-20-api-keys.md, API.md).

- `keys_app` builds an application with API keys and the per-IP / per-account limits on, inside
  its real lifespan, with a fake clock on every limiter and test routes that need no data
  (`__small`, `__blob`, `__hold`, `__boom`).
- `create_account` / `create_key` put accounts and keys into the database with plain SQL
  following the table layout of the spec (section 6), so most tests do not depend on the
  admin endpoints. The key value is generated here, the database gets only its SHA-256.
- Keys are random-looking but deterministic (seeded), nothing sleeps.
"""

import asyncio
import hashlib
import random
import re
import string
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, Response
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app

Factory = async_sessionmaker[AsyncSession]

V2 = "/api/v2"
ADMIN_TOKEN = "test-admin-token"
IP_A = "203.0.113.11"
IP_B = "203.0.113.12"
IP_C = "203.0.113.13"
ADMIN_IP = "198.51.100.200"
UNIT = 1024  # bytes of a v1 response that cost one request in these tests

KEY_RE = re.compile(r"^si_[A-Za-z0-9]{32}$")
BASE62 = string.ascii_letters + string.digits
_rng = random.Random(20261122)

SMALL_V2 = "/api/v2/__small"
SMALL_V1 = "/api/v1/__small"
SMALL_V1_ALT = "/mars-photos/api/v1/__small"
BLOB_V1 = "/api/v1/__blob"
HOLD_V2 = "/api/v2/__hold"
BOOM_V2 = "/api/v2/__boom"

V2_BODY_429 = {"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": None}}
V1_BODY_429 = {"errors": "Rate limit exceeded"}


def new_key() -> str:
    """A well-formed key (`si_` + 32 base62 characters) that nobody has issued."""
    return "si_" + "".join(_rng.choice(BASE62) for _ in range(32))


def nasa_key() -> str:
    """A 40-character api.nasa.gov style key (foreign format)."""
    return "".join(_rng.choice(BASE62) for _ in range(40))


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode("utf-8")).digest()


class Clock:
    def __init__(self, now: float = 50_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def key_settings(settings: Settings, **extra: Any) -> Settings:
    """Keys on, limits at the documented defaults, admin on; `extra` overrides any field."""
    values: dict[str, Any] = {
        "api_keys_enabled": True,
        "rate_limit_enabled": True,
        "rate_limit_per_minute": 60,
        "rate_limit_per_hour": 2000,
        "rate_limit_concurrent": 4,
        "rate_limit_key_per_minute": 120,
        "rate_limit_key_per_hour": 10_000,
        "rate_limit_key_concurrent": 8,
        "rate_limit_v1_bytes_per_request": UNIT,
        "api_key_max_per_account": 2,
        "api_key_cache_seconds": 60,
        "api_key_auth_max_failures": 20,
        "api_key_auth_window_minutes": 15,
        "api_key_usage_flush_seconds": 3600,  # tests flush by hand
        "admin_token": SecretStr(ADMIN_TOKEN),
        "sync_enabled": False,
    }
    values.update(extra)
    return settings.model_copy(update=values)


@dataclass
class KeysApp:
    app: FastAPI
    clock: Clock
    release: asyncio.Event
    entered: list[int]
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    @property
    def factory(self) -> Factory:
        factory: Factory = self.app.state.session_factory
        return factory

    def client(self, ip: str = IP_A, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app, client=(ip, 40000), raise_app_exceptions=False
        )
        client = httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)
        self.clients.append(client)
        return client

    def admin(self) -> httpx.AsyncClient:
        """Admin client on its own IP, so it never spends the budget of the IPs under test."""
        return self.client(ADMIN_IP, {"X-Admin-Token": ADMIN_TOKEN})

    async def wait_entered(self, count: int, limit: int = 200_000) -> None:
        """Wait until `count` requests are inside `__hold`."""
        for _ in range(limit):
            if len(self.entered) >= count:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"only {len(self.entered)} of {count} requests reached __hold")


def add_test_routes(app: FastAPI, release: asyncio.Event, entered: list[int]) -> None:
    def small() -> Response:
        return Response(content=b"ok", media_type="text/plain")

    def blob(n: int = 0) -> Response:
        return Response(content=b"a" * n, media_type="text/plain")

    async def hold() -> dict[str, bool]:
        entered.append(1)
        await release.wait()
        return {"ok": True}

    async def boom() -> None:
        raise RuntimeError("kaboom")

    for path in (SMALL_V2, SMALL_V1, SMALL_V1_ALT):
        app.add_api_route(path, small, methods=["GET", "HEAD"])
    app.add_api_route(BLOB_V1, blob, methods=["GET"])
    app.add_api_route("/mars-photos/api/v1/__blob", blob, methods=["GET"])
    app.add_api_route(HOLD_V2, hold, methods=["GET"])
    app.add_api_route(BOOM_V2, boom, methods=["GET"])


def install_clock(app: FastAPI, clock: Clock) -> None:
    """Fake time for every limiter (and the key cache, when it has a `clock` attribute)."""
    for name in ("rate_limiter", "key_rate_limiter", "api_key_auth_limiter", "api_key_resolver"):
        target = getattr(app.state, name, None)
        if target is not None and hasattr(target, "clock"):
            target.clock = clock


@asynccontextmanager
async def keys_app(settings: Settings, **extra: Any) -> AsyncIterator[KeysApp]:
    app = create_app(key_settings(settings, **extra))
    release = asyncio.Event()
    entered: list[int] = []
    add_test_routes(app, release, entered)
    clock = Clock()
    try:
        async with app.router.lifespan_context(app):
            install_clock(app, clock)
            env = KeysApp(app, clock, release, entered)
            try:
                yield env
            finally:
                release.set()
                for client in env.clients:
                    await client.aclose()
    finally:
        await app.state.engine.dispose()


KeysAppFactory = Callable[..., Awaitable[KeysApp]]


# --- database helpers ---


@dataclass(frozen=True)
class IssuedKey:
    value: str
    id: int
    account_id: int

    @property
    def prefix(self) -> str:
        return self.value[:11]


async def create_account(
    factory: Factory,
    email: str = "user@example.org",
    *,
    blocked: bool = False,
    block_reason: str | None = None,
    note: str | None = None,
    created_at: datetime | None = None,
) -> int:
    async with factory() as session:
        account_id = await session.scalar(
            text(
                "INSERT INTO api_accounts (email, status, verified_at, blocked_at, block_reason, "
                "note, created_at) VALUES (:email, :status, now(), "
                "CASE WHEN :blocked THEN now() END, :reason, :note, "
                "COALESCE(CAST(:created_at AS timestamptz), now())) RETURNING id"
            ),
            {
                "email": email,
                "status": "blocked" if blocked else "active",
                "blocked": blocked,
                "reason": block_reason if blocked else None,
                "note": note,
                "created_at": created_at,
            },
        )
        await session.commit()
    assert isinstance(account_id, int)
    return account_id


async def create_key(
    factory: Factory,
    account_id: int,
    *,
    key: str | None = None,
    revoked: bool = False,
    revoke_reason: str = "owner",
) -> IssuedKey:
    value = key or new_key()
    async with factory() as session:
        key_id = await session.scalar(
            text(
                "INSERT INTO api_keys (account_id, key_hash, prefix, revoked_at, revoke_reason) "
                "VALUES (:account, :hash, :prefix, CASE WHEN :revoked THEN now() END, "
                "CASE WHEN :revoked THEN CAST(:reason AS text) END) RETURNING id"
            ),
            {
                "account": account_id,
                "hash": key_hash(value),
                "prefix": value[:11],
                "revoked": revoked,
                "reason": revoke_reason,
            },
        )
        await session.commit()
    assert isinstance(key_id, int)
    return IssuedKey(value, key_id, account_id)


async def account_with_key(
    factory: Factory, email: str = "user@example.org", **account: Any
) -> IssuedKey:
    """An account with one active key."""
    return await create_key(factory, await create_account(factory, email, **account))


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def rows(factory: Factory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]


# --- response helpers ---


def remaining(response: httpx.Response) -> int:
    return int(response.headers["x-ratelimit-remaining"])


def retry_after(response: httpx.Response) -> int:
    value = response.headers.get("retry-after")
    assert value is not None and value.isdigit(), dict(response.headers)
    return int(value)


async def statuses(client: httpx.AsyncClient, url: str, n: int, **kwargs: Any) -> list[int]:
    return [(await client.get(url, **kwargs)).status_code for _ in range(n)]


def assert_v2_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """A v2 error: exact envelope, `no-store`, and the rate limit headers of the IP tier."""
    assert response.status_code == status, response.text
    body = response.json()
    assert list(body) == ["error"], body
    assert list(body["error"]) == ["code", "message", "details"], body
    assert body["error"]["code"] == code, body
    assert body["error"]["details"] is None
    assert response.headers.get("cache-control") == "no-store", dict(response.headers)
    for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
        assert name in response.headers, (name, dict(response.headers))
    return dict(body["error"])


def assert_v1_error(response: httpx.Response, status: int, message: str) -> None:
    assert response.status_code == status, response.text
    assert response.json() == {"errors": message}
    assert response.headers.get("cache-control") == "no-store", dict(response.headers)
    for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
        assert name in response.headers, (name, dict(response.headers))
