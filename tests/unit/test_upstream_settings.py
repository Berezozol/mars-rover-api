"""Validation of upstream-related Settings fields."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("m2020_page_size", 0, id="m2020-page-size-0"),
        pytest.param("m2020_page_size", 101, id="m2020-page-size-101"),
        pytest.param("m2020_page_size", -5, id="m2020-page-size-negative"),
        pytest.param("msl_page_size", 0, id="msl-page-size-0"),
        pytest.param("upstream_max_pages", 0, id="max-pages-0"),
        pytest.param("upstream_max_retries", -1, id="max-retries-negative"),
        pytest.param("upstream_timeout_seconds", 0, id="timeout-0"),
        pytest.param("upstream_timeout_seconds", -1.5, id="timeout-negative"),
    ],
)
def test_settings_invalid_upstream_value_raises_validation_error(field: str, value: Any) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **{field: value})
    assert field in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("m2020_page_size", 1, id="m2020-page-size-1"),
        pytest.param("m2020_page_size", 100, id="m2020-page-size-100"),
        pytest.param("msl_page_size", 1, id="msl-page-size-1"),
        pytest.param("msl_page_size", 500, id="msl-page-size-500"),
        pytest.param("upstream_max_pages", 1, id="max-pages-1"),
        pytest.param("upstream_max_retries", 0, id="max-retries-0"),
        pytest.param("upstream_timeout_seconds", 0.1, id="timeout-small-positive"),
    ],
)
def test_settings_boundary_upstream_value_is_accepted(field: str, value: Any) -> None:
    assert getattr(Settings(_env_file=None, **{field: value}), field) == value
