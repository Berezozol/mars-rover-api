"""Keys of our format never reach the application logs (stage 20A, AC-32, unit part).

The `api_key` query parameter is redacted by the existing filter (see
test_log_redaction.py); here the same is checked for real `si_` keys. The end-to-end part
(requests through the app with the key in the header and in the query) is in
tests/api/test_api_keys_logs.py.
"""

import io
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import configure_logging

pytestmark = pytest.mark.unit

KEY = "si_Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm"


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    configure_logging("DEBUG")
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


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(f"/api/v2/rovers?api_key={KEY}", id="v2-only"),
        pytest.param(f"/api/v1/rovers/curiosity/photos?sol=1&api_key={KEY}", id="v1-last"),
        pytest.param(f"/api/v2/rovers?api_key={KEY}&x=1", id="v2-first"),
    ],
)
def test_ac32_access_log_does_not_contain_the_si_key(captured: io.StringIO, path: str) -> None:
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.5:50000", "GET", path, "1.1", 200
    )

    output = captured.getvalue()
    assert KEY not in output
    assert KEY[3:] not in output
    assert "api_key=<redacted>" in output


def test_ac32_application_logger_does_not_contain_the_si_key(captured: io.StringIO) -> None:
    logging.getLogger("app.api.ratelimit").warning(
        "request failed url=/api/v2/rovers?api_key=%s", KEY
    )

    assert KEY not in captured.getvalue()


# --- exception text (review of 20A) ---


def _raise_and_log(message: str) -> None:
    try:
        raise ValueError(message)
    except ValueError:
        logging.getLogger("app.some.module").exception("Handler failed")


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(f"bad key {KEY} rejected", id="bare-key"),
        pytest.param(f"GET /api/v2/rovers?api_key={KEY} failed", id="in-url"),
        pytest.param(f"header X-Api-Key: {KEY}", id="header-text"),
    ],
)
def test_ac32_log_exception_with_a_si_key_in_the_exception_text_is_redacted(
    captured: io.StringIO, message: str
) -> None:
    _raise_and_log(message)

    output = captured.getvalue()
    assert "Handler failed" in output
    assert "ValueError" in output  # the traceback is there, only the key is not
    assert KEY not in output
    assert KEY[3:] not in output
