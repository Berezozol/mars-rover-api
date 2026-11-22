"""Logging setup: one line per event, key details as key=value pairs."""

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote

LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"

# Telegram puts the bot token in the URL path: .../bot<token>/sendMessage.
_TELEGRAM_TOKEN = re.compile(r"/bot[^/\s\"']+/")


def _redact_record(record: logging.LogRecord, redact: Callable[[str], str]) -> None:
    """Redact the record in place, keeping the shape of ``args``.

    Formatters such as uvicorn's access formatter unpack ``record.args`` (a 5-tuple), so the
    args must stay a tuple (or mapping); only their ``str`` items are rewritten.
    """
    original = (record.msg, record.args)
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    args: Any = record.args
    if isinstance(args, tuple):
        record.args = tuple(redact(a) if isinstance(a, str) else a for a in args)
    elif isinstance(args, Mapping):
        record.args = {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
    message = _format(record)
    if message is None:
        # The secret replaced a placeholder ("...?api_key=%s"): redact the formatted line.
        record.msg, record.args = original
        message = _format(record)
        if message is not None:
            record.msg, record.args = redact(message), ()
    else:
        # A secret split between the format string and an argument ("...?%s" with
        # "api_key=..."): flatten to the formatted message.
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
    _redact_exception(record, redact)


def _format(record: logging.LogRecord) -> str | None:
    try:
        return record.getMessage()
    except (TypeError, ValueError):
        return None


def _redact_exception(record: logging.LogRecord, redact: Callable[[str], str]) -> None:
    """Exception texts and stack traces can carry URLs and keys too: format the exception
    now (the formatters reuse ``exc_text``) and redact the text."""
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


class RedactTelegramToken(logging.Filter):
    """Replaces Telegram bot tokens in URLs logged by httpx (e.g. its request lines)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record, lambda text: _TELEGRAM_TOKEN.sub("/bot<redacted>/", text))
        return True


_QUERY_PARAM = re.compile(r"([?&])([^=&\s\"']*)=([^&\s\"']*)")
REDACTED = "<redacted>"


# A key of ours (stage 20) wherever it shows up: query string, header dump, exception text.
_OWN_KEY = re.compile(r"si_[A-Za-z0-9]{32}")


def redact_api_key(message: str) -> str:
    """Replace the value of every ``api_key`` query parameter (any case, URL-encoded name too)
    and any API key of our format."""

    def replace(match: re.Match[str]) -> str:
        if unquote(match.group(2)).lower() == "api_key":
            return f"{match.group(1)}{match.group(2)}={REDACTED}"
        return match.group(0)

    return _OWN_KEY.sub(REDACTED, _QUERY_PARAM.sub(replace, message))


class RedactApiKey(logging.Filter):
    """Keeps ``?api_key=`` values and our API keys out of every log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record, redact_api_key)
        return True


def _add_once(target: logging.Logger | logging.Handler) -> None:
    if not any(isinstance(f, RedactApiKey) for f in target.filters):
        target.addFilter(RedactApiKey())


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, RedactTelegramToken) for f in httpx_logger.filters):
        httpx_logger.addFilter(RedactTelegramToken())
    # Our own log lines (any logger) go through the root handlers; uvicorn's loggers have
    # handlers of their own (and the access log prints the full request line).
    for handler in logging.getLogger().handlers:
        _add_once(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        _add_once(uvicorn_logger)
        for handler in uvicorn_logger.handlers:
            _add_once(handler)
    # Keep third-party noise down unless we are debugging.
    if level.upper() != "DEBUG":
        httpx_logger.setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
