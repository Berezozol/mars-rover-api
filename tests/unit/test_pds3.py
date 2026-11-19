"""`parse_pds3_image` (stage 9a): PDS3 label + raw pixels of MER EDR products.

Contract (agreed with backend-dev, docs/DATA-SOURCES.md section MER): ASCII PDS3 label,
``RECORD_BYTES``, ``LABEL_RECORDS``, ``^IMAGE`` as a record number counted from 1, as
``("file", n)`` or as ``<BYTES>``; ``SAMPLE_BITS`` 8/16; ``SAMPLE_TYPE`` MSB/LSB/UNSIGNED;
``BANDS`` > 1 -> the first band; ``LINE_PREFIX_BYTES`` / ``LINE_SUFFIX_BYTES`` skipped.
Broken input -> ``Pds3Error`` (a ``ValueError``); limits: input <= 64 MiB, LINES and
LINE_SAMPLES <= 8192. Data: synthetic files from ``tests/unit/_pds3.py`` and the real
samples recorded by ``scripts/record_fixtures.py --part mer-img``.
"""

import re
import time

import numpy as np
import pytest

from app.sources.pds3 import Pds3Error, Pds3Image, parse_pds3_image
from tests.unit._pds3 import (
    SAMPLE_FORMATS,
    build_pds3,
    make_pixels,
    real_samples,
    reference_pixels,
)

pytestmark = pytest.mark.unit

POINTERS = ["record", "tuple", "bytes"]


def assert_image(image: Pds3Image, pixels: np.ndarray, bits: int) -> None:
    lines, samples = pixels.shape
    assert (image.lines, image.samples, image.sample_bits) == (lines, samples, bits)
    assert image.pixels.shape == (lines, samples)
    assert np.array_equal(image.pixels, pixels)


# --- synthetic files: every supported variation -------------------------------------------


@pytest.mark.parametrize("pointer", POINTERS)
@pytest.mark.parametrize("fmt", list(SAMPLE_FORMATS))
def test_parse_sample_formats_and_pointer_forms_returns_exact_pixels(
    fmt: str, pointer: str
) -> None:
    pixels = make_pixels(5, 9, fmt)
    built = build_pds3(pixels, fmt=fmt, pointer=pointer)

    image = parse_pds3_image(built.data)

    assert_image(image, pixels, SAMPLE_FORMATS[fmt][0])


@pytest.mark.parametrize("pointer", POINTERS)
@pytest.mark.parametrize("record_bytes", [126, 512, 2048], ids=lambda n: f"rec{n}")
def test_parse_realistic_label_uses_image_object_not_nested_groups(
    pointer: str, record_bytes: int
) -> None:
    """Nested GROUPs carry LINES = 64 / 1024 before the IMAGE object; ^IMAGE_HEADER is first."""
    pixels = make_pixels(7, 11, "i16-msb")
    built = build_pds3(pixels, pointer=pointer, record_bytes=record_bytes, realistic=True)

    image = parse_pds3_image(built.data)

    assert_image(image, pixels, 16)


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [(4, 0), (0, 3), (8, 2), (1, 1)],
    ids=["prefix4", "suffix3", "prefix8-suffix2", "odd-1-1"],
)
@pytest.mark.parametrize("fmt", ["u8", "i16-msb", "u16-lsb"])
@pytest.mark.parametrize("pointer", POINTERS)
def test_parse_line_prefix_and_suffix_bytes_are_skipped(
    pointer: str, fmt: str, prefix: int, suffix: int
) -> None:
    pixels = make_pixels(6, 10, fmt)
    built = build_pds3(pixels, fmt=fmt, pointer=pointer, prefix=prefix, suffix=suffix)

    image = parse_pds3_image(built.data)

    assert_image(image, pixels, SAMPLE_FORMATS[fmt][0])


@pytest.mark.parametrize("fmt", ["u8", "i16-msb", "u16-lsb"])
@pytest.mark.parametrize("bands", [2, 3])
@pytest.mark.parametrize("prefix", [0, 4], ids=["no-prefix", "prefix4"])
def test_parse_several_bands_returns_first_band_only(fmt: str, bands: int, prefix: int) -> None:
    pixels = make_pixels(6, 8, fmt)
    built = build_pds3(pixels, fmt=fmt, bands=bands, prefix=prefix)

    image = parse_pds3_image(built.data)

    assert_image(image, pixels, SAMPLE_FORMATS[fmt][0])


def test_parse_trailing_bytes_after_image_are_ignored() -> None:
    pixels = make_pixels(4, 4, "i16-lsb")
    built = build_pds3(pixels, fmt="i16-lsb")

    image = parse_pds3_image(built.data + b"\x00" * 1000)

    assert_image(image, pixels, 16)


def test_parse_pixels_are_a_2d_array_with_exact_values_for_unsigned_top_range() -> None:
    pixels = np.array([[0, 1, 32767], [32768, 40000, 65535]], dtype=np.int64)
    built = build_pds3(pixels, fmt="u16-msb")

    image = parse_pds3_image(built.data)

    assert image.pixels.ndim == 2
    assert image.pixels.tolist() == pixels.tolist()


@pytest.mark.parametrize(
    ("lines", "samples"), [(8192, 1), (1, 8192)], ids=["8192-lines", "8192-samples"]
)
def test_parse_dimensions_at_the_limit_are_accepted(lines: int, samples: int) -> None:
    pixels = make_pixels(lines, samples, "u8")
    built = build_pds3(pixels, fmt="u8")

    image = parse_pds3_image(built.data)

    assert_image(image, pixels, 8)


# --- real MER samples ----------------------------------------------------------------------


def _image_object(data: bytes) -> str:
    text = data[:200_000].decode("latin-1")
    match = re.search(r"OBJECT\s*=\s*IMAGE\r?\n(.*?)END_OBJECT\s*=\s*IMAGE", text, re.DOTALL)
    assert match is not None
    return match.group(1)


def _number(block: str, key: str) -> float:
    match = re.search(rf"^\s*{key}\s*=\s*([\d.]+)", block, re.MULTILINE)
    assert match is not None, key
    return float(match.group(1))


@pytest.mark.parametrize("path", real_samples(), ids=lambda p: p.name)
def test_parse_real_mer_sample_matches_label_and_independent_reader(path) -> None:
    data = path.read_bytes()
    block = _image_object(data)
    lines, samples = int(_number(block, "LINES")), int(_number(block, "LINE_SAMPLES"))

    image = parse_pds3_image(data)

    assert (image.lines, image.samples, image.sample_bits) == (lines, samples, 16)
    assert image.pixels.shape == (lines, samples)
    assert np.array_equal(image.pixels, reference_pixels(data, lines, samples))
    # The label's MINIMUM / MAXIMUM are the true extremes of the stored pixels.
    assert (int(image.pixels.min()), int(image.pixels.max())) == (
        int(_number(block, "MINIMUM")),
        int(_number(block, "MAXIMUM")),
    )


@pytest.mark.parametrize("path", real_samples(), ids=lambda p: p.name)
def test_real_mer_labels_have_no_encoding_or_compression_keyword_in_the_image_object(path) -> None:
    """Fact for the parser: MER EDR pixels are stored raw (compression is onboard only)."""
    block = _image_object(path.read_bytes())

    assert "ENCODING_TYPE" not in block
    assert not re.search(r"^\s*COMPRESSION\s*=", block, re.MULTILINE)


@pytest.mark.parametrize(
    ("name", "shape", "extremes", "first_row"),
    [
        ("2p135232363esf2700p2682l8m1.img", (63, 63), (28, 1114), None),
        ("2n160633049edna2k1f0006r0m1.img", (256, 256), (336, 3156), [2642, 2040, 1725, 2058]),
    ],
    ids=["esf-subframe", "edn-downsampled"],
)
def test_parse_committed_real_samples_hard_coded_facts(name, shape, extremes, first_row) -> None:
    path = next(p for p in real_samples() if p.name == name)

    image = parse_pds3_image(path.read_bytes())

    assert image.pixels.shape == shape
    assert (int(image.pixels.min()), int(image.pixels.max())) == extremes
    if first_row:
        assert image.pixels[0, :4].tolist() == first_row


# --- broken input --------------------------------------------------------------------------


def _valid() -> bytes:
    return build_pds3(make_pixels(8, 8, "i16-msb")).data


def _without_end() -> bytes:
    data = _valid()
    assert b"\r\nEND\r\n" in data
    return data.replace(b"\r\nEND\r\n", b"\r\n      \r\n", 1)


def _garbage() -> bytes:
    return np.random.default_rng(1).integers(0, 256, size=5000, dtype=np.uint8).tobytes()


BROKEN = {
    "empty": lambda: b"",
    "garbage": _garbage,
    "jpeg-not-pds3": lambda: b"\xff\xd8\xff\xe0" + b"\x00" * 600,
    "label-only-text": lambda: b"PDS_VERSION_ID = PDS3\r\nRECORD_BYTES = 512\r\n",
    "no-end": _without_end,
    "truncated-pixels": lambda: _valid()[:-1],
    "truncated-half": lambda: _valid()[: len(_valid()) - 60],
    "truncated-in-label": lambda: _valid()[:200],
    "lines-zero": lambda: build_pds3(make_pixels(4, 4), lines=0).data,
    "samples-zero": lambda: build_pds3(make_pixels(4, 4), samples=0).data,
    "lines-declared-too-many": lambda: build_pds3(make_pixels(4, 4), lines=100).data,
    "samples-declared-too-many": lambda: build_pds3(make_pixels(4, 4), samples=100).data,
    "lines-huge": lambda: build_pds3(make_pixels(4, 4), lines=10**9).data,
    "samples-huge": lambda: build_pds3(make_pixels(4, 4), samples=10**9).data,
    "both-huge": lambda: build_pds3(make_pixels(4, 4), lines=10**6, samples=10**6).data,
    "lines-over-limit-with-data": lambda: build_pds3(make_pixels(8193, 1, "u8"), fmt="u8").data,
    "samples-over-limit-with-data": lambda: build_pds3(make_pixels(1, 8193, "u8"), fmt="u8").data,
    "lines-not-a-number": lambda: _valid().replace(
        b"LINES                           = 8", b"LINES                           = x"
    ),
    "sample-bits-12": lambda: _valid().replace(
        b"SAMPLE_BITS                     = 16", b"SAMPLE_BITS                     = 12"
    ),
    "sample-bits-32": lambda: _valid().replace(
        b"SAMPLE_BITS                     = 16", b"SAMPLE_BITS                     = 32"
    ),
    "sample-type-real": lambda: _valid().replace(b"MSB_INTEGER", b"IEEE_REAL "),
    "bands-zero": lambda: build_pds3(make_pixels(4, 4), bands=0).data,
    "no-image-pointer": lambda: _valid().replace(b"^IMAGE   ", b"^IMAGX   "),
    "pointer-beyond-file": lambda: _valid().replace(
        b"^IMAGE                            = 3       ",
        b"^IMAGE                            = 99999   ",
    ),
}


@pytest.mark.parametrize("make", BROKEN.values(), ids=BROKEN.keys())
def test_parse_broken_input_raises_pds3_error(make) -> None:
    data = make()

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)


def test_pds3_error_is_a_value_error() -> None:
    assert issubclass(Pds3Error, ValueError)


def test_parse_input_over_64_mib_raises_pds3_error() -> None:
    data = b"PDS_VERSION_ID                    = PDS3\r\n" + bytes(64 * 1024 * 1024)

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)


def test_parse_declared_size_beyond_limit_does_not_allocate_memory() -> None:
    """A tiny file declaring 10**9 x 10**9 samples must fail fast, not try to allocate."""
    data = build_pds3(make_pixels(4, 4), lines=10**9, samples=10**9).data

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)


# --- hostile labels and the tightened limits (stage 9a review) --------------------------


def _timed_error(data: bytes) -> float:
    started = time.perf_counter()
    with pytest.raises(Pds3Error):
        parse_pds3_image(data)
    return time.perf_counter() - started


CRLF = bytes((13, 10))
HEAD = b"PDS_VERSION_ID = PDS3" + CRLF
END = b"END" + CRLF
HOSTILE = {
    "unclosed-quote-400kb": lambda: b'PDS_VERSION_ID = PDS3\r\nNOTE = "' + b"abc def\r\n" * 45_000,
    "quote-on-every-line": lambda: b"PDS_VERSION_ID = PDS3\r\n" + b'K = "\r\n' * 100_000,
    "many-short-lines-no-end": lambda: b"PDS_VERSION_ID = PDS3\r\n" + b"K = 1\r\n" * 100_000,
    "nested-objects-no-end": lambda: b"PDS_VERSION_ID = PDS3\r\n" + b"OBJECT = X\r\n" * 50_000,
    "unclosed-comment": lambda: b"PDS_VERSION_ID = PDS3\r\n/* " + b"note\r\n" * 80_000,
    "end-after-comment-openers-200kb": lambda: HEAD + b"X = " + b"/*a" * 66_000 + CRLF + END,
    "end-after-unclosed-comment": lambda: HEAD + b"/* " + (b"a /* b" + CRLF) * 20_000 + END,
    "end-after-quote-openers": lambda: HEAD + b"X = " + b'"a' * 100_000 + CRLF + END,
    "end-after-many-statements": lambda: HEAD + (b"K = (1," + CRLF) * 20_000 + END,
    "end-after-slash-star-lines": lambda: HEAD + (b"A = 1 /* x" + CRLF) * 15_000 + END,
    "one-giant-line": lambda: b"PDS_VERSION_ID = PDS3\r\nK = " + b"7" * 600_000,
}


@pytest.mark.parametrize("make", HOSTILE.values(), ids=HOSTILE.keys())
def test_parse_pathological_label_fails_fast_with_pds3_error(make) -> None:
    data = make()

    assert _timed_error(data) < 5.0  # generous: a quadratic parser takes minutes


@pytest.mark.parametrize(
    "key", ["LINES", "LINE_SAMPLES", "SAMPLE_BITS", "BANDS", "RECORD_BYTES", "^IMAGE"]
)
def test_parse_number_of_5000_digits_is_a_pds3_error_not_a_bare_value_error(key: str) -> None:
    data = build_pds3(make_pixels(4, 4)).data
    pattern = re.compile(rb"(?m)^(\s*" + re.escape(key.encode()) + rb"\s*=\s*)\d+")
    assert pattern.search(data), key
    data = pattern.sub(lambda m: m.group(1) + b"9" * 5000, data, count=1)

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)


def test_parse_input_over_8_mib_raises_pds3_error() -> None:
    data = b"PDS_VERSION_ID                    = PDS3\r\n" + bytes(9 * 1024 * 1024)

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)


def test_parse_valid_file_of_about_4_mib_within_the_pixel_limit_is_accepted() -> None:
    pixels = make_pixels(2048, 2048, "u8")

    image = parse_pds3_image(build_pds3(pixels, fmt="u8").data)

    assert image.pixels.shape == (2048, 2048)
    assert int(image.pixels[0, 0]) == int(pixels[0, 0])


@pytest.mark.parametrize(
    ("lines", "samples"), [(2048, 2049), (2049, 2048), (4096, 1025)], ids=lambda v: str(v)
)
def test_parse_more_than_2048_squared_pixels_raises_pds3_error(lines: int, samples: int) -> None:
    data = build_pds3(make_pixels(lines, samples, "u8"), fmt="u8").data

    with pytest.raises(Pds3Error):
        parse_pds3_image(data)
