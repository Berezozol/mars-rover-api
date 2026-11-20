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
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    args: Any = record.args
    if isinstance(args, tuple):
        record.args = tuple(redact(a) if isinstance(a, str) else a for a in args)
    elif isinstance(args, Mapping):
        record.args = {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
    _redact_split_value(record, redact)


def _redact_split_value(record: logging.LogRecord, redact: Callable[[str], str]) -> None:
    """Last resort for a secret split between the format string and an argument
    (``"...?%s"`` with ``"api_key=..."``): flatten to the formatted message.
    """
    try:
        message = record.getMessage()
    except (TypeError, ValueError):
        return
    redacted = redact(message)
    if redacted != message:
        record.msg, record.args = redacted, ()


class RedactTelegramToken(logging.Filter):
    """Replaces Telegram bot tokens in URLs logged by httpx (e.g. its request lines)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record, lambda text: _TELEGRAM_TOKEN.sub("/bot<redacted>/", text))
        return True


_QUERY_PARAM = re.compile(r"([?&])([^=&\s\"']*)=([^&\s\"']*)")
REDACTED = "<redacted>"


def redact_api_key(message: str) -> str:
    """Replace the value of every ``api_key`` query parameter (any case, URL-encoded name too)."""

    def replace(match: re.Match[str]) -> str:
        if unquote(match.group(2)).lower() == "api_key":
            return f"{match.group(1)}{match.group(2)}={REDACTED}"
        return match.group(0)

    return _QUERY_PARAM.sub(replace, message)


class RedactApiKey(logging.Filter):
    """Keeps clients' ``?api_key=`` values (old API keys) out of access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record, redact_api_key)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, RedactTelegramToken) for f in httpx_logger.filters):
        httpx_logger.addFilter(RedactTelegramToken())
    # Our own log lines (any logger) go through the root handlers.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactApiKey) for f in handler.filters):
            handler.addFilter(RedactApiKey())
    # uvicorn's access log prints the full request line, query string included.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactApiKey) for f in access_logger.filters):
        access_logger.addFilter(RedactApiKey())
    # Keep third-party noise down unless we are debugging.
    if level.upper() != "DEBUG":
        httpx_logger.setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
