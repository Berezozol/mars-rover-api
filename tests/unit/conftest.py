"""Fixtures for upstream unit tests. The strict `upstream_router` lives in tests/conftest.py."""

import pytest

from tests.unit._upstream import FakeClock


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
