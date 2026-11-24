"""Helpers for the stage 20B tests (docs/proposals/stage-20b-key-signup.md, API.md "Ключи API").

- `make_signup_app` builds an app with API keys and sign-up on (on top of `tests/_apikeys.py`),
  puts a `MemoryMailer` into `app.state.mailer`, switches the background `wake()` of the outbox
  off (tests run the outbox by hand with `run_once()`, so nothing races) and gives the sign-up
  limiter the fake clock. Nothing sleeps, nothing touches the network.
- Mail helpers read the token and the link out of a captured `EmailMessage`.
- DB helpers read `api_key_requests` and insert rows with explicit state for the outbox tests.
"""

import asyncio
import hashlib
import itertools
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from app.services.key_mail import KeyMailOutbox
from app.services.mail import MemoryMailer
from tests._apikeys import Factory, KeysApp, rows, scalar

V2 = "/api/v2"
BASE_URL = "https://solindex.example"
SMTP_PASSWORD = "smtp-pass-Zx81QfT0aLm92"
MAIL_FROM = "noreply@sender.example"
MAIL_FROM_NAME = "Sol Index"

REQUESTS = f"{V2}/keys/requests"
CONFIRM = f"{V2}/keys/confirm"
DELETION_REQUESTS = f"{V2}/account/deletion-requests"
DELETION_CONFIRM = f"{V2}/account/deletion-confirm"
ACCOUNT = f"{V2}/account"
ACCOUNT_KEYS = f"{V2}/account/keys"
KEY_MAIL = f"{V2}/admin/key-mail"

TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]{43})(?![A-Za-z0-9_-])")
LINK_RE = re.compile(r"https?://[^\s]+#token=[^\s]+")
ISO_UTC_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$")
ACCEPTED = {"status": "accepted", "mail_delayed": False}

KEY_INFO_FIELDS = ["id", "prefix", "created_at", "last_used_at", "revoked_at"]

_ip_counter = itertools.count(1)


def fresh_ip() -> str:
    """A new documentation-range address (192.0.2.0/24 is not used by `tests/_apikeys.py`)."""
    n = next(_ip_counter)
    return f"192.0.{2 + n // 250}.{n % 250 + 1}"


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def signup_overrides(**extra: Any) -> dict[str, Any]:
    """Settings for an app with sign-up on; `extra` overrides any field."""
    values: dict[str, Any] = {
        "key_signup_enabled": True,
        "smtp_host": "smtp.test.invalid",
        "smtp_username": "sender@sender.example",
        "smtp_password": SecretStr(SMTP_PASSWORD),
        "mail_from": MAIL_FROM,
        "mail_from_name": MAIL_FROM_NAME,
        "public_base_url": BASE_URL,
        # The scheduler must not take rows the tests queue; tests run the sender by hand.
        "key_mail_job_enabled": False,
    }
    values.update(extra)
    return values


class SlowMailer(MemoryMailer):
    """A mailer that yields to the event loop while "sending" (to overlap two passes)."""

    async def send(self, message: EmailMessage) -> None:
        await asyncio.sleep(0)
        await super().send(message)


@dataclass
class Signup:
    env: KeysApp
    mailer: MemoryMailer
    frozen_now: datetime | None = None

    @property
    def factory(self) -> Factory:
        return self.env.factory

    @property
    def outbox(self) -> Any:
        return self.env.app.state.key_mail_outbox

    def freeze_outbox_clock(self, at: datetime | None) -> None:
        """Fix the "now" of the sender (and its degraded window) at `at`; None: real time."""
        self.frozen_now = at

    def freeze_signup_clock(self, at: datetime) -> None:
        """Fix the "now" of the sign-up service (the UTC day of the global limit).

        Uses the private `_clock` of `KeySignupService` (a public `clock` would be better)."""
        self.env.app.state.key_signup_service._clock = lambda: at

    async def run(self) -> None:
        """One pass of the sender (`run_outbox()` of the spec)."""
        await self.outbox.run_once()

    def client(self, ip: str | None = None, headers: dict[str, str] | None = None) -> Any:
        return self.env.client(ip or fresh_ip(), headers)

    async def post(
        self, path: str, body: Any, ip: str | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        client = self.client(ip, headers)
        if body is None:  # httpx drops `json=None`; a JSON `null` has to be sent by hand
            return await client.post(
                path, content=b"null", headers={"Content-Type": "application/json"}
            )
        return await client.post(path, json=body)

    async def request_key(self, email: str, ip: str | None = None, **extra: Any) -> httpx.Response:
        return await self.post(REQUESTS, {"email": email, **extra}, ip)

    async def token_for(self, email: str, purpose: str = "key") -> str:
        """Ask for a mail, run the sender and return the token of the mail sent to `email`."""
        path = REQUESTS if purpose == "key" else DELETION_REQUESTS
        response = await self.post(path, {"email": email})
        assert response.status_code == 202, response.text
        await self.run()
        return token_of(last_mail_to(self.mailer, email.lower()))

    async def confirm(
        self, token: str, *, revoke_existing: bool | None = None, ip: str | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {"token": token}
        if revoke_existing is not None:
            body["revoke_existing"] = revoke_existing
        return await self.post(CONFIRM, body, ip)

    async def issue(self, email: str) -> str:
        """The whole flow: request, send, confirm. Returns the issued key."""
        response = await self.confirm(await self.token_for(email))
        assert response.status_code == 201, response.text
        key = response.json()["key"]
        assert isinstance(key, str)
        return key


SignupFactory = Callable[..., Awaitable[Signup]]


async def make_signup_app(
    make_keys_app: Callable[..., Any],
    mailer: MemoryMailer | None = None,
    **extra: Any,
) -> Signup:
    env: KeysApp = await make_keys_app(**signup_overrides(**extra))
    used = mailer or MemoryMailer()
    signup = Signup(env, used)
    if getattr(env.app.state, "key_mail_outbox", None) is not None:
        state = env.app.state
        state.mailer = used
        # The same outbox as the application's, with a clock the tests can freeze.
        state.key_mail_outbox = KeyMailOutbox(
            state.worker_session_factory,
            lambda: state.mailer,
            state.settings,
            clock=lambda: signup.frozen_now or datetime.now(UTC),
        )
        state.key_mail_outbox.auto_wake = False
    limiter = getattr(env.app.state, "signup_limiter", None)
    if limiter is not None and hasattr(limiter, "clock"):
        limiter.clock = env.clock
    return signup


# --- mails ---


def body_of(message: EmailMessage) -> str:
    content = message.get_content()
    assert isinstance(content, str)
    return content


def token_of(message: EmailMessage) -> str:
    match = TOKEN_RE.search(body_of(message))
    assert match, body_of(message)
    return match.group(1)


def link_of(message: EmailMessage) -> str:
    match = LINK_RE.search(body_of(message))
    assert match, body_of(message)
    return match.group(0)


def recipient(message: EmailMessage) -> str:
    return parseaddr(str(message["To"]))[1]


def recipients(mailer: MemoryMailer) -> list[str]:
    return [recipient(m) for m in mailer.sent]


def last_mail_to(mailer: MemoryMailer, email: str) -> EmailMessage:
    found = [m for m in mailer.sent if recipient(m) == email]
    assert found, (email, recipients(mailer))
    return found[-1]


# --- database ---


async def request_rows(
    factory: Factory, where: str = "true", **params: Any
) -> list[dict[str, Any]]:
    return await rows(
        factory,
        "SELECT id, email, purpose, status, attempts, token_hash, created_at, next_attempt_at, "
        "sent_at, expires_at, used_at FROM api_key_requests "
        f"WHERE {where} ORDER BY id",
        **params,
    )


async def count(factory: Factory, table: str, where: str = "true", **params: Any) -> int:
    value = await scalar(factory, f"SELECT count(*) FROM {table} WHERE {where}", **params)
    assert isinstance(value, int)
    return value


async def insert_request(
    factory: Factory,
    email: str,
    *,
    purpose: str = "key",
    status: str = "pending",
    created_hours_ago: float = 0.0,
    expires_in_hours: float | None = None,
    attempts: int = 0,
    next_in_minutes: float = 0.0,
    created_at: datetime | None = None,
) -> int:
    """A row of the queue with an explicit state (a `sent` row gets a token hash and `sent_at`)."""
    sent = status == "sent"
    async with factory() as session:
        row_id = await session.scalar(
            text(
                "INSERT INTO api_key_requests (email, purpose, status, attempts, created_at, "
                "next_attempt_at, token_hash, sent_at, expires_at) VALUES (:email, :purpose, "
                ":status, :attempts, COALESCE(CAST(:created_at AS timestamptz), "
                "now() - make_interval(secs => :age)), "
                "now() + make_interval(secs => :next), "
                "CASE WHEN :sent THEN CAST(:hash AS bytea) END, "
                "CASE WHEN :sent THEN COALESCE(CAST(:created_at AS timestamptz), "
                "now() - make_interval(secs => :age)) END, "
                "CASE WHEN :has_expiry THEN now() + make_interval(secs => :exp) END) RETURNING id"
            ),
            {
                "email": email,
                "purpose": purpose,
                "status": status,
                "attempts": attempts,
                "age": created_hours_ago * 3600,
                "created_at": created_at,
                "next": next_in_minutes * 60,
                "sent": sent,
                "hash": sha256(
                    f"{email}|{purpose}|{status}|{created_hours_ago}|{expires_in_hours}"
                ),
                "has_expiry": expires_in_hours is not None or sent,
                "exp": (expires_in_hours if expires_in_hours is not None else 1.0) * 3600,
            },
        )
        await session.commit()
    assert isinstance(row_id, int)
    return row_id


async def block_account(factory: Factory, email: str) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE api_accounts SET status = 'blocked', blocked_at = now(), "
                "block_reason = 'abuse' WHERE email = :e"
            ),
            {"e": email},
        )
        await session.commit()


async def make_due(factory: Factory) -> None:
    """Make every pending row due for the next pass (the retry schedule is checked separately)."""
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE api_key_requests SET next_attempt_at = now() - interval '1 second' "
                "WHERE status = 'pending'"
            )
        )
        await session.commit()


async def seconds_until_next_attempt(factory: Factory, row_id: int) -> float:
    value = await scalar(
        factory,
        "SELECT EXTRACT(EPOCH FROM next_attempt_at - now()) FROM api_key_requests WHERE id = :i",
        i=row_id,
    )
    return float(value)


async def dump_tables(factory: Factory) -> str:
    """Every row of the four key tables as text (to prove a secret is not stored)."""
    parts: list[str] = []
    for table in ("api_accounts", "api_keys", "api_key_requests", "api_key_usage_daily"):
        result = await rows(factory, f"SELECT t::text AS row FROM {table} t")
        parts.extend(row["row"] for row in result)
    return "\n".join(parts)


# --- logs ---


class Collector(logging.Handler):
    """Every log record of every level, with message, arguments, exception and extras."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text_of(self, record: logging.LogRecord) -> str:
        parts = [record.getMessage(), str(record.args), record.exc_text or ""]
        if record.exc_info:
            parts.append(str(record.exc_info[1]))
            parts.append(repr(record.exc_info[1]))
        parts.extend(str(value) for value in record.__dict__.values())
        return "\n".join(parts)

    def everything(self) -> str:
        return "\n".join(self.text_of(r) for r in self.records)


def assert_none_logged(collector: Collector, *secrets: str) -> None:
    for record in collector.records:
        text_ = collector.text_of(record)
        for secret in secrets:
            assert secret and secret not in text_, (record.name, record.levelname, text_)


def error_code(response: httpx.Response) -> str:
    body = response.json()
    assert list(body) == ["error"], body
    code = body["error"]["code"]
    assert isinstance(code, str)
    return code


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    """A v2 error with the expected status and code and `Cache-Control: no-store`."""
    assert response.status_code == status, response.text
    assert error_code(response) == code, response.text
    assert response.headers.get("cache-control") == "no-store", dict(response.headers)


def retry_after_seconds(response: httpx.Response) -> int:
    value = response.headers.get("retry-after")
    assert value is not None and value.isdigit() and int(value) >= 1, dict(response.headers)
    return int(value)
