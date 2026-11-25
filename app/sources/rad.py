"""MSL RAD radiation files of the PDS PPI volume MSL-M-RAD-3-RDR-V1.0 (stage 21).

The only place that knows their format (docs/DATA-SOURCES.md, "RAD RDR"):

- ``CHECKSUMS.TXT``: ``md5  ./path`` per file of the volume; ``parse_checksums`` keeps the daily
  surface files ``DATA/SOL_AAAAA_BBBBB/RAD_RDR_YYYY_DDD_HH_MM_SSSS_V00.TXT``.
- ``TIMING_ERRORS.TXT``: blocks with ``Found in File: <name>``; ``parse_timing_errors``.
- a daily ``.TXT`` (12-72 MB, 97 % of it PHA spectra): ``RadParser`` reads it as a stream of
  chunks, line by line, and keeps only the dosimetry numbers of every observation; it also
  computes the md5 and the last bytes of the whole body on the fly. Nothing is written to disk.

Observations are returned as found (``None`` for what is missing); whether one is usable is
decided by the service (``combine_sol``).
"""

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto

logger = logging.getLogger(__name__)

TAIL_BYTES = 64 * 1024
DEFAULT_MAX_LINE_BYTES = 64 * 1024
# Real files have hundreds of blocks; this only stops a malformed or hostile file.
DEFAULT_MAX_BLOCKS = 5000
# ``entry`` of the COUNTER_L2 row with the logic ``BU`` (all counts of detector B).
BU_ENTRY = 4
BU_LOGIC = "BU"
# Name of the COUNTER_L1 row of detector B with its live time in ``duration``.
BU_CHANNEL = "BU-x8"

_PATH_RE = re.compile(r"DATA/SOL_\d{5}_\d{5}/RAD_RDR_\d{4}_\d{3}_\d{2}_\d{2}_(\d{4})_V\d{2}\.TXT")
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
_TIMING_FILE_RE = re.compile(r"Found in File:\s*(\S+)")
_HEADER_RE = re.compile(r"\[([A-Za-z0-9_]+)(?::\s*(\d+))?\]")
_L1_RE = re.compile(r'\s*\d+\s+"([^"]*)"\s+(-?\d+)\s+(-?\d+)\s+(-?\d+(?:\.\d+)?)\s')
_L2_RE = re.compile(r'\s*(\d+)\s+(-?\d+)\s+(-?\d+)\s.*"([^"]*)"\s*')
_QC_RE = re.compile(r"[01]{8}")
_TIME_FRACTION_RE = re.compile(r"\.\d+$")


class RadFormatError(Exception):
    """The file does not look like a RAD daily file (or breaks a size limit of the parser)."""


@dataclass(frozen=True)
class ChecksumEntry:
    path: str
    md5: str
    sol: int


@dataclass(frozen=True)
class RadObservation:
    """One observation with dosimetry (a block of the file that has a dose section).

    ``dose_*`` are in uGy/h; a dose section with text that is not a number gives ``nan``, a
    missing or empty one ``None``. ``live_s`` is ``duration`` of the ``BU-x8`` row of
    ``COUNTER_L1``; ``bu_count`` / ``bu_read`` are ``count`` / ``read`` of the ``COUNTER_L2``
    row ``entry`` 4 (``None`` when the row is missing or its logic is not ``BU``).
    """

    index: int
    start_utc_raw: str | None
    start_utc: datetime | None
    qc_raw: str | None
    dose_b: float | None
    dose_e: float | None
    live_s: float | None
    bu_count: int | None
    bu_read: int | None
    # ``OBS_DURATION`` in seconds; ``None`` in the early format (2012) and when unreadable.
    obs_duration_s: float | None = None

    @property
    def qc(self) -> int | None:
        return parse_qc(self.qc_raw)


@dataclass(frozen=True)
class RadFile:
    """A parsed daily file: header, dose observations and what was read from the wire."""

    start_sol: int
    start_time: datetime | None
    creation_time: datetime | None
    rdr_version: int | None
    md5: str
    size: int
    tail: bytes
    blocks: int
    observations: list[RadObservation]


def parse_qc(raw: str | None) -> int | None:
    """``QC`` (8 characters 0/1, quoted or not) as an integer; ``None`` if it is anything else.

    Bit 0 (the rightmost character) means "possible quality problems", bit 2 DAN mode,
    bit 3 Solar Event.
    """
    if raw is None:
        return None
    value = raw.strip().strip('"').strip()
    return int(value, 2) if _QC_RE.fullmatch(value) else None


def parse_pds_time(value: str | None) -> datetime | None:
    """``YYYY-DDD HH:MM:SS`` (day of year, UTC) as an aware datetime; ``None`` if malformed."""
    if value is None:
        return None
    text = _TIME_FRACTION_RE.sub("", value.strip().strip('"').strip())
    try:
        return datetime.strptime(text, "%Y-%j %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def file_name(path: str) -> str:
    """The last component of a volume path."""
    return path.rsplit("/", 1)[-1]


def parse_checksums(text: str) -> list[ChecksumEntry]:
    """The daily surface files of ``CHECKSUMS.TXT`` in file order, paths without ``./``.

    Other files of the volume (``.LBL``, ``.FMT``, cruise, documents) are skipped silently,
    a line that is not ``md5  path`` at all with a warning.
    """
    entries: list[ChecksumEntry] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or _MD5_RE.fullmatch(parts[0]) is None:
            logger.warning("CHECKSUMS.TXT line %d is malformed, skipped", number)
            continue
        path = parts[1].strip().removeprefix("./")
        found = _PATH_RE.fullmatch(path)
        if found is not None:
            entries.append(ChecksumEntry(path, parts[0].lower(), int(found.group(1))))
    return entries


def parse_timing_errors(text: str) -> set[str]:
    """File names listed after ``Found in File:`` (``Timing Error`` and ``Date Range Error``)."""
    return {match.group(1) for match in _TIMING_FILE_RE.finditer(text)}


class _Mode(Enum):
    SKIP = auto()
    FILE = auto()
    OBSERVATION = auto()
    COUNTER_L1 = auto()
    COUNTER_L2 = auto()
    DOSE_B = auto()
    DOSE_E = auto()


_SECTION_MODES = {
    "FILE": _Mode.FILE,
    "OBSERVATION": _Mode.OBSERVATION,
    "COUNTER_L1": _Mode.COUNTER_L1,
    "COUNTER_L2": _Mode.COUNTER_L2,
    "DOSIMETRY_TOTAL_DOSE_B": _Mode.DOSE_B,
    "DOSIMETRY_TOTAL_DOSE_E": _Mode.DOSE_E,
}


class _Block:
    """The values of one ``[OBSERVATION]`` block while it is being read."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.start_utc_raw: str | None = None
        self.qc_raw: str | None = None
        self.seen_b = False
        self.seen_e = False
        self.dose_b: float | None = None
        self.dose_e: float | None = None
        self.live_s: float | None = None
        self.bu_count: int | None = None
        self.bu_read: int | None = None
        self.obs_duration_s: float | None = None

    def observation(self) -> RadObservation:
        return RadObservation(
            index=self.index,
            start_utc_raw=self.start_utc_raw,
            start_utc=parse_pds_time(self.start_utc_raw),
            qc_raw=self.qc_raw,
            dose_b=self.dose_b,
            dose_e=self.dose_e,
            live_s=self.live_s,
            bu_count=self.bu_count,
            bu_read=self.bu_read,
            obs_duration_s=self.obs_duration_s,
        )


def _key_value(text: str) -> tuple[str, str] | None:
    key, sep, value = text.partition("=")
    if not sep:
        return None
    return key.strip(), value.strip().strip('"').strip()


def _number(text: str) -> float:
    """A dose value; text that is not a number becomes ``nan`` (judged by the service)."""
    try:
        return float(text)
    except ValueError:
        return math.nan


MAX_OBS_DURATION_S = 14400.0


def _duration(text: str) -> float | None:
    """``OBS_DURATION``: a finite number of seconds in (0, ``MAX_OBS_DURATION_S``], else ``None``
    (the caller falls back)."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and 0 < value <= MAX_OBS_DURATION_S else None


class RadParser:
    """Chunk sink (``feed`` / ``result``) that parses a RAD daily file as it arrives.

    Memory: one line of at most ``max_line_bytes``, the last ``TAIL_BYTES`` of the body and one
    small object per dose observation. Lines end with ``\\n`` (a ``\\r`` before it is dropped);
    chunk borders may fall anywhere.
    """

    def __init__(
        self, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES, max_blocks: int = DEFAULT_MAX_BLOCKS
    ) -> None:
        self._max_line = max_line_bytes
        self._max_blocks = max_blocks
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._size = 0
        self._tail = bytearray()
        self._pending = b""
        self._mode = _Mode.SKIP
        self._header: dict[str, str] = {}
        self._block: _Block | None = None
        self._observations: list[RadObservation] = []
        self._blocks = 0

    def feed(self, chunk: bytes) -> None:
        self._md5.update(chunk)
        self._size += len(chunk)
        self._tail += chunk
        if len(self._tail) > 2 * TAIL_BYTES:
            del self._tail[:-TAIL_BYTES]
        lines = (self._pending + chunk).split(b"\n")
        self._pending = lines.pop()
        if len(self._pending) > self._max_line:
            raise RadFormatError(f"line longer than {self._max_line} bytes")
        for line in lines:
            self._line(line)

    def result(self) -> RadFile:
        if self._pending:
            self._line(self._pending)
            self._pending = b""
        self._close_block()
        try:
            start_sol = int(self._header["START_SOL"])
        except (KeyError, ValueError):
            raise RadFormatError("START_SOL is missing or not a number") from None
        rdr = self._header.get("RDR_VERSION", "")
        return RadFile(
            start_sol=start_sol,
            start_time=parse_pds_time(self._header.get("START_TIME")),
            creation_time=parse_pds_time(self._header.get("CREATION_TIME")),
            rdr_version=int(rdr) if rdr.isdigit() else None,
            md5=self._md5.hexdigest(),
            size=self._size,
            tail=bytes(self._tail[-TAIL_BYTES:]),
            blocks=self._blocks,
            observations=self._observations,
        )

    def _line(self, line: bytes) -> None:
        if len(line) > self._max_line:
            raise RadFormatError(f"line longer than {self._max_line} bytes")
        if line[:1] == b"[":
            self._section(line.decode("ascii", "replace").strip())
            return
        if self._mode is _Mode.SKIP:
            return
        text = line.decode("ascii", "replace").strip()
        if text:
            self._content(text)

    def _section(self, text: str) -> None:
        match = _HEADER_RE.fullmatch(text)
        name = match.group(1) if match else ""
        if name == "OBSERVATION":
            self._close_block()
            self._blocks += 1
            if self._blocks > self._max_blocks:
                raise RadFormatError(f"more than {self._max_blocks} observation blocks")
            self._block = _Block(int(match.group(2) or 0) if match else 0)
        elif name == "FILE":
            self._close_block()
        mode = _SECTION_MODES.get(name, _Mode.SKIP)
        if self._block is None and mode not in (_Mode.FILE, _Mode.OBSERVATION):
            mode = _Mode.SKIP
        self._mode = mode
        if self._block is not None:
            self._mark_dose_section(mode)

    def _mark_dose_section(self, mode: _Mode) -> None:
        if self._block is None:
            return
        if mode is _Mode.DOSE_B:
            self._block.seen_b = True
        elif mode is _Mode.DOSE_E:
            self._block.seen_e = True

    def _close_block(self) -> None:
        block, self._block = self._block, None
        if block is not None and (block.seen_b or block.seen_e):
            self._observations.append(block.observation())

    def _content(self, text: str) -> None:
        mode = self._mode
        if mode is _Mode.FILE:
            self._file_line(text)
        elif self._block is None:
            return
        elif mode is _Mode.OBSERVATION:
            self._observation_line(self._block, text)
        elif mode is _Mode.COUNTER_L1:
            self._counter_l1(self._block, text)
        elif mode is _Mode.COUNTER_L2:
            self._counter_l2(self._block, text)
        elif mode is _Mode.DOSE_B and self._block.dose_b is None:
            self._block.dose_b = _number(text)
        elif mode is _Mode.DOSE_E and self._block.dose_e is None:
            self._block.dose_e = _number(text)

    def _file_line(self, text: str) -> None:
        pair = _key_value(text)
        if pair is not None and pair[0] in (
            "START_SOL",
            "START_TIME",
            "CREATION_TIME",
            "RDR_VERSION",
        ):
            self._header[pair[0]] = pair[1]

    @staticmethod
    def _observation_line(block: _Block, text: str) -> None:
        pair = _key_value(text)
        if pair is None:
            return
        if pair[0] == "START_OBS_UTC":
            block.start_utc_raw = pair[1]
        elif pair[0] == "QC":
            block.qc_raw = pair[1]
        elif pair[0] == "OBS_DURATION":
            block.obs_duration_s = _duration(pair[1])

    @staticmethod
    def _counter_l1(block: _Block, text: str) -> None:
        if BU_CHANNEL not in text or block.live_s is not None:
            return
        match = _L1_RE.match(text)
        if match is not None and match.group(1).strip() == BU_CHANNEL:
            block.live_s = float(match.group(4))

    @staticmethod
    def _counter_l2(block: _Block, text: str) -> None:
        parts = text.split(None, 1)
        if not parts or not parts[0].isdigit() or int(parts[0]) != BU_ENTRY:
            return
        match = _L2_RE.fullmatch(text)
        if match is None or match.group(4).strip() != BU_LOGIC:
            return
        block.bu_count, block.bu_read = int(match.group(2)), int(match.group(3))
        if block.bu_count != block.bu_read:
            logger.warning(
                "RAD observation %d: BU count %d differs from read %d",
                block.index,
                block.bu_count,
                block.bu_read,
            )
