"""Bodies and answers of the key endpoints of stage 20B (docs/API.md, "Ключи API")."""

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from app.api.v2.schemas import (
    AccountStatus,
    ApiKeyInfoOut,
    IssuedAccountOut,
    IssuedKeyOut,
    UtcDateTime,
    key_info_out,
)
from app.core.config import Settings
from app.services.api_keys import IssuedKey, OwnAccount, normalize_email
from app.services.key_mail import MailStatus

# A token is 32 random bytes in base64url without padding.
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{43}$"
Token = Annotated[StrictStr, Field(pattern=TOKEN_PATTERN)]


class MailRequestIn(BaseModel):
    """POST /keys/requests and /account/deletion-requests body; ``website`` is the honeypot."""

    model_config = ConfigDict(extra="forbid")

    email: StrictStr
    website: StrictStr | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)


class ConfirmKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Token
    revoke_existing: StrictBool = False


class ConfirmDeletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Token


class MailRequestOut(BaseModel):
    status: Literal["accepted"] = "accepted"
    mail_delayed: bool


class NewKeyOut(BaseModel):
    """A key added from the account (no ``account`` object)."""

    key: str
    key_info: ApiKeyInfoOut


class UsageOut(BaseModel):
    today: int
    last_30_days: int


class LimitsOut(BaseModel):
    per_minute: int
    per_hour: int
    concurrent: int


class OwnAccountOut(BaseModel):
    email: str
    status: AccountStatus
    created_at: UtcDateTime
    keys: list[ApiKeyInfoOut]
    usage: UsageOut
    limits: LimitsOut


class KeyMailOut(BaseModel):
    pending: int
    sent_24h: int
    failed_24h: int
    global_limit: int
    last_success_at: UtcDateTime | None
    last_error_at: UtcDateTime | None


def confirmed_key_out(issued: IssuedKey) -> IssuedKeyOut:
    return IssuedKeyOut(
        key=issued.key,
        key_info=key_info_out(issued.record),
        account=IssuedAccountOut(email=issued.account.email, created_at=issued.account.created_at),
    )


def new_key_out(issued: IssuedKey) -> NewKeyOut:
    return NewKeyOut(key=issued.key, key_info=key_info_out(issued.record))


def own_account_out(own: OwnAccount, settings: Settings) -> OwnAccountOut:
    return OwnAccountOut(
        email=own.account.email,
        status=cast(AccountStatus, own.account.status),
        created_at=own.account.created_at,
        keys=[key_info_out(key) for key in own.keys],
        usage=UsageOut(today=own.requests_today, last_30_days=own.requests_30d),
        limits=LimitsOut(
            per_minute=settings.rate_limit_key_per_minute,
            per_hour=settings.rate_limit_key_per_hour,
            concurrent=settings.rate_limit_key_concurrent,
        ),
    )


def key_mail_out(status: MailStatus) -> KeyMailOut:
    return KeyMailOut(**vars(status))
