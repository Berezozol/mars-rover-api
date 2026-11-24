"""AC-1 (docs/proposals/msl-thumbnails.md): the frame <-> thumbnail matching key.

`thumbnail_key(imageid)` is the first 22 characters of a MSSS imageid (Mastcam, MAHLI, MARDI):
the product letter and version that follow are dropped, so a frame (`C00`, `D01-D03`,
`E01-E0C`, MAHLI `R00`/`S00`) and its thumbnail (`I01`, MAHLI `T01`/`U01`) share one key.
"""

import pytest

from app.sources.msl import thumbnail_key

pytestmark = pytest.mark.unit

STEM = "4327MR1065450012201536"
MAHLI_STEM = "5073MH0001530001900057"
FRAME_LETTERS = ["C00", "D01", "D02", "D03", *(f"E0{c}" for c in "123456789ABC")]


@pytest.mark.parametrize("letters", FRAME_LETTERS)
def test_thumbnail_key_mastcam_frame_and_thumbnail_share_key_ac1(letters: str) -> None:
    frame = thumbnail_key(f"{STEM}{letters}_DXXX")

    assert frame is not None
    assert frame == thumbnail_key(f"{STEM}I01_DXXX")


@pytest.mark.parametrize(
    ("frame", "thumbnail"),
    [
        ("5073MH0001530001900057R00_DXXX", "5073MH0001530001900057T01_DXXX"),
        ("5073MH0001530001900058S00_DXXX", "5073MH0001530001900058U01_DXXX"),
        ("5073MD0018190000402180E01_DXXX", "5073MD0018190000402180I01_DXXX"),
        ("0100MD0000340000101525E01_DXXX", "0100MD0000340000101525I01_DXXX"),
        ("0100ML0004900050102961C00_DXXX", "0100ML0004900050102961I01_DXXX"),
    ],
    ids=["mahli-R00-T01", "mahli-S00-U01", "mardi-5073", "mardi-100", "mastcam-C00"],
)
def test_thumbnail_key_recorded_pairs_share_key_ac1(frame: str, thumbnail: str) -> None:
    assert thumbnail_key(frame) is not None
    assert thumbnail_key(frame) == thumbnail_key(thumbnail)


def test_thumbnail_key_is_the_first_22_characters_ac1() -> None:
    assert thumbnail_key(f"{STEM}E01_DXXX") == STEM
    assert thumbnail_key(f"{MAHLI_STEM}R00_DXXX") == MAHLI_STEM


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("4327MR1065450012201536E01_DXXX", "4327MR1065450012201537E01_DXXX"),
        ("4327MR1065450012201536E01_DXXX", "4327ML1065450012201536E01_DXXX"),
        ("4327MR1065450012201536E01_DXXX", "4328MR1065450012201536E01_DXXX"),
        ("5073MH0001530001900057R00_DXXX", "5073MH0001530001900058S00_DXXX"),
        ("5073ML1102680061307131C00_DXXX", "5073ML1102690021307132C00_DXXX"),
    ],
    ids=["next-shot", "other-camera", "other-sol", "mahli-shots", "mastcam-sequences"],
)
def test_thumbnail_key_different_shots_get_different_keys_ac1(first: str, second: str) -> None:
    assert thumbnail_key(first) != thumbnail_key(second)


@pytest.mark.parametrize("imageid", ["", "x"], ids=["empty", "short"])
def test_thumbnail_key_malformed_imageid_is_none_ac1(imageid: str) -> None:
    assert thumbnail_key(imageid) is None
