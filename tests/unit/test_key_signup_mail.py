"""Mail sending and the settings of stage 20B (AC-21, AC-22).

`SmtpMailer` is tested with a fake `smtplib.SMTP` / `SMTP_SSL` (no socket is opened), the
messages come from the real templates. `Settings` is built without an env file.
"""

import smtplib
from email.message import EmailMessage
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.mail import MailerError, MemoryMailer, SmtpMailer
from app.services.mail_templates import delete_link_message, key_link_message

pytestmark = pytest.mark.unit

PASSWORD = "smtp-pass-Zx81QfT0aLm92"
TOKEN = "T" * 43
BASE = "https://solindex.example"


def make_settings(**extra: Any) -> Settings:
    values: dict[str, Any] = {
        "smtp_host": "smtp.test.invalid",
        "smtp_port": 587,
        "smtp_username": "sender@sender.example",
        "smtp_password": SecretStr(PASSWORD),
        "mail_from": "noreply@sender.example",
        "mail_from_name": "Sol Index",
        "public_base_url": BASE,
    }
    values.update(extra)
    return Settings(_env_file=None, **values)


class FakeSmtp:
    """Records what `SmtpMailer` does with `smtplib`; `instances` holds every connection."""

    instances: list["FakeSmtp"] = []
    fail_with: BaseException | None = None
    kind = "SMTP"

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self, **kwargs: Any) -> tuple[int, bytes]:
        self.calls.append("starttls")
        return 220, b"ready"

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        self.calls.append("login")
        self.login_args = (user, password)
        return 235, b"ok"

    def send_message(self, message: EmailMessage, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("send")
        self.send_kwargs = kwargs
        if FakeSmtp.fail_with is not None:
            raise FakeSmtp.fail_with
        self.sent.append(message)
        return {}

    def sendmail(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("send_message is expected; the test fake records only that")


class FakeSmtpSsl(FakeSmtp):
    kind = "SMTP_SSL"


@pytest.fixture(autouse=True)
def fake_smtplib(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.instances = []
    FakeSmtp.fail_with = None
    FakeSmtpSsl.instances = FakeSmtp.instances
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtpSsl)
    return FakeSmtp


# --- AC-21: SmtpMailer ---


async def test_ac21_the_key_mail_is_plain_utf8_from_mail_from_to_the_address() -> None:
    settings = make_settings()
    message = key_link_message(settings, "user@example.org", TOKEN)

    await SmtpMailer(settings).send(message)

    (smtp,) = FakeSmtp.instances
    (sent,) = smtp.sent
    assert sent.get_content_type() == "text/plain"
    assert sent.get_content_charset() == "utf-8"
    assert smtp.send_kwargs["to_addrs"] == ["user@example.org"]  # one recipient
    assert "noreply@sender.example" in str(sent["From"])
    assert "Sol Index" in str(sent["From"])
    assert "user@example.org" in str(sent["To"])
    assert str(sent["Subject"]).strip() != ""


async def test_ac21_the_link_host_comes_from_public_base_url() -> None:
    settings = make_settings(public_base_url="https://keys.example.net/")
    message = key_link_message(settings, "user@example.org", TOKEN)

    await SmtpMailer(settings).send(message)

    body = FakeSmtp.instances[0].sent[0].get_content()
    assert f"https://keys.example.net/keys/confirm#token={TOKEN}" in body
    assert "//keys/confirm" not in body  # the trailing slash of the base does not double up


async def test_ac21_the_deletion_mail_links_to_the_delete_page() -> None:
    settings = make_settings()

    message = delete_link_message(settings, "user@example.org", TOKEN)

    assert f"{BASE}/keys/delete#token={TOKEN}" in message.get_content()
    assert "/keys/confirm" not in message.get_content()


async def test_ac21_the_mail_text_has_the_expiry_the_ignore_hint_and_the_unofficial_note() -> None:
    settings = make_settings(key_token_ttl_minutes=45)

    body = key_link_message(settings, "user@example.org", TOKEN).get_content()

    assert "45" in body
    assert "ignore" in body.lower()
    assert "unofficial" in body.lower()


async def test_ac21_the_mail_text_has_no_tracking_pixel_or_html() -> None:
    message = key_link_message(make_settings(), "user@example.org", TOKEN)

    assert not message.is_multipart()
    assert "<img" not in message.get_content().lower()
    assert "<html" not in message.get_content().lower()


async def test_ac21_starttls_is_used_when_enabled() -> None:
    settings = make_settings(smtp_starttls=True)

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    (smtp,) = FakeSmtp.instances
    assert smtp.kind == "SMTP"
    assert (smtp.host, smtp.port) == ("smtp.test.invalid", 587)
    assert smtp.calls.index("starttls") < smtp.calls.index("login") < smtp.calls.index("send")


async def test_ac21_starttls_is_not_used_when_disabled() -> None:
    settings = make_settings(smtp_starttls=False, smtp_port=25)

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    (smtp,) = FakeSmtp.instances
    assert "starttls" not in smtp.calls
    assert smtp.port == 25


async def test_ac21_implicit_tls_uses_smtp_ssl_and_no_starttls() -> None:
    settings = make_settings(smtp_ssl=True, smtp_port=465)

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    (smtp,) = FakeSmtp.instances
    assert smtp.kind == "SMTP_SSL"
    assert smtp.port == 465
    assert "starttls" not in smtp.calls
    assert smtp.sent


@pytest.mark.parametrize("seconds", [3.0, 15.0, 40.5])
async def test_ac21_the_connection_timeout_is_smtp_timeout_seconds(seconds: float) -> None:
    settings = make_settings(smtp_timeout_seconds=seconds)

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    assert FakeSmtp.instances[0].kwargs["timeout"] == seconds


async def test_ac21_the_login_uses_the_configured_credentials() -> None:
    settings = make_settings()

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    assert FakeSmtp.instances[0].login_args == ("sender@sender.example", PASSWORD)


async def test_ac21_no_login_without_a_username() -> None:
    settings = make_settings(smtp_username=None, smtp_password=None)

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    assert "login" not in FakeSmtp.instances[0].calls


async def test_ac21_reply_to_is_set_only_when_configured() -> None:
    plain = key_link_message(make_settings(), "a@example.org", TOKEN)
    with_reply = key_link_message(
        make_settings(mail_reply_to="help@sender.example"), "a@example.org", TOKEN
    )

    assert plain["Reply-To"] is None
    assert "help@sender.example" in str(with_reply["Reply-To"])


@pytest.mark.parametrize(
    "to",
    ["a@example.org\r\nBcc: victim@example.org", "a@example.org\nBcc: v@example.org", "a\r@x.org"],
    ids=["crlf", "lf", "cr"],
)
def test_ac21_a_header_value_with_cr_or_lf_is_refused(to: str) -> None:
    with pytest.raises(ValueError):
        key_link_message(make_settings(), to, TOKEN)


async def test_ac21_the_mail_never_contains_the_smtp_password() -> None:
    message = key_link_message(make_settings(), "a@example.org", TOKEN)

    assert PASSWORD not in str(message)
    assert PASSWORD not in message.as_string()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(
            smtplib.SMTPResponseException(550, b"user@example.org rejected"), 550, id="550"
        ),
        pytest.param(smtplib.SMTPResponseException(421, b"try later"), 421, id="421"),
        pytest.param(
            smtplib.SMTPAuthenticationError(535, b"bad login sender@sender.example"), 535, id="535"
        ),
        pytest.param(smtplib.SMTPServerDisconnected("gone"), None, id="disconnected"),
        pytest.param(
            smtplib.SMTPConnectError(421, "no route to smtp.test.invalid"), 421, id="connect"
        ),
        pytest.param(ConnectionRefusedError("refused user@example.org"), None, id="refused"),
        pytest.param(TimeoutError("timed out"), None, id="timeout"),
    ],
)
async def test_ac21_a_smtp_failure_becomes_mailer_error_with_the_code_only(
    error: BaseException, code: int | None
) -> None:
    settings = make_settings()
    FakeSmtp.fail_with = error

    with pytest.raises(MailerError) as caught:
        await SmtpMailer(settings).send(key_link_message(settings, "user@example.org", TOKEN))

    assert caught.value.smtp_code == code
    rendered = f"{caught.value!s} {caught.value!r} {caught.value.args}"
    assert "user@example.org" not in rendered
    assert "sender@sender.example" not in rendered


async def test_ac21_nothing_is_sent_through_a_real_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake replaced `smtplib`: a real socket would raise here."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a real socket was opened")

    monkeypatch.setattr("socket.create_connection", refuse)
    settings = make_settings()

    await SmtpMailer(settings).send(key_link_message(settings, "a@example.org", TOKEN))

    assert len(FakeSmtp.instances) == 1


async def test_ac21_memory_mailer_keeps_messages_and_fails_on_demand() -> None:
    mailer = MemoryMailer()
    message = key_link_message(make_settings(), "a@example.org", TOKEN)
    mailer.fail_next = 2

    for _ in range(2):
        with pytest.raises(MailerError):
            await mailer.send(message)
    await mailer.send(message)

    assert mailer.sent == [message]
    assert mailer.fail_next == 0


# --- AC-22: settings ---

FULL = {
    "key_signup_enabled": True,
    "smtp_host": "smtp.test.invalid",
    "mail_from": "noreply@sender.example",
    "public_base_url": BASE,
}


@pytest.mark.parametrize(
    "missing",
    ["smtp_host", "mail_from", "public_base_url"],
    ids=["smtp-host", "mail-from", "base-url"],
)
@pytest.mark.parametrize("blank", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_ac22_signup_without_a_required_setting_fails_and_names_it(
    missing: str, blank: str | None
) -> None:
    values = {**FULL, missing: blank}

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)

    assert missing.upper() in str(caught.value)


def test_ac22_signup_with_everything_set_is_accepted() -> None:
    settings = Settings(_env_file=None, **FULL)

    assert settings.key_signup_enabled is True


def test_ac22_signup_off_needs_no_mail_settings() -> None:
    settings = Settings(_env_file=None, key_signup_enabled=False)

    assert settings.key_signup_enabled is False
    assert settings.smtp_host is None


@pytest.mark.parametrize(
    "missing",
    ["smtp_host", "mail_from", "public_base_url"],
    ids=["smtp-host", "mail-from", "base-url"],
)
def test_ac22_create_app_repeats_the_check_for_a_copied_settings(missing: str) -> None:
    """`model_copy(update=...)` skips validators; the application checks again at start."""
    settings = Settings(_env_file=None).model_copy(update={**FULL, missing: None})

    with pytest.raises(ValueError, match=missing.upper()):
        create_app(settings)


def test_ac22_the_error_text_has_no_values() -> None:
    values = {**FULL, "smtp_host": None, "smtp_password": SecretStr(PASSWORD)}

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)

    assert PASSWORD not in str(caught.value)
    assert "noreply@sender.example" not in str(caught.value)


def test_ac22_smtp_password_is_a_secret_and_stays_out_of_every_rendering() -> None:
    settings = make_settings()

    assert isinstance(settings.smtp_password, SecretStr)
    for rendering in (
        repr(settings),
        str(settings),
        settings.model_dump_json(),
        str(settings.model_dump()),
        repr(settings.smtp_password),
    ):
        assert PASSWORD not in rendering


def test_ac22_smtp_password_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PASSWORD", PASSWORD)
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")

    settings = Settings(_env_file=None)

    assert isinstance(settings.smtp_password, SecretStr)
    assert settings.smtp_password.get_secret_value() == PASSWORD
    assert PASSWORD not in repr(settings)


def test_ac22_the_documented_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.key_signup_enabled is False
    assert settings.key_signup_per_ip_per_hour == 5
    assert settings.key_signup_per_email_per_day == 3
    assert settings.key_signup_global_per_day == 50
    assert settings.key_token_ttl_minutes == 60
    assert settings.key_mail_max_attempts == 4
    assert settings.key_mail_retry_minutes == "1,5,15"
    assert settings.key_mail_degraded_minutes == 15
    assert settings.key_email_blocked_domains == ""
    assert settings.smtp_host is None
    assert settings.smtp_port == 587
    assert settings.smtp_starttls is True
    assert settings.smtp_ssl is False
    assert settings.smtp_timeout_seconds == 15
    assert settings.mail_from_name == "Sol Index"
    assert settings.mail_reply_to is None


@pytest.mark.parametrize(
    "name",
    [
        "key_signup_per_ip_per_hour",
        "key_signup_per_email_per_day",
        "key_signup_global_per_day",
        "key_token_ttl_minutes",
        "key_mail_max_attempts",
        "key_mail_degraded_minutes",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_ac22_limits_must_be_positive(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})
