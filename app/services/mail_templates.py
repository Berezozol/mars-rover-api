"""Texts of the mails of stage 20B (plain text, English, no tracking, no images).

Links are built only from PUBLIC_BASE_URL, never from the request, and carry the token in the
fragment.
"""
# ruff: noqa: E501

from email.message import EmailMessage

from app.core.config import Settings
from app.services.mail import build_message

KEY_SUBJECT = "Your Sol Index API key link"
DELETE_SUBJECT = "Confirm deleting your Sol Index data"

_KEY_BODY = """Hello,

Someone asked for an API key for the Sol Index / Mars Rover API using this email address.
To get the key, open this link:

{link}

The link works once and expires in {expires}. The key is shown on that page only one time,
so copy it right away.

If this was not you, just ignore this email. No key will be created.

Sol Index is an unofficial project. It is not affiliated with or endorsed by NASA or JPL.

You can delete your data at any time with the "Delete my data" page of Sol Index, or by replying to this email.
"""

_DELETE_BODY = """Hello,

Someone asked to delete the data stored for this email address in the Sol Index / Mars Rover API.
To confirm, open this link:

{link}

The link works once and expires in {expires}. After you confirm, your account, your API keys
and your request counters are deleted for good.

If this was not you, just ignore this email. Nothing will be deleted.

Sol Index is an unofficial project. It is not affiliated with or endorsed by NASA or JPL.

Questions? Reply to this email.
"""


def _base(settings: Settings) -> str:
    return (settings.public_base_url or "").rstrip("/")


def key_link(settings: Settings, token: str) -> str:
    return f"{_base(settings)}/keys/confirm#token={token}"


def delete_link(settings: Settings, token: str) -> str:
    return f"{_base(settings)}/keys/delete#token={token}"


def _expires(settings: Settings) -> str:
    return f"{settings.key_token_ttl_minutes} minutes"


def key_link_message(settings: Settings, to: str, token: str) -> EmailMessage:
    body = _KEY_BODY.format(link=key_link(settings, token), expires=_expires(settings))
    return build_message(settings, to=to, subject=KEY_SUBJECT, body=body)


def delete_link_message(settings: Settings, to: str, token: str) -> EmailMessage:
    body = _DELETE_BODY.format(link=delete_link(settings, token), expires=_expires(settings))
    return build_message(settings, to=to, subject=DELETE_SUBJECT, body=body)
