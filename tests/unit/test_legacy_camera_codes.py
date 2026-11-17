"""legacy_camera_codes: v1 `camera` value -> camera codes (stage 4 contract, owner decision).

Curiosity: FHAZ -> FHAZ + FHAZ_*, RHAZ -> RHAZ + RHAZ_*, NAVCAM -> NAVCAM + NAV_*,
MAST -> MAST + MAST_*, CHEMCAM -> CHEMCAM + CHEMCAM_*; any other code is an exact match.
Other rovers: always exact. Result: upper-case codes from known_codes, sorted; [] if none.
"""

import pytest

from app.core.cameras import CAMERA_NAMES, LEGACY_CAMERA_GROUPS, legacy_camera_codes

pytestmark = pytest.mark.unit

CURIOSITY_SEED = ["FHAZ", "RHAZ", "MAST", "CHEMCAM", "MAHLI", "MARDI", "NAVCAM"]
CURIOSITY_INSTRUMENTS = [
    "FHAZ_LEFT_A",
    "FHAZ_RIGHT_A",
    "FHAZ_LEFT_B",
    "FHAZ_RIGHT_B",
    "RHAZ_LEFT_A",
    "RHAZ_RIGHT_A",
    "RHAZ_LEFT_B",
    "RHAZ_RIGHT_B",
    "NAV_LEFT_A",
    "NAV_RIGHT_A",
    "NAV_LEFT_B",
    "NAV_RIGHT_B",
    "MAST_LEFT",
    "MAST_RIGHT",
    "CHEMCAM_RMI",
]
CURIOSITY_KNOWN = CURIOSITY_SEED + CURIOSITY_INSTRUMENTS


def test_legacy_camera_groups_constant_matches_contract() -> None:
    assert LEGACY_CAMERA_GROUPS == {
        "curiosity": {
            "FHAZ": "FHAZ_",
            "RHAZ": "RHAZ_",
            "NAVCAM": "NAV_",
            "MAST": "MAST_",
            "CHEMCAM": "CHEMCAM_",
        }
    }


def test_curiosity_instrument_codes_are_in_catalog() -> None:
    # Sanity check of the test data against the camera catalog.
    assert set(CURIOSITY_KNOWN) <= set(CAMERA_NAMES)


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ("FHAZ", ["FHAZ", "FHAZ_LEFT_A", "FHAZ_LEFT_B", "FHAZ_RIGHT_A", "FHAZ_RIGHT_B"]),
        ("fhaz", ["FHAZ", "FHAZ_LEFT_A", "FHAZ_LEFT_B", "FHAZ_RIGHT_A", "FHAZ_RIGHT_B"]),
        ("RHAZ", ["RHAZ", "RHAZ_LEFT_A", "RHAZ_LEFT_B", "RHAZ_RIGHT_A", "RHAZ_RIGHT_B"]),
        ("NavCam", ["NAVCAM", "NAV_LEFT_A", "NAV_LEFT_B", "NAV_RIGHT_A", "NAV_RIGHT_B"]),
        ("MAST", ["MAST", "MAST_LEFT", "MAST_RIGHT"]),
        ("chemcam", ["CHEMCAM", "CHEMCAM_RMI"]),
        ("MAST_LEFT", ["MAST_LEFT"]),
        ("mast_right", ["MAST_RIGHT"]),
        ("FHAZ_LEFT_B", ["FHAZ_LEFT_B"]),
        ("NAV_LEFT_B", ["NAV_LEFT_B"]),
        ("CHEMCAM_RMI", ["CHEMCAM_RMI"]),
        ("MAHLI", ["MAHLI"]),
        ("mardi", ["MARDI"]),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_curiosity_groups_and_exact_codes(camera: str, expected: list[str]) -> None:
    assert legacy_camera_codes("curiosity", camera, CURIOSITY_KNOWN) == expected


@pytest.mark.parametrize("camera", ["PANCAM", "xyz", "NAV", "FHAZ_", "MAST_", "NAVCAM_LEFT"])
def test_curiosity_unknown_camera_returns_empty(camera: str) -> None:
    assert legacy_camera_codes("curiosity", camera, CURIOSITY_KNOWN) == []


def test_curiosity_group_without_short_code_in_catalog_selects_prefixed_only() -> None:
    assert legacy_camera_codes("curiosity", "FHAZ", ["FHAZ_LEFT_B", "MAHLI"]) == ["FHAZ_LEFT_B"]


def test_curiosity_group_uses_exact_prefix_with_underscore() -> None:
    # NAVCAM groups NAV_*, not NAVCAM_*; FHAZ groups FHAZ_*, not FHAZX.
    known = ["NAVCAM_LEFT", "NAV_LEFT_B", "FHAZX", "FHAZ_RIGHT_B", "MASTCAM"]

    assert legacy_camera_codes("curiosity", "NAVCAM", known) == ["NAV_LEFT_B"]
    assert legacy_camera_codes("curiosity", "FHAZ", known) == ["FHAZ_RIGHT_B"]
    assert legacy_camera_codes("curiosity", "MAST", known) == []


def test_curiosity_result_is_sorted_regardless_of_known_order() -> None:
    known = ["MAST_RIGHT", "MAST", "MAST_LEFT"]

    assert legacy_camera_codes("curiosity", "mast", known) == ["MAST", "MAST_LEFT", "MAST_RIGHT"]


@pytest.mark.parametrize(
    ("rover", "camera", "known", "expected"),
    [
        ("perseverance", "NAVCAM", ["NAVCAM_LEFT", "NAVCAM_RIGHT", "NAV_LEFT_B"], []),
        ("perseverance", "navcam_left", ["NAVCAM_LEFT", "NAVCAM_RIGHT"], ["NAVCAM_LEFT"]),
        ("perseverance", "FHAZ", ["FHAZ", "FHAZ_LEFT_B"], ["FHAZ"]),
        ("spirit", "fhaz", ["FHAZ", "FHAZ_LEFT_B", "RHAZ"], ["FHAZ"]),
        ("spirit", "MAST", ["MAST_LEFT"], []),
        ("opportunity", "NavCam", ["NAVCAM", "NAV_LEFT_B"], ["NAVCAM"]),
        ("opportunity", "PANCAM", ["FHAZ"], []),
    ],
    ids=[
        "m2020-navcam-no-group",
        "m2020-exact",
        "m2020-fhaz-exact",
        "spirit-fhaz-exact",
        "spirit-mast-no-group",
        "opportunity-navcam-exact",
        "opportunity-unknown",
    ],
)
def test_other_rovers_exact_match_only(
    rover: str, camera: str, known: list[str], expected: list[str]
) -> None:
    assert legacy_camera_codes(rover, camera, known) == expected


def test_accepts_any_iterable_of_known_codes() -> None:
    known = (code for code in ["MAST_LEFT", "MAST_RIGHT"])

    assert legacy_camera_codes("curiosity", "MAST", known) == ["MAST_LEFT", "MAST_RIGHT"]
