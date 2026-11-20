"""Client `api_key` values never reach the logs (stage 18A).

After `configure_logging()` the value of an `api_key` query parameter (any case, any position,
URL-encoded values included) is replaced by `<redacted>` in uvicorn.access records and in our
own log lines. Output is captured from the handlers `configure_logging` installs, so it does
not matter whether the filter sits on a logger or on a handler.
"""

import io
import logging
import sys
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


# --- through uvicorn's own formatter (it unpacks `record.args` as a 5-tuple) -----------------


@pytest.fixture
def uvicorn_output(captured: io.StringIO) -> Iterator[tuple[io.StringIO, list[BaseException]]]:
    """Configured handlers with uvicorn's AccessFormatter; handler errors are collected."""
    from uvicorn.logging import AccessFormatter

    errors: list[BaseException] = []
    saved_raise = logging.raiseExceptions
    logging.raiseExceptions = True
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)]
    originals = {}
    for handler in handlers:
        handler.setFormatter(
            AccessFormatter(
                '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
            )
        )
        originals[handler] = handler.handleError

        def record_error(record: logging.LogRecord, _h: logging.Handler = handler) -> None:
            exc = sys.exc_info()[1]
            if exc is not None:
                errors.append(exc)

        handler.handleError = record_error  # type: ignore[method-assign]
    try:
        yield captured, errors
    finally:
        logging.raiseExceptions = saved_raise
        for handler, original in originals.items():
            handler.handleError = original  # type: ignore[method-assign]


@pytest.mark.parametrize(("query", "expected"), QUERIES[:7])
def test_access_record_formats_through_uvicorn_formatter_without_errors(
    uvicorn_output: tuple[io.StringIO, list[BaseException]], query: str, expected: str
) -> None:
    buffer, errors = uvicorn_output

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.5:50000",
        "GET",
        f"/api/v1/rovers/curiosity/photos?{query}",
        "1.1",
        200,
    )

    output = buffer.getvalue()
    assert errors == [], f"logging error: {errors!r}"
    assert "Logging error" not in output
    assert SECRET not in output
    assert expected in output
    assert "200" in output
    assert "203.0.113.5:50000" in output


def test_access_record_stays_a_five_tuple_after_filtering(
    uvicorn_output: tuple[io.StringIO, list[BaseException]],
) -> None:
    """Whatever the filter does, the record reaching handlers keeps the uvicorn shape."""
    seen: list[logging.LogRecord] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logging.getLogger().addHandler(Spy())
    try:
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "203.0.113.5:50000",
            "GET",
            "/api/v2/rovers?api_key=" + SECRET,
            "1.1",
            200,
        )
    finally:
        logging.getLogger().handlers = [
            h for h in logging.getLogger().handlers if not isinstance(h, Spy)
        ]

    assert seen
    assert isinstance(seen[0].args, tuple) and len(seen[0].args) == 5
    assert seen[0].args[4] == 200
    assert SECRET not in str(seen[0].args)


def test_dict_args_record_is_redacted_without_errors(
    uvicorn_output: tuple[io.StringIO, list[BaseException]],
) -> None:
    from app.core.logging import configure_logging  # noqa: F401  (filters already installed)

    buffer, errors = uvicorn_output
    for handler in logging.getLogger().handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))

    logging.getLogger("app.some.module").warning(
        "fetch url=%(url)s status=%(status)d",
        {"url": "/api/v2/rovers?sol=1&api_key=" + SECRET, "status": 502},
    )

    output = buffer.getvalue()
    assert errors == []
    assert "Logging error" not in output
    assert SECRET not in output
    assert "api_key=<redacted>" in output
    assert "status=502" in output
