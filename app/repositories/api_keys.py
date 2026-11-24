"""API key accounts, keys and usage counters (stage 20)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    DateTime,
    and_,
    cast,
    column,
    delete,
    exists,
    func,
    or_,
    select,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikeys import DISPLAY_PREFIX_LENGTH
from app.db.models import ApiAccount, ApiKey, ApiKeyRequest, ApiKeyUsageDaily
from app.repositories._common import FRESH, AnyRow, AnySelect

# Key of the advisory lock that serializes mail requests.
_MAIL_LOCK_KEY = 20_260_220

# Days of usage shown by admin (today included).
USAGE_DAYS = 30


@dataclass(frozen=True)
class KeyLookup:
    """What the request path needs to know about a key."""

    key_id: int
    account_id: int
    revoked: bool
    blocked: bool


async def find_key_by_hash(session: AsyncSession, key_hash: bytes) -> KeyLookup | None:
    statement = (
        select(ApiKey.id, ApiKey.account_id, ApiKey.revoked_at, ApiAccount.status)
        .join(ApiAccount, ApiAccount.id == ApiKey.account_id)
        .where(ApiKey.key_hash == key_hash)
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return KeyLookup(
        key_id=row.id,
        account_id=row.account_id,
        revoked=row.revoked_at is not None,
        blocked=row.status == "blocked",
    )


async def get_account(session: AsyncSession, account_id: int) -> ApiAccount | None:
    statement = select(ApiAccount).where(ApiAccount.id == account_id).execution_options(**FRESH)
    return (await session.execute(statement)).scalar_one_or_none()


async def get_or_create_account(
    session: AsyncSession, email: str, note: str | None, now: datetime
) -> ApiAccount:
    """The account of ``email`` (lowercase), created verified if it does not exist; the row
    is locked until the transaction ends, so concurrent issuance for it is serialized."""
    statement = (
        insert(ApiAccount)
        .values(email=email, note=note, verified_at=now)
        .on_conflict_do_nothing(index_elements=[ApiAccount.email])
    )
    await session.execute(statement)
    lookup = (
        select(ApiAccount)
        .where(ApiAccount.email == email)
        .with_for_update()
        .execution_options(**FRESH)
    )
    return (await session.execute(lookup)).scalar_one()


async def count_active_keys(session: AsyncSession, account_id: int) -> int:
    statement = (
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.account_id == account_id, ApiKey.revoked_at.is_(None))
    )
    return int((await session.execute(statement)).scalar_one())


async def add_key(session: AsyncSession, account_id: int, key_hash: bytes, prefix: str) -> ApiKey:
    key = ApiKey(account_id=account_id, key_hash=key_hash, prefix=prefix)
    session.add(key)
    await session.flush()
    await session.refresh(key)
    return key


async def get_key(session: AsyncSession, key_id: int) -> ApiKey | None:
    statement = select(ApiKey).where(ApiKey.id == key_id).execution_options(**FRESH)
    return (await session.execute(statement)).scalar_one_or_none()


async def revoke_key(session: AsyncSession, key_id: int, reason: str) -> ApiKey | None:
    """Revoke an active key (an already revoked one keeps its time and reason); None if the
    key does not exist."""
    statement = (
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoke_reason=reason)
    )
    await session.execute(statement)
    return await get_key(session, key_id)


async def set_blocked(
    session: AsyncSession, account_id: int, *, blocked: bool, reason: str | None
) -> ApiAccount | None:
    """Block (with a reason) or unblock an account; None if it does not exist."""
    values = (
        {"status": "blocked", "blocked_at": func.now(), "block_reason": reason}
        if blocked
        else {"status": "active", "blocked_at": None, "block_reason": None}
    )
    await session.execute(update(ApiAccount).where(ApiAccount.id == account_id).values(**values))
    return await get_account(session, account_id)


async def delete_account(session: AsyncSession, account_id: int) -> tuple[bool, list[int]]:
    """Delete the account; its keys and counters go with it (cascade). Returns whether it
    existed and the ids of the removed keys. Pending confirmation requests of the e-mail are
    removed too."""
    account = await get_account(session, account_id)
    if account is None:
        return False, []
    key_ids = list(
        (await session.execute(select(ApiKey.id).where(ApiKey.account_id == account_id)))
        .scalars()
        .all()
    )
    await session.execute(delete(ApiKeyRequest).where(ApiKeyRequest.email == account.email))
    await session.execute(delete(ApiAccount).where(ApiAccount.id == account_id))
    return True, key_ids


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_condition(q: str) -> ColumnElement[bool]:
    """Part of the e-mail (case-insensitive) or the display prefix of one of the keys."""
    by_email = ApiAccount.email.ilike(f"%{_escape_like(q)}%", escape="\\")
    if not q.startswith("si_"):
        return by_email
    prefix = q[:DISPLAY_PREFIX_LENGTH]
    by_key = exists().where(
        ApiKey.account_id == ApiAccount.id,
        ApiKey.prefix.startswith(prefix, autoescape=True),
    )
    return or_(by_email, by_key)


def _active_keys_column() -> ColumnElement[int]:
    return (
        select(func.count())
        .where(ApiKey.account_id == ApiAccount.id, ApiKey.revoked_at.is_(None))
        .scalar_subquery()
    )


def _requests_column(since: date) -> ColumnElement[int]:
    total = (
        select(func.coalesce(func.sum(ApiKeyUsageDaily.requests), 0))
        .where(ApiKeyUsageDaily.account_id == ApiAccount.id, ApiKeyUsageDaily.day >= since)
        .scalar_subquery()
    )
    return cast(total, BigInteger)


def _summary_select(today: date) -> AnySelect:
    return select(
        ApiAccount,
        _active_keys_column().label("active_keys"),
        _requests_column(today - timedelta(days=USAGE_DAYS - 1)).label("requests_30d"),
    )


async def list_accounts(
    session: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    today: date,
    limit: int,
    offset: int,
) -> tuple[list[AnyRow], int]:
    """Accounts newest first with their active key count and requests of the last 30 days;
    the second value is the total number of matches."""
    conditions = []
    if q:
        conditions.append(_search_condition(q))
    if status:
        conditions.append(ApiAccount.status == status)
    count = select(func.count()).select_from(ApiAccount)
    page = _summary_select(today)
    if conditions:
        count = count.where(and_(*conditions))
        page = page.where(and_(*conditions))
    total = int((await session.execute(count)).scalar_one())
    page = page.order_by(ApiAccount.created_at.desc(), ApiAccount.id.desc())
    rows = (await session.execute(page.limit(limit).offset(offset))).all()
    return list(rows), total


async def account_summary(session: AsyncSession, account_id: int, today: date) -> AnyRow | None:
    statement = _summary_select(today).where(ApiAccount.id == account_id)
    return (await session.execute(statement)).one_or_none()


async def list_keys(session: AsyncSession, account_id: int) -> list[ApiKey]:
    statement = (
        select(ApiKey)
        .where(ApiKey.account_id == account_id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .execution_options(**FRESH)
    )
    return list((await session.execute(statement)).scalars().all())


async def usage_daily(
    session: AsyncSession, account_id: int, today: date
) -> list[tuple[date, int, int]]:
    """Requests and rejected answers of the account per day, newest day first, last 30 days
    (days without requests are absent)."""
    statement = (
        select(
            ApiKeyUsageDaily.day,
            func.sum(ApiKeyUsageDaily.requests),
            func.sum(ApiKeyUsageDaily.rejected),
        )
        .where(
            ApiKeyUsageDaily.account_id == account_id,
            ApiKeyUsageDaily.day >= today - timedelta(days=USAGE_DAYS - 1),
        )
        .group_by(ApiKeyUsageDaily.day)
        .order_by(ApiKeyUsageDaily.day.desc())
    )
    rows = (await session.execute(statement)).all()
    return [(day, int(requests), int(rejected)) for day, requests, rejected in rows]


@dataclass(frozen=True)
class UsageRow:
    account_id: int
    key_id: int
    day: date
    requests: int
    rejected: int


# Statement parameters are limited to 32767: 5 per usage row, 2 per touched key.
_USAGE_BATCH = 5_000
_TOUCH_BATCH = 10_000


async def upsert_usage(session: AsyncSession, rows: Collection[UsageRow]) -> None:
    """Add the counts to the day's row of each key (ON CONFLICT adds, never overwrites).
    Keys deleted since the counts were taken are skipped. Written in batches."""
    items = list(rows)
    for start in range(0, len(items), _USAGE_BATCH):
        await _upsert_usage_batch(session, items[start : start + _USAGE_BATCH])


async def _upsert_usage_batch(session: AsyncSession, rows: list[UsageRow]) -> None:
    ids = {row.key_id for row in rows}
    existing = set((await session.execute(select(ApiKey.id).where(ApiKey.id.in_(ids)))).scalars())
    values = [
        {
            "key_id": row.key_id,
            "account_id": row.account_id,
            "day": row.day,
            "requests": row.requests,
            "rejected": row.rejected,
        }
        for row in rows
        if row.key_id in existing
    ]
    if not values:
        return
    statement = insert(ApiKeyUsageDaily).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[ApiKeyUsageDaily.key_id, ApiKeyUsageDaily.day],
        set_={
            "requests": ApiKeyUsageDaily.requests + statement.excluded.requests,
            "rejected": ApiKeyUsageDaily.rejected + statement.excluded.rejected,
        },
    )
    await session.execute(statement)


async def touch_keys(session: AsyncSession, last_used: dict[int, datetime]) -> None:
    """Set ``last_used_at`` of each key to its latest use (never moves it back); one UPDATE
    per batch of keys."""
    items = list(last_used.items())
    for start in range(0, len(items), _TOUCH_BATCH):
        data = values(
            column("id", BigInteger),
            column("used_at", DateTime(timezone=True)),
            name="touched",
        ).data(items[start : start + _TOUCH_BATCH])
        statement = (
            update(ApiKey)
            .where(ApiKey.id == data.c.id)
            .where(or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < data.c.used_at))
            .values(last_used_at=data.c.used_at)
        )
        await session.execute(statement)


async def delete_old_usage(session: AsyncSession, before: date) -> int:
    result = await session.execute(delete(ApiKeyUsageDaily).where(ApiKeyUsageDaily.day < before))
    return int(getattr(result, "rowcount", 0) or 0)


async def delete_expired_requests(session: AsyncSession, before: datetime) -> int:
    """Mail requests that are over: sent ones whose link expired, failed and still pending ones
    created, before ``before``."""
    statement = delete(ApiKeyRequest).where(
        or_(
            and_(ApiKeyRequest.status == "sent", ApiKeyRequest.expires_at < before),
            and_(ApiKeyRequest.status != "sent", ApiKeyRequest.created_at < before),
        )
    )
    result = await session.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)


async def delete_inactive_accounts(session: AsyncSession, before: datetime) -> int:
    """Delete accounts created before ``before`` none of whose keys was created or used since
    (keys, counters and mail requests of the e-mail go with them)."""
    recent = exists().where(
        ApiKey.account_id == ApiAccount.id,
        or_(ApiKey.created_at >= before, ApiKey.last_used_at >= before),
    )
    statement = (
        delete(ApiAccount)
        .where(ApiAccount.created_at < before, ~recent)
        .returning(ApiAccount.email)
    )
    emails = list((await session.execute(statement)).scalars())
    if emails:
        await session.execute(delete(ApiKeyRequest).where(ApiKeyRequest.email.in_(emails)))
    return len(emails)


# Mail requests (stage 20B)


async def lock_mail_requests(session: AsyncSession) -> None:
    """Serialize the limit checks and inserts of mail requests (released at commit/rollback)."""
    await session.execute(select(func.pg_advisory_xact_lock(_MAIL_LOCK_KEY)))


_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def _same_mailbox(email: str) -> ColumnElement[bool]:
    """Rows of the same mailbox: the address itself, and for Gmail also its dot and ``+tag``
    variants (counting only; the address is stored as given)."""
    local, _, domain = email.rpartition("@")
    if domain not in _GMAIL_DOMAINS:
        return ApiKeyRequest.email == email
    canon = local.split("+", 1)[0].replace(".", "")
    stored_local = func.split_part(ApiKeyRequest.email, "@", 1)
    plain = func.replace(func.split_part(stored_local, "+", 1), ".", "")
    return and_(func.split_part(ApiKeyRequest.email, "@", 2).in_(_GMAIL_DOMAINS), plain == canon)


async def count_mail_requests(
    session: AsyncSession, since: datetime, email: str | None = None
) -> tuple[int, datetime | None]:
    """Mail requests created since ``since`` (of one e-mail, or all) and the oldest of them."""
    statement = select(func.count(), func.min(ApiKeyRequest.created_at)).where(
        ApiKeyRequest.created_at >= since
    )
    if email is not None:
        statement = statement.where(_same_mailbox(email))
    row = (await session.execute(statement)).one()
    return int(row[0]), row[1]


async def get_account_by_email(session: AsyncSession, email: str) -> ApiAccount | None:
    statement = select(ApiAccount).where(ApiAccount.email == email).execution_options(**FRESH)
    return (await session.execute(statement)).scalar_one_or_none()


async def add_mail_request(
    session: AsyncSession, email: str, purpose: str, now: datetime, status: str = "pending"
) -> ApiKeyRequest:
    request = ApiKeyRequest(
        email=email,
        purpose=purpose,
        status=status,
        attempts=0,
        created_at=now,
        next_attempt_at=now,
    )
    session.add(request)
    await session.flush()
    return request


async def claim_pending(session: AsyncSession, now: datetime, limit: int) -> list[ApiKeyRequest]:
    """Due pending requests, locked (rows locked by another sender are skipped)."""
    statement = (
        select(ApiKeyRequest)
        .where(ApiKeyRequest.status == "pending", ApiKeyRequest.next_attempt_at <= now)
        .order_by(ApiKeyRequest.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .execution_options(**FRESH)
    )
    return list((await session.execute(statement)).scalars().all())


async def lock_request_by_token(
    session: AsyncSession, token_hash: bytes, purpose: str
) -> ApiKeyRequest | None:
    statement = (
        select(ApiKeyRequest)
        .where(ApiKeyRequest.token_hash == token_hash, ApiKeyRequest.purpose == purpose)
        .with_for_update()
        .execution_options(**FRESH)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def delete_requests_of_email(session: AsyncSession, email: str) -> None:
    await session.execute(delete(ApiKeyRequest).where(ApiKeyRequest.email == email))


async def mail_counters(
    session: AsyncSession, since: datetime
) -> tuple[int, int, int, datetime | None]:
    """Pending requests, sent since ``since``, failed created since ``since``, last sent_at."""
    r = ApiKeyRequest
    statement = select(
        func.count().filter(r.status == "pending"),
        func.count().filter(and_(r.status == "sent", r.sent_at >= since)),
        func.count().filter(and_(r.status == "failed", r.created_at >= since)),
        func.max(r.sent_at),
    )
    row = (await session.execute(statement)).one()
    return int(row[0]), int(row[1]), int(row[2]), row[3]


async def lock_account(session: AsyncSession, account_id: int) -> ApiAccount | None:
    statement = (
        select(ApiAccount)
        .where(ApiAccount.id == account_id)
        .with_for_update()
        .execution_options(**FRESH)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def revoke_active_keys(session: AsyncSession, account_id: int, reason: str) -> list[int]:
    """Revoke every active key of the account; returns their ids."""
    statement = (
        update(ApiKey)
        .where(ApiKey.account_id == account_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoke_reason=reason)
        .returning(ApiKey.id)
    )
    return list((await session.execute(statement)).scalars())
