"""Reader of PDS3 image products (the Spirit/Opportunity ``.img`` files).

A product is an ASCII label (``KEY = VALUE`` lines ending with ``END``) followed by the raster.
Only what the MER EDR products need is supported: one uncompressed ``IMAGE`` object of 8- or
16-bit integers (the first band if there are several). Anything else raises ``Pds3Error``.
The format of upstream files is known to ``app/sources/`` only.
"""

import re
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

# Protection against pathological inputs: MER EDRs are at most ~2 MB (1024 x 1024 x 16 bit).
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 2048 * 2048
MAX_BANDS = 64
MAX_AFFIX_BYTES = 4096
# The label is searched for its END statement in this prefix of the file.
MAX_LABEL_BYTES = 256 * 1024
# One KEY = VALUE statement (multi-line values included) is at most this long.
MAX_STATEMENT_CHARS = 64 * 1024

_END_RE = re.compile(rb"^[ \t]*END[ \t]*\r?$", re.MULTILINE)
_INT_RE = re.compile(r"[+-]?\d{1,12}")
_STORAGE_SEQUENTIAL = "BAND_SEQUENTIAL"

_SIGNED_TYPES = frozenset({"INTEGER", "MSB_INTEGER", "LSB_INTEGER"})
_UNSIGNED_TYPES = frozenset({"UNSIGNED_INTEGER", "MSB_UNSIGNED_INTEGER", "LSB_UNSIGNED_INTEGER"})
_LITTLE_ENDIAN_TYPES = frozenset({"LSB_INTEGER", "LSB_UNSIGNED_INTEGER"})


class Pds3Error(ValueError):
    """The data is not a PDS3 image this reader understands."""


@dataclass(frozen=True)
class Pds3Image:
    lines: int
    samples: int
    sample_bits: int
    pixels: NDArray[np.number]  # 2D (lines, samples), values as stored


def parse_pds3_image(data: bytes) -> Pds3Image:
    """Parse a PDS3 product with an ``IMAGE`` object into a 2D pixel array."""
    if len(data) > MAX_INPUT_BYTES:
        raise Pds3Error(f"File is too large ({len(data)} bytes)")
    label, root = _parse_label(data)
    image = label.get("IMAGE")
    if image is None:
        raise Pds3Error("Label has no IMAGE object")
    lines = _dimension(image, "LINES")
    samples = _dimension(image, "LINE_SAMPLES")
    bits = _int_value(image, "SAMPLE_BITS")
    if bits not in (8, 16):
        raise Pds3Error(f"Unsupported SAMPLE_BITS {bits}")
    dtype = _dtype(image.get("SAMPLE_TYPE"), bits)
    bands = _int_value(image, "BANDS", default=1)
    if not 1 <= bands <= MAX_BANDS:
        raise Pds3Error(f"Unsupported BANDS {bands}")
    if bands > 1 and image.get("BAND_STORAGE_TYPE", "").upper() != _STORAGE_SEQUENTIAL:
        raise Pds3Error("Multi-band image must use BAND_SEQUENTIAL storage")
    if lines * samples > MAX_PIXELS:
        raise Pds3Error(f"Image is too large ({lines} x {samples} samples)")
    prefix = _int_value(image, "LINE_PREFIX_BYTES", default=0)
    suffix = _int_value(image, "LINE_SUFFIX_BYTES", default=0)
    if not (0 <= prefix <= MAX_AFFIX_BYTES and 0 <= suffix <= MAX_AFFIX_BYTES):
        raise Pds3Error("Unsupported LINE_PREFIX_BYTES / LINE_SUFFIX_BYTES")
    offset = _image_offset(root)
    pixels = _read_raster(data, offset, lines, samples, dtype, prefix, suffix)
    return Pds3Image(lines=lines, samples=samples, sample_bits=bits, pixels=pixels)


def _parse_label(data: bytes) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Objects of the label by name (root statements under ``""``) and the root statements."""
    match = _END_RE.search(data[:MAX_LABEL_BYTES])
    if match is None:
        raise Pds3Error("No PDS3 label: END statement not found")
    text = data[: match.start()].decode("latin-1")
    objects: dict[str, dict[str, str]] = {"": {}}
    stack: list[str] = [""]
    for statement in _statements(text):
        key, _, value = statement.partition("=")
        key, value = key.strip().upper(), value.strip()
        if key == "OBJECT":
            stack.append(value.upper())
            objects.setdefault(stack[-1], {})
        elif key == "END_OBJECT":
            if len(stack) > 1:
                stack.pop()
        elif key:
            objects[stack[-1]].setdefault(key, value)
    if "RECORD_TYPE" not in objects[""] and "^IMAGE" not in objects[""]:
        raise Pds3Error("Not a PDS3 label")
    return objects, objects[""]


def _statements(text: str) -> list[str]:
    """``KEY = VALUE`` statements; values may span lines (quotes, parentheses, braces).

    Linear in the label size: the open-bracket state is tracked per added line.
    """
    result: list[str] = []
    parts: list[str] = []
    size = 0
    quotes = 0
    depth = 0
    for raw in text.splitlines():
        if len(raw) > MAX_STATEMENT_CHARS:
            raise Pds3Error("Label line is too long")
        line = _strip_comments(raw).strip()
        if not line and not parts:
            continue
        parts.append(line)
        size += len(line) + 1
        if size > MAX_STATEMENT_CHARS:
            raise Pds3Error("Label statement is too long")
        quotes += line.count('"')
        depth += line.count("(") + line.count("{") - line.count(")") - line.count("}")
        if quotes % 2 == 1 or depth > 0:
            continue
        statement = " ".join(parts).strip()
        if "=" in statement:
            result.append(statement)
        parts, size, quotes, depth = [], 0, 0, 0
    return result


def _strip_comments(line: str) -> str:
    """The line without ``/* ... */`` comments, in one linear pass.

    An unclosed ``/*`` is a ``Pds3Error`` (a comment spanning lines is not valid in labels
    we read).
    """
    start = line.find("/*")
    if start < 0:
        return line
    parts: list[str] = []
    pos = 0
    while start >= 0:
        end = line.find("*/", start + 2)
        if end < 0:
            raise Pds3Error("Unclosed comment in label")
        parts.append(line[pos:start])
        pos = end + 2
        start = line.find("/*", pos)
    parts.append(line[pos:])
    return "".join(parts)


def _int_value(section: dict[str, str], key: str, *, default: int | None = None) -> int:
    raw = section.get(key)
    if raw is None:
        if default is None:
            raise Pds3Error(f"IMAGE object has no {key}")
        return default
    if _INT_RE.fullmatch(raw) is None:
        raise Pds3Error(f"{key} is not an integer: {raw[:32]!r}")
    return int(raw)


def _dimension(section: dict[str, str], key: str) -> int:
    value = _int_value(section, key)
    if not 1 <= value <= MAX_DIMENSION:
        raise Pds3Error(f"{key} {value} is out of range 1..{MAX_DIMENSION}")
    return value


def _dtype(sample_type: str | None, bits: int) -> np.dtype:
    kind = (sample_type or "").strip('"').upper()
    if kind not in _SIGNED_TYPES | _UNSIGNED_TYPES:
        raise Pds3Error(f"Unsupported SAMPLE_TYPE {kind[:32]!r}")
    signed = kind in _SIGNED_TYPES
    order = "<" if kind in _LITTLE_ENDIAN_TYPES else ">"
    return np.dtype(f"{order}{'i' if signed else 'u'}{bits // 8}")


def _image_offset(root: dict[str, str]) -> int:
    """Byte offset of the raster from the ``^IMAGE`` pointer."""
    pointer = root.get("^IMAGE")
    if pointer is None:
        raise Pds3Error("Label has no ^IMAGE pointer")
    numbers = _INT_RE.findall(re.sub(r'"[^"]*"', "", pointer))
    if not numbers:
        raise Pds3Error("^IMAGE pointer has no position")
    position = int(numbers[0])
    if position < 1:
        raise Pds3Error("^IMAGE position must be at least 1")
    if "<BYTES>" in pointer.upper():
        return position - 1
    record_bytes = _int_value(root, "RECORD_BYTES")
    if record_bytes < 1:
        raise Pds3Error("RECORD_BYTES must be positive")
    return (position - 1) * record_bytes


def _read_raster(
    data: bytes,
    offset: int,
    lines: int,
    samples: int,
    dtype: np.dtype,
    prefix: int,
    suffix: int,
) -> NDArray[np.number]:
    row_bytes = samples * dtype.itemsize
    stride = prefix + row_bytes + suffix
    needed = lines * stride - suffix
    if len(data) < offset + needed:
        raise Pds3Error(
            f"Raster is truncated: {len(data) - offset} bytes after the label, {needed} needed"
        )
    raw = np.frombuffer(data, dtype=np.uint8, count=lines * stride - suffix, offset=offset)
    if suffix:
        raw = np.concatenate([raw, np.zeros(suffix, dtype=np.uint8)])
    rows = raw.reshape(lines, stride)[:, prefix : prefix + row_bytes]
    pixels = np.ascontiguousarray(rows).view(dtype).reshape(lines, samples)
    return pixels.astype(dtype.newbyteorder("="), copy=True)
