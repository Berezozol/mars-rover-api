"""Sending e-mail (stage 20B): the ``Mailer`` protocol, the SMTP sender and a test double.

``smtplib`` runs in a thread (``asyncio.to_thread``): a handful of mails an hour does not need
an async SMTP library. Texts of SMTP errors can contain the recipient's address, so a failure
is reported only as ``MailerError`` with the numeric SMTP code, never with the original text.
"""

import asyncio
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings


class MailerError(Exception):
    """A mail could not be sent. Carries only the SMTP code (None: no SMTP answer, e.g. a
    refused connection or a timeout); never the text of the original error."""

    def __init__(self, smtp_code: int | None = None) -> None:
        super().__init__("Mail could not be sent")
        self.smtp_code = smtp_code


class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Send the message; ``MailerError`` when it could not be sent."""
        ...


def _clean(value: str) -> str:
    """The header value; ``ValueError`` (without the value) if it could inject a header."""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("A mail header contains control characters")
    return value


def build_message(settings: Settings, *, to: str, subject: str, body: str) -> EmailMessage:
    """A plain-text UTF-8 message from MAIL_FROM (with MAIL_FROM_NAME) to ``to``.

    Control characters (CR, LF...) in any header value raise ``ValueError``.
    """
    sender = _clean((settings.mail_from or "").strip())
    message = EmailMessage()
    try:
        message["From"] = str(
            Address(display_name=_clean(settings.mail_from_name), addr_spec=sender)
        )
    except Exception:
        raise ValueError("MAIL_FROM is not a valid address") from None
    message["To"] = _clean(to)
    message["Subject"] = _clean(subject)
    if settings.mail_reply_to:
        message["Reply-To"] = _clean(settings.mail_reply_to)
    message.set_content(body, subtype="plain", charset="utf-8")
    return message


class SmtpMailer:
    """Sends through the SMTP server of the settings (STARTTLS or implicit TLS)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> None:
        try:
            await asyncio.to_thread(self._send_sync, message)
        except smtplib.SMTPResponseException as exc:
            raise MailerError(int(exc.smtp_code)) from None
        except (smtplib.SMTPException, OSError, ssl.SSLError):
            raise MailerError(None) from None

    def _send_sync(self, message: EmailMessage) -> None:
        settings = self._settings
        timeout = settings.smtp_timeout_seconds
        host = settings.smtp_host or ""
        if settings.smtp_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, settings.smtp_port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            smtp = smtplib.SMTP(host, settings.smtp_port, timeout=timeout)
        with smtp:
            if settings.smtp_starttls and not settings.smtp_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password_value)
            smtp.send_message(message, to_addrs=[str(message["To"])])


class MemoryMailer:
    """Test double: keeps the messages in ``sent``; the next ``fail_next`` sends fail."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_next = 0

    async def send(self, message: EmailMessage) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise MailerError(None)
        self.sent.append(message)
