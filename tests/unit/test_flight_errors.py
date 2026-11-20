"""FlightNotFound (stage 12): 404 `flight_not_found`, a NotFoundError like the other 404s."""

import pytest

from app.core.errors import FlightNotFound, NotFoundError

pytestmark = pytest.mark.unit


def test_flight_not_found_is_a_404_not_found_error_with_its_own_code() -> None:
    assert issubclass(FlightNotFound, NotFoundError)
    assert FlightNotFound.status_code == 404
    assert FlightNotFound.code == "flight_not_found"
