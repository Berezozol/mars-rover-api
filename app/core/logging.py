"""Logging setup: one line per event, key details as key=value pairs."""

import logging
import re

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


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, RedactTelegramToken) for f in httpx_logger.filters):
        httpx_logger.addFilter(RedactTelegramToken())
    # Keep third-party noise down unless we are debugging.
    if level.upper() != "DEBUG":
        httpx_logger.setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
