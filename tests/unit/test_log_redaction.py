"""Client `api_key` values never reach the logs (stage 18A).

After `configure_logging()` the value of an `api_key` query parameter (any case, any position,
URL-encoded values included) is replaced by `<redacted>` in uvicorn.access records and in our
own log lines. Output is captured from the handlers `configure_logging` installs, so it does
not matter whether the filter sits on a logger or on a handler.
"""

import io
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import configure_logging

pytestmark = pytest.mark.unit

SECRET = "TOP-SECRET-VALUE"


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    configure_logging("INFO")
    buffer = io.StringIO()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(buffer)
    try:
        yield buffer
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def _access(path: str) -> None:
    """Log the way uvicorn's access logger does: '%s - "%s %s HTTP/%s" %d'."""
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.5:50000", "GET", path, "1.1", 200
    )


QUERIES = [
    pytest.param("api_key=" + SECRET, "api_key=<redacted>", id="only"),
    pytest.param("api_key=" + SECRET + "&sol=1", "api_key=<redacted>&sol=1", id="first"),
    pytest.param(
        "sol=1&api_key=" + SECRET + "&camera=fhaz",
        "sol=1&api_key=<redacted>&camera=fhaz",
        id="middle",
    ),
    pytest.param("sol=1&api_key=" + SECRET, "sol=1&api_key=<redacted>", id="last"),
    pytest.param("API_KEY=" + SECRET, "API_KEY=<redacted>", id="upper-case"),
    pytest.param("Api_Key=" + SECRET + "&sol=1", "Api_Key=<redacted>&sol=1", id="mixed-case"),
    pytest.param(
        "api_key=a%20b%2Fc%26d%3D" + SECRET + "&sol=1",
        "api_key=<redacted>&sol=1",
        id="url-encoded-value",
    ),
    pytest.param("api_key=&sol=1", "sol=1", id="empty-value-is-harmless"),
    pytest.param(
        "api_key=" + SECRET + "&api_key=" + SECRET + "2&sol=1",
        "api_key=<redacted>&api_key=<redacted>&sol=1",
        id="repeated",
    ),
]


@pytest.mark.parametrize(("query", "expected"), QUERIES)
def test_uvicorn_access_redacts_the_api_key_value(
    captured: io.StringIO, query: str, expected: str
) -> None:
    _access(f"/api/v1/rovers/curiosity/photos?{query}")

    output = captured.getvalue()
    assert SECRET not in output
    assert "a%20b" not in output
    if "<redacted>" in expected:
        assert f"/api/v1/rovers/curiosity/photos?{expected}" in output
    else:  # empty value: nothing secret, the other parameters stay intact
        assert "sol=1" in output


@pytest.mark.parametrize(("query", "expected"), QUERIES[:7])
def test_application_logs_redact_the_api_key_value(
    captured: io.StringIO, query: str, expected: str
) -> None:
    logging.getLogger("app.some.module").warning("request failed url=/api/v2/rovers?%s", query)

    output = captured.getvalue()
    assert SECRET not in output
    assert expected in output


def test_message_without_api_key_is_unchanged(captured: io.StringIO) -> None:
    _access("/api/v2/rovers/curiosity/photos?sol=1&camera=fhaz")

    assert "/api/v2/rovers/curiosity/photos?sol=1&camera=fhaz" in captured.getvalue()
    assert "<redacted>" not in captured.getvalue()


def test_redaction_keeps_the_record_usable_for_formatting(captured: io.StringIO) -> None:
    """Numeric args (status code) must survive the rewrite: `%d` with a str would raise."""
    _access("/api/v1/rovers?api_key=" + SECRET)

    assert " 200" in captured.getvalue()
    assert "--- Logging error ---" not in captured.getvalue()


def test_configure_logging_twice_does_not_stack_filters(captured: io.StringIO) -> None:
    configure_logging("INFO")
    buffer = io.StringIO()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(buffer)

    _access("/api/v1/rovers?api_key=" + SECRET)

    assert buffer.getvalue().count("<redacted>") == 1
    assert SECRET not in buffer.getvalue()
