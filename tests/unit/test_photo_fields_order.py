"""Stage 19: `PHOTO_FIELDS` is the key order of `PhotoOut` (which ends with `near_stop`)."""

import pytest

from app.api.v2.schemas import PhotoOut

try:
    from app.api.v2.schemas import PHOTO_FIELDS
except ImportError:
    from app.services.v2 import PHOTO_FIELDS

pytestmark = pytest.mark.unit


def test_photo_fields_equal_the_photo_out_key_order() -> None:
    assert list(PHOTO_FIELDS) == list(PhotoOut.model_fields)
    assert len(PHOTO_FIELDS) == 17
    assert PHOTO_FIELDS[-2:] == ("mast", "near_stop")
