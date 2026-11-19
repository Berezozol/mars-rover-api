"""`stretch_to_8bit` and `encode_jpeg` (stage 9a, app.services.mer_images).

Contract: ``stretch_to_8bit(pixels, low_pct=0.5, high_pct=99.5) -> uint8`` maps the
low/high percentiles of the image to 0/255 and clips outside them; a flat image must not
divide by zero. ``encode_jpeg(pixels_u8, quality=90) -> bytes`` returns a grayscale (mode
``L``) JPEG of the same size.
"""

import io
import warnings

import numpy as np
import pytest
from PIL import Image

from app.services.mer_images import encode_jpeg, stretch_to_8bit

pytestmark = pytest.mark.unit


def _ramp(n: int = 10_000, shape: tuple[int, int] = (100, 100)) -> np.ndarray:
    return np.arange(n, dtype=np.int64).reshape(shape)


# --- stretch_to_8bit -----------------------------------------------------------------------


def test_stretch_ramp_returns_uint8_of_the_same_shape_over_the_full_range() -> None:
    out = stretch_to_8bit(_ramp())

    assert out.dtype == np.uint8
    assert out.shape == (100, 100)
    assert (int(out.min()), int(out.max())) == (0, 255)


def test_stretch_clips_values_outside_the_percentiles() -> None:
    pixels = _ramp()
    out = stretch_to_8bit(pixels)

    flat_in, flat_out = pixels.ravel(), out.ravel()
    low, high = np.percentile(pixels, [0.5, 99.5])
    assert (flat_out[flat_in <= low] == 0).all()
    assert (flat_out[flat_in >= high] == 255).all()
    # Something strictly between is neither clipped to 0 nor to 255.
    assert 0 < flat_out[flat_in == 5000][0] < 255


def test_stretch_is_linear_between_the_percentiles() -> None:
    pixels = _ramp()
    low, high = np.percentile(pixels, [0.5, 99.5])

    out = stretch_to_8bit(pixels).ravel()

    expected = np.clip((pixels.ravel() - low) / (high - low), 0, 1) * 255
    assert np.abs(out.astype(float) - expected).max() <= 1.0


def test_stretch_is_monotonic() -> None:
    out = stretch_to_8bit(_ramp()).ravel()

    assert (np.diff(out.astype(int)) >= 0).all()


def test_stretch_outliers_do_not_squash_the_rest_of_the_image() -> None:
    """A few hot pixels (above the 99.5th percentile) must not flatten the body."""
    pixels = np.tile(np.arange(100, 200, dtype=np.int64), (100, 1))  # 100 x 100, 100..199
    pixels[0, :3] = 60_000
    pixels[1, :3] = 0

    out = stretch_to_8bit(pixels)

    body = out[10:, :]
    assert int(body.max()) - int(body.min()) >= 200
    assert int(out[0, 0]) == 255 and int(out[1, 0]) == 0


@pytest.mark.parametrize("value", [0, 1, 255, 1000, 65535], ids=lambda v: f"value{v}")
def test_stretch_flat_image_does_not_divide_by_zero(value: int) -> None:
    pixels = np.full((16, 16), value, dtype=np.int64)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # numpy divide/invalid warnings become failures
        out = stretch_to_8bit(pixels)

    assert out.dtype == np.uint8 and out.shape == (16, 16)
    assert len(np.unique(out)) == 1


def test_stretch_nearly_flat_image_keeps_the_difference() -> None:
    pixels = np.full((20, 20), 100, dtype=np.int64)
    pixels[:, 10:] = 101

    out = stretch_to_8bit(pixels)

    assert out[0, 0] < out[0, 19]


def test_stretch_single_pixel_image_works() -> None:
    out = stretch_to_8bit(np.array([[1234]], dtype=np.int64))

    assert out.shape == (1, 1) and out.dtype == np.uint8


@pytest.mark.parametrize("dtype", ["uint8", "uint16", "int16", "int64", ">u2", "<u2"])
def test_stretch_accepts_the_usual_pixel_dtypes(dtype: str) -> None:
    pixels = (_ramp() % 4000).astype(dtype)

    out = stretch_to_8bit(pixels)

    assert out.dtype == np.uint8
    assert (int(out.min()), int(out.max())) == (0, 255)


def test_stretch_does_not_modify_the_input() -> None:
    pixels = _ramp().astype(np.uint16)
    before = pixels.copy()

    stretch_to_8bit(pixels)

    assert np.array_equal(pixels, before)


def test_stretch_custom_percentiles_change_the_clipping() -> None:
    pixels = _ramp()

    narrow = stretch_to_8bit(pixels, low_pct=10, high_pct=90)
    wide = stretch_to_8bit(pixels, low_pct=0, high_pct=100)

    assert (narrow == 0).sum() > (wide == 0).sum()
    assert (narrow == 255).sum() > (wide == 255).sum()
    assert (int(wide.min()), int(wide.max())) == (0, 255)
    # 0 / 100 percentiles: nothing is clipped, the first pixel is 0 and the last is 255.
    assert wide[0, 0] == 0 and wide[-1, -1] == 255


def test_stretch_real_8_bit_range_in_16_bit_storage_is_stretched() -> None:
    """Navcam EFF frames store 0..255 in 16-bit samples; the output still uses 0..255."""
    rng = np.random.default_rng(3)
    pixels = rng.integers(38, 256, size=(64, 64), dtype=np.int64)

    out = stretch_to_8bit(pixels)

    assert int(out.min()) == 0 and int(out.max()) == 255


# --- encode_jpeg ---------------------------------------------------------------------------


def _decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _gradient(lines: int, samples: int) -> np.ndarray:
    return np.tile(np.linspace(0, 255, samples).astype(np.uint8), (lines, 1))


def _noise(lines: int, samples: int) -> np.ndarray:
    return np.random.default_rng(5).integers(0, 256, size=(lines, samples), dtype=np.uint8)


@pytest.mark.parametrize(
    ("lines", "samples"), [(64, 64), (13, 37), (1, 1), (256, 100)], ids=lambda v: str(v)
)
def test_encode_jpeg_decodes_with_the_same_size_in_grayscale(lines: int, samples: int) -> None:
    data = encode_jpeg(_gradient(lines, samples))

    image = _decode(data)
    assert data[:3] == b"\xff\xd8\xff"
    assert image.format == "JPEG"
    assert image.mode == "L"
    assert image.size == (samples, lines)  # PIL size is (width, height)


def test_encode_jpeg_keeps_the_picture_content() -> None:
    pixels = _gradient(64, 128)

    decoded = np.asarray(_decode(encode_jpeg(pixels)), dtype=np.int64)

    assert np.abs(decoded - pixels.astype(np.int64)).mean() < 3.0
    assert decoded[:, 0].mean() < decoded[:, -1].mean()  # orientation: left dark, right bright


def test_encode_jpeg_is_not_transposed_for_non_square_images() -> None:
    pixels = np.zeros((20, 60), dtype=np.uint8)
    pixels[:10, :] = 255  # bright top half

    decoded = np.asarray(_decode(encode_jpeg(pixels)))

    assert decoded.shape == (20, 60)
    assert decoded[2:8].mean() > 200 and decoded[12:18].mean() < 50


def test_encode_jpeg_quality_changes_the_size() -> None:
    pixels = _noise(64, 64)

    low = encode_jpeg(pixels, quality=20)
    high = encode_jpeg(pixels, quality=95)

    assert len(low) < len(high)


def test_encode_jpeg_default_quality_is_90() -> None:
    pixels = _noise(64, 64)

    assert encode_jpeg(pixels) == encode_jpeg(pixels, quality=90)


def test_encode_jpeg_is_deterministic() -> None:
    pixels = _noise(32, 32)

    assert encode_jpeg(pixels) == encode_jpeg(pixels)


def test_stretch_then_encode_roundtrip_of_a_16_bit_frame() -> None:
    pixels = (_ramp(64 * 64, (64, 64)) * 16).astype(np.uint16)  # 0..65520

    data = encode_jpeg(stretch_to_8bit(pixels))

    image = _decode(data)
    assert image.size == (64, 64) and image.mode == "L"
