"""Synthetic PDS3 image files for the stage-9a tests, plus the real MER samples.

``build_pds3`` writes a fixed-length-record PDS3 file the way the MER EDR products are laid
out (CRLF label, ``END``, padding to a whole number of records, image after the label) with
the variations the parser must support: sample width (8/16 bit), byte order, line prefix and
suffix bytes, several bands, and the three forms of the ``^IMAGE`` pointer (record number,
``("file", record)``, ``<BYTES>``). ``realistic=True`` adds what the real labels contain:
comments, multi-line quoted strings, nested ``GROUP``s with their own ``LINES`` /
``LINE_SAMPLES`` before the ``IMAGE`` object, and a VICAR header between label and image
(``^IMAGE_HEADER`` precedes ``^IMAGE``).

The pointer follows the PDS3 standard: record numbers and ``<BYTES>`` offsets count from 1.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MER_IMG_FIXTURES = ROOT / "tests" / "fixtures" / "upstream" / "mer" / "img"
MER_IMG_LOCAL = ROOT / "data" / "mer-img"  # big samples, git-ignored (manual checks)

CRLF = "\r\n"
PREFIX_BYTE = 0xA5
SUFFIX_BYTE = 0x5A

# (SAMPLE_BITS, SAMPLE_TYPE, numpy dtype, maximum value that is not ambiguous by sign)
SAMPLE_FORMATS = {
    "u8": (8, "UNSIGNED_INTEGER", np.dtype("u1"), 255),
    "u8-msb-unsigned": (8, "MSB_UNSIGNED_INTEGER", np.dtype("u1"), 255),
    "u8-lsb-unsigned": (8, "LSB_UNSIGNED_INTEGER", np.dtype("u1"), 255),
    "i16-msb": (16, "MSB_INTEGER", np.dtype(">u2"), 4095),
    "i16-lsb": (16, "LSB_INTEGER", np.dtype("<u2"), 4095),
    "u16-msb": (16, "MSB_UNSIGNED_INTEGER", np.dtype(">u2"), 65535),
    "u16-lsb": (16, "LSB_UNSIGNED_INTEGER", np.dtype("<u2"), 65535),
}


def make_pixels(lines: int, samples: int, fmt: str = "i16-msb", seed: int = 7) -> np.ndarray:
    """Deterministic pixel values that fit ``fmt`` (also with the unsigned top range)."""
    maximum = SAMPLE_FORMATS[fmt][3]
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, maximum + 1, size=(lines, samples), dtype=np.int64)
    pixels[0, 0] = maximum  # pin the extremes so a wrong width or order cannot hide
    pixels[-1, -1] = 0
    return pixels


@dataclass
class Built:
    data: bytes
    label_records: int
    image_offset: int  # 0-based byte offset of the first image byte


def _label(
    *,
    lines: int,
    samples: int,
    bits: int,
    sample_type: str,
    pointer: str,
    record_bytes: int,
    label_records: int,
    file_records: int,
    header_records: int,
    prefix: int,
    suffix: int,
    bands: int,
    realistic: bool,
    extra: str,
) -> str:
    first = label_records + 1  # 1-based record number of the first record after the label
    if header_records:
        image_record = first + header_records
        header_pointer = f"^IMAGE_HEADER                     = {first:<8}"
    else:
        image_record = first
        header_pointer = ""
    if pointer == "record":
        image_pointer = f"{image_record:<8}"
    elif pointer == "tuple":
        image_pointer = f'("TEST.IMG", {image_record})'
    elif pointer == "bytes":
        image_pointer = f"{(image_record - 1) * record_bytes + 1:<8} <BYTES>"
    else:
        raise ValueError(pointer)

    rows = ["PDS_VERSION_ID                    = PDS3", ""]
    if realistic:
        rows += ["/* FILE DATA ELEMENTS */", ""]
    rows += [
        "RECORD_TYPE                       = FIXED_LENGTH",
        f"RECORD_BYTES                      = {record_bytes}",
        f"FILE_RECORDS                      = {file_records:<8}",
        f"LABEL_RECORDS                     = {label_records:<8}",
        "",
    ]
    if realistic:
        rows += ["/* POINTERS TO DATA OBJECTS */", ""]
    if header_pointer:
        rows.append(header_pointer)
    rows += [f"^IMAGE                            = {image_pointer}", ""]
    if realistic:
        rows += [
            ' DATA_SET_NAME                    = "MER 2 MARS NAVIGATION CAMERA EDR OPS ',
            '                                     VERSION 1.0"',
            "GROUP                             = COMPRESSION_PARAMETERS",
            "  LINES                           = 64",
            "  LINE_SAMPLES                    = 64",
            "  FIRST_LINE                      = 1",
            '  INST_CMPRS_NAME                 = "ICER ADAPTIVE VARIABLE-LENGTH CODING ',
            '                                     (ICER)"',
            "END_GROUP                         = COMPRESSION_PARAMETERS",
            "GROUP                             = SUBFRAME_REQUEST_PARMS",
            "  LINES                           = 1024",
            "  LINE_SAMPLES                    = 1024",
            "END_GROUP                         = SUBFRAME_REQUEST_PARMS",
            "",
        ]
    rows += [
        "OBJECT                            = IMAGE",
        f"  LINES                           = {lines}",
        f"  LINE_SAMPLES                    = {samples}",
        f"  SAMPLE_TYPE                     = {sample_type}",
        f"  SAMPLE_BITS                     = {bits}",
        f"  BANDS                           = {bands}",
        "  BAND_STORAGE_TYPE               = BAND_SEQUENTIAL",
    ]
    if prefix:
        rows.append(f"  LINE_PREFIX_BYTES               = {prefix}")
    if suffix:
        rows.append(f"  LINE_SUFFIX_BYTES               = {suffix}")
    if realistic:
        rows += [
            "  MINIMUM                         = 0.0",
            "  MAXIMUM                         = 255.0",
        ]
    rows += ["END_OBJECT                        = IMAGE"]
    if header_records:
        rows += [
            "OBJECT                         = IMAGE_HEADER",
            ' ^DESCRIPTION                  = "VICAR2.TXT"',
            "END_OBJECT                     = IMAGE_HEADER",
        ]
    if extra:
        rows.append(extra)
    rows += ["END", ""]
    return CRLF.join(rows)


def build_pds3(
    pixels: np.ndarray,
    *,
    fmt: str = "i16-msb",
    pointer: str = "record",
    record_bytes: int = 512,
    prefix: int = 0,
    suffix: int = 0,
    bands: int = 1,
    realistic: bool = False,
    extra_label: str = "",
    lines: int | None = None,
    samples: int | None = None,
) -> Built:
    """Build one PDS3 file whose first band is ``pixels``.

    Other bands (when ``bands > 1``) hold ``maximum - pixels`` so that reading a wrong band
    shows up. ``lines`` / ``samples`` override the values written to the label only (for
    broken-input tests).
    """
    bits, sample_type, dtype, maximum = SAMPLE_FORMATS[fmt]
    n_lines, n_samples = pixels.shape
    prefix_bytes = bytes([PREFIX_BYTE]) * prefix
    suffix_bytes = bytes([SUFFIX_BYTE]) * suffix

    def band_bytes(values: np.ndarray) -> bytes:
        rows = values.astype(dtype)
        return b"".join(prefix_bytes + rows[i].tobytes() + suffix_bytes for i in range(n_lines))

    body = band_bytes(pixels)
    for band in range(1, bands):
        body += band_bytes((maximum - pixels) if band % 2 else pixels)

    header_records = 2 if realistic else 0
    header = b""
    if header_records:
        header = b"LBLSIZE=12800           FORMAT='HALF'  TYPE='IMAGE'"
        header = header.ljust(header_records * record_bytes, b" ")

    label_records = 1
    while True:
        file_records = label_records + header_records + -(-len(body) // record_bytes)
        label = _label(
            lines=n_lines if lines is None else lines,
            samples=n_samples if samples is None else samples,
            bits=bits,
            sample_type=sample_type,
            pointer=pointer,
            record_bytes=record_bytes,
            label_records=label_records,
            file_records=file_records,
            header_records=header_records,
            prefix=prefix,
            suffix=suffix,
            bands=bands,
            realistic=realistic,
            extra=extra_label,
        )
        needed = -(-len(label) // record_bytes)
        if needed == label_records:
            break
        label_records = needed
    padded = label.encode("ascii").ljust(label_records * record_bytes, b" ")
    data = padded + header + body
    return Built(data=data, label_records=label_records, image_offset=len(padded) + len(header))


def real_samples() -> list[Path]:
    """Real MER .img files: the committed small ones plus the local big ones, if present."""
    found = sorted(MER_IMG_FIXTURES.glob("*.img"))
    names = {path.name for path in found}
    if MER_IMG_LOCAL.is_dir():
        found += sorted(p for p in MER_IMG_LOCAL.glob("*.img") if p.name not in names)
    return found


def reference_pixels(data: bytes, lines: int, samples: int) -> np.ndarray:
    """Independent reader for the real MER samples: 16-bit MSB, no prefix, ^IMAGE in records."""
    import re

    text = data[:200_000].decode("latin-1")
    record_bytes = int(re.search(r"^RECORD_BYTES\s*=\s*(\d+)", text, re.MULTILINE).group(1))
    record = int(re.search(r"^\^IMAGE\s*=\s*(\d+)", text, re.MULTILINE).group(1))
    offset = (record - 1) * record_bytes
    raw = data[offset : offset + lines * samples * 2]
    return np.frombuffer(raw, dtype=">u2").reshape(lines, samples).astype(np.int64)
