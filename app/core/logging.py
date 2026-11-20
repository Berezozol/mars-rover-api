"""Logging setup: one line per event, key details as key=value pairs."""

import logging
import re
from urllib.parse import unquote

LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"

# Telegram puts the bot token in the URL path: .../bot<token>/sendMessage.
_TELEGRAM_TOKEN = re.compile(r"/bot[^/\s\"']+/")


class RedactTelegramToken(logging.Filter):
    """Replaces Telegram bot tokens in URLs logged by httpx (e.g. its request lines)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_TOKEN.sub("/bot<redacted>/", message)
        if redacted != message:
            record.msg, record.args = redacted, None
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
        message = record.getMessage()
        redacted = redact_api_key(message)
        if redacted != message:
            record.msg, record.args = redacted, None
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
