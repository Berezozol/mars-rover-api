"""Logging setup: one line per event, key details as key=value pairs."""

import logging

LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    # Keep third-party noise down unless we are debugging.
    if level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
