"""SampleNotFound (stage 13): 404 `sample_not_found`, a NotFoundError like the other 404s."""

import pytest

from app.core.errors import NotFoundError, SampleNotFound

pytestmark = pytest.mark.unit


def test_sample_not_found_is_a_404_not_found_error_with_its_own_code() -> None:
    assert issubclass(SampleNotFound, NotFoundError)
    assert SampleNotFound.status_code == 404
    assert SampleNotFound.code == "sample_not_found"
