"""Helpers for the stage-21 MSL RAD tests: synthetic RDR files, a fake PDS host, DB helpers.

Everything is written from docs/proposals/stage-21-rad.md and the formats described in
docs/DATA-SOURCES.md; nothing here imports the implementation except the CLI entry point.

- `fixture(name)`: bytes of a trimmed real file from tests/fixtures/upstream/rad/.
- `obs_block` / `rad_file`: synthetic observation blocks / whole files in the real layout
  (CRLF, `[FILE]`, `[OBSERVATION: NN]`, `COUNTER_L1` with `BU-x8`, `COUNTER_L2` entry 4,
  `DOSIMETRY_TOTAL_DOSE_B/E`).
- `RadHost`: a respx handler that behaves like the PDS Apache host: conditional GETs for the
  two lists, `Range` (206 / 200 fallback), failures, truncation, resets, redirects; it logs
  every request and the order of requests and (fake) sleeps.
- DB helpers read `rad_files`, `rad_daily`, `rad_dataset` with plain SQL.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "upstream" / "rad"
HOST = "pds-ppi.igpp.ucla.edu"
VOLUME = "MSL-M-RAD-3-RDR-V1.0"
PREFIX = f"/data/{VOLUME}/"
BASE_URL = f"https://{HOST}{PREFIX}"
CHECKSUMS = "CHECKSUMS.TXT"
TIMING = "DOCUMENT/TIMING_ERRORS.TXT"
LOCK_KEY = 7_250_004
SOL_SECONDS = 88_775.244
CREDIT_RAD = "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0"

Factory = async_sessionmaker[AsyncSession]

F_SOL10 = "RAD_RDR_2012_228_20_41_0010_V00.TXT"
F_4843 = "RAD_RDR_2026_080_17_18_4843_V00.TXT"
F_4842 = "RAD_RDR_2026_079_17_02_4842_V00.TXT"
F_1649_A = "RAD_RDR_2017_085_15_54_1649_V00.TXT"  # earlier name, earlier CREATION_TIME
F_1649_B = "RAD_RDR_2017_085_16_28_1649_V00.TXT"  # the later one: the primary file of the sol
FOLDER = {
    10: "SOL_00000_00089",
    1649: "SOL_01649_01772",
    4842: "SOL_04710_04843",
    4843: "SOL_04710_04843",
}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def sol_of(name: str) -> int:
    match = re.search(r"_(\d{4})_V00\.TXT$", name)
    assert match, name
    return int(match.group(1))


def rel_path(name: str) -> str:
    """Path of a fixture file in the volume (as in CHECKSUMS.TXT without `./`)."""
    return f"DATA/{FOLDER[sol_of(name)]}/{name}"


def link_of(path: str) -> str:
    return BASE_URL + path


# --- synthetic files -----------------------------------------------------------------------------

_L1_HEAD = (
    " ch     name       fast       slow   duration  alive %   fast_DAC   slow_DAC fast_actual "
    "slow_actual input_gain fast_shaper slow_shaper"
)
_L2_HEAD = (
    " entry      count       read                                inspect"
    "                                require                                readout pri too"
    "                                    logic"
)
_BITS = '"000000000000000000000000100000000000"'


def obs_block(
    index: int,
    *,
    dose_b: float | str | None = 6.0,
    dose_e: float | str | None = 5.5,
    live: int | str | None = 900,
    count: int | str | None = 7200,
    read: int | str | None = None,
    qc: str | None = "00000000",
    quote_qc: bool = True,
    utc: str = "2026-080 17:18:00",
    logic: str | None = "BU",
    entry: int | None = 4,
    duration_line: bool = True,
    nl: str = "\r\n",
) -> str:
    """One `[OBSERVATION: NN]` block with COUNTER_L1/L2, dose sections and a tiny PHA.

    `None` for `dose_b` / `dose_e` omits the whole section, for `live` the BU row, for `count`
    and `entry` the entry-4 row, for `qc` the QC line. Strings are written as given (use them
    for `abc`, `-1`, ...). `read` defaults to `count`.
    """
    n = f"{index:02d}"
    lines = [
        f"[OBSERVATION: {n}]",
        f"EDR_FILENAME=RDB_{827380920 + index}EHP_4843_000_0000_M3.DAT",
        f'START_OBS_MARS="4843 0:{index % 60:02d}:24"',
        f'START_OBS_UTC="{utc}"',
    ]
    if duration_line:
        lines.append("OBS_DURATION=961")
    if qc is not None:
        lines.append(f'QC="{qc}"' if quote_qc else f"QC={qc}")
    lines += ["", f"[COUNTERS: {n}]", "", f"[COUNTER_L1: {n}]", "", _L1_HEAD, "-" * 135]
    lines.append('  1 "   N/A"         -2         -2         -2   -2.000         -2')
    if live is not None:
        lines.append(
            f' 24 " BU-x8"       6467       6454 {live!s:>10}   96.809        104         90'
        )
    lines.append('  25 "A2L-x1"         -1          0        930   96.809        255')
    lines += ["", f"[COUNTER_L2: {n}]", "", _L2_HEAD, "-" * 118]
    lines.append(f'     3          0          0 {_BITS} {_BITS} {_BITS}   1   0 "A2M&BM"')
    if entry is not None and count is not None:
        shown_read = count if read is None else read
        lines.append(
            f"{entry:6d} {count!s:>10} {shown_read!s:>10} {_BITS} {_BITS} {_BITS}   0   1 "
            f'"{logic if logic is not None else ""}"'
        )
    lines += ["", f"[DOSIMETRY: {n}]", "DETECTOR_B_THICKNESS=300.0", ""]
    if dose_b is not None:
        lines += [f"[DOSIMETRY_TOTAL_DOSE_B: {n}]", "", f"{dose_b}", ""]
    if dose_e is not None:
        lines += [f"[DOSIMETRY_TOTAL_DOSE_E: {n}]", "", f"{dose_e}", ""]
    lines += [f"[PHA: {n}]", "", "  1  2  3  4", ""]
    return nl.join(lines) + nl


def twin_block(index: int, nl: str = "\r\n") -> str:
    """The PHA-only twin of a dose block: no counters, no dose sections."""
    n = f"{index:02d}"
    return (
        nl.join(
            [
                f"[OBSERVATION: {n}]",
                "EDR_FILENAME=RDB_827380921ESD_4843_000_0000_M3.DAT",
                'START_OBS_UTC="2026-080 17:18:00"',
                'QC="00000000"',
                "",
                f"[PHA: {n}]",
                "",
                "  1  2  3  4",
                "",
            ]
        )
        + nl
    )


def rad_file(
    sol: int,
    blocks: list[str],
    *,
    start_time: str = "2026-080 17:18:00",
    creation_time: str = "2026-180 17:52:08",
    nl: str = "\r\n",
    start_sol_text: str | None = None,
    pad_to: int | None = None,
) -> bytes:
    """A whole RDR file; `pad_to` appends a PHA-like filler section up to that many bytes."""
    head = nl.join(
        [
            "[FILE]",
            "SOFTWARE_VERSION=1.0",
            "RDR_VERSION=0",
            f'CREATION_TIME="{creation_time}"',
            f"START_SOL={start_sol_text if start_sol_text is not None else sol}",
            f'START_TIME="{start_time}"',
            "",
            "",
        ]
    )
    data = (head + "".join(blocks)).encode("ascii")
    if pad_to is not None and len(data) < pad_to:
        row = ("  " + " ".join(["12345"] * 12) + nl).encode("ascii")
        filler = b"[PHA: 99]" + nl.encode() + nl.encode()
        need = pad_to - len(data) - len(filler)
        data += filler + row * (need // len(row)) + b"x" * (need % len(row))
    return data


def good_file(
    sol: int,
    *,
    n_obs: int = 3,
    dose_b: float = 6.0,
    dose_e: float = 5.5,
    day: int = 80,
    pad_to: int | None = None,
    creation_time: str = "2026-180 17:52:08",
) -> bytes:
    """A small valid file for `sol` with `n_obs` unremarkable observations."""
    utc = f"2026-{day:03d} 17:18:00"
    blocks = [
        obs_block(i * 2, dose_b=dose_b + i * 0.01, dose_e=dose_e, utc=utc) for i in range(n_obs)
    ]
    return rad_file(sol, blocks, start_time=utc, creation_time=creation_time, pad_to=pad_to)


def synthetic_name(sol: int, *, day: int | None = None, hh_mm: str = "10_00") -> str:
    return f"RAD_RDR_2026_{day if day is not None else 80:03d}_{hh_mm}_{sol:04d}_V00.TXT"


def synthetic_path(name: str) -> str:
    sol = sol_of(name)
    low = sol // 100 * 100
    return f"DATA/SOL_{low:05d}_{low + 99:05d}/{name}"


def checksums_text(entries: dict[str, str], extra_lines: list[str] | None = None) -> str:
    """`CHECKSUMS.TXT`: `md5  ./path` per line (LF), `entries` is path -> md5."""
    lines = [f"{digest}  ./{path}" for path, digest in entries.items()]
    return "\n".join([*lines, *(extra_lines or [])]) + "\n"


# --- fake PDS host -----------------------------------------------------------------------------


@dataclass
class Req:
    method: str
    path: str  # relative to the volume
    range: str | None
    if_none_match: str | None
    if_modified_since: str | None
    user_agent: str | None


@dataclass
class RadHost:
    """respx handler for the volume: files, lists, Range, ETags, failures.

    `files` maps the volume path (`DATA/SOL_.../RAD_RDR_...TXT`) to bytes. `CHECKSUMS.TXT` is
    generated from `files` (md5 of the current bodies) unless `checksums_override` is set;
    `md5_override` changes single entries. Behaviours in `fail[path]` are consumed one per
    request: `"503"`, `"429"`, `"404"`, `"truncate"` (body cut in half, full Content-Length),
    `"reset"` (connection error after the first chunk), `"corrupt"` (one byte flipped),
    `"boom"` (an unexpected exception inside the handler), `"redirect"` (302 elsewhere).
    """

    events: list[tuple[str, Any]]
    files: dict[str, bytes] = field(default_factory=dict)
    timing: bytes = b""
    checksums_override: bytes | None = None
    md5_override: dict[str, str] = field(default_factory=dict)
    extra_checksum_lines: list[str] = field(default_factory=list)
    etags: dict[str, str] = field(default_factory=lambda: {CHECKSUMS: '"cs-1"', TIMING: '"tm-1"'})
    last_modified: dict[str, str] = field(
        default_factory=lambda: {
            CHECKSUMS: "Mon, 03 Aug 2026 12:00:00 GMT",
            TIMING: "Tue, 01 Jan 2013 00:00:00 GMT",
        }
    )
    range_supported: bool = True
    send_content_length: bool = True
    chunk: int = 16384
    fail: dict[str, list[str]] = field(default_factory=dict)
    requests: list[Req] = field(default_factory=list)
    streamed: dict[str, int] = field(default_factory=dict)
    inflight: int = 0
    max_inflight: int = 0

    # -- setup --

    def set_file(self, path: str, data: bytes, *, bump_list: bool = True) -> None:
        self.files[path] = data
        if bump_list:
            self.bump(CHECKSUMS)

    def remove_file(self, path: str) -> None:
        del self.files[path]
        self.bump(CHECKSUMS)

    def bump(self, which: str) -> None:
        n = int(self.etags[which].strip('"').split("-")[1]) + 1
        self.etags[which] = f'"{which[:2].lower()}-{n}"'

    def checksums_bytes(self) -> bytes:
        if self.checksums_override is not None:
            return self.checksums_override
        entries = {p: self.md5_override.get(p, md5(d)) for p, d in self.files.items()}
        return checksums_text(entries, self.extra_checksum_lines).encode("ascii")

    def mount(self, router: respx.MockRouter) -> RadHost:
        router.route(host=HOST).mock(side_effect=self.handle)
        return self

    # -- queries --

    def calls(self, kind: str | None = None) -> list[Req]:
        """Requests: `lists`, `files`, `probes` (Range) or all."""
        if kind is None:
            return list(self.requests)
        is_list = lambda r: r.path in (CHECKSUMS, TIMING)  # noqa: E731
        if kind == "lists":
            return [r for r in self.requests if is_list(r)]
        if kind == "probes":
            return [r for r in self.requests if not is_list(r) and r.range]
        return [r for r in self.requests if not is_list(r)]

    def file_calls(self, path: str) -> list[Req]:
        return [r for r in self.requests if r.path == path]

    # -- the handler --

    def handle(self, request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix(PREFIX)
        req = Req(
            request.method,
            rel,
            request.headers.get("range"),
            request.headers.get("if-none-match"),
            request.headers.get("if-modified-since"),
            request.headers.get("user-agent"),
        )
        self.requests.append(req)
        self.events.append(("req", rel))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            return self._respond(request, rel)
        except BaseException:
            self._end(rel)
            raise

    def _end(self, rel: str) -> None:
        if self.inflight:
            self.inflight -= 1
        self.events.append(("end", rel))

    def _respond(self, request: httpx.Request, rel: str) -> httpx.Response:
        queue = self.fail.get(rel)
        behaviour = queue.pop(0) if queue else None
        if behaviour == "boom":
            raise RuntimeError("boom: unexpected failure inside the fake host")
        if behaviour in ("503", "429", "404"):
            self._end(rel)
            return httpx.Response(int(behaviour), content=b"error")
        if behaviour == "redirect":
            self._end(rel)
            return httpx.Response(302, headers={"Location": "https://evil.example.test/x.TXT"})

        if rel in (CHECKSUMS, TIMING):
            body = self.checksums_bytes() if rel == CHECKSUMS else self.timing
            etag = self.etags[rel]
            headers = {"ETag": etag, "Last-Modified": self.last_modified[rel]}
            if request.headers.get("if-none-match") == etag:
                self._end(rel)
                return httpx.Response(304, headers=headers)
            headers["Content-Length"] = str(len(body))
            return self._stream(200, rel, body, headers, behaviour)

        data = self.files.get(rel)
        if data is None:
            self._end(rel)
            return httpx.Response(404, content=b"not found")
        range_header = request.headers.get("range")
        if range_header and self.range_supported:
            match = re.fullmatch(r"bytes=-(\d+)", range_header)
            assert match, range_header
            n = int(match.group(1))
            tail = data[-n:]
            headers = {
                "Content-Range": f"bytes {len(data) - len(tail)}-{len(data) - 1}/{len(data)}",
                "Content-Length": str(len(tail)),
                "Accept-Ranges": "bytes",
            }
            return self._stream(206, rel, tail, headers, behaviour)
        headers = {"Accept-Ranges": "bytes" if self.range_supported else "none"}
        if self.send_content_length:
            headers["Content-Length"] = str(len(data))
        if behaviour == "corrupt":
            data = bytearray(data)
            data[len(data) // 2] ^= 0x01
            data = bytes(data)
        return self._stream(200, rel, data, headers, behaviour)

    def _stream(
        self, status: int, rel: str, body: bytes, headers: dict[str, str], behaviour: str | None
    ) -> httpx.Response:
        sent = body[: len(body) // 2] if behaviour == "truncate" else body
        stream = _Stream(self, rel, sent, fail_after=self.chunk if behaviour == "reset" else None)
        return httpx.Response(status, headers=headers, stream=stream)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, host: RadHost, rel: str, data: bytes, fail_after: int | None) -> None:
        self.host, self.rel, self.data, self.fail_after = host, rel, data, fail_after
        self.done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        sent = 0
        try:
            for start in range(0, len(self.data), self.host.chunk):
                if self.fail_after is not None and sent >= self.fail_after:
                    raise httpx.ReadError("connection reset by peer")
                piece = self.data[start : start + self.host.chunk]
                sent += len(piece)
                self.host.streamed[self.rel] = self.host.streamed.get(self.rel, 0) + len(piece)
                yield piece
        finally:
            self._finish()

    async def aclose(self) -> None:
        self._finish()

    def _finish(self) -> None:
        if not self.done:
            self.done = True
            self.host._end(self.rel)


def fixture_host(router: respx.MockRouter, events: list[tuple[str, Any]], *names: str) -> RadHost:
    """A host serving the named trimmed fixture files plus the fixture TIMING_ERRORS.TXT."""
    host = RadHost(events, timing=fixture("TIMING_ERRORS.TXT"))
    for name in names:
        host.files[rel_path(name)] = fixture(name)
    return host.mount(router)


# --- fake sleep ----------------------------------------------------------------------------------


@pytest.fixture
def events() -> list[tuple[str, Any]]:
    """Shared log of ("req"/"end", path) from the host and ("sleep", seconds) from fake sleep."""
    return []


@pytest.fixture
def fake_sleep(monkeypatch: pytest.MonkeyPatch, events: list[tuple[str, Any]]) -> list[float]:
    """Replace `asyncio.sleep`: record the requested seconds, never wait for real.

    `UpstreamClient` and `RateLimiter` bind `asyncio.sleep` as a keyword default at definition
    time, so their defaults are replaced as well (a no-op if the implementation injects its own).
    """
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds: float, result: Any = None) -> Any:
        slept.append(seconds)
        events.append(("sleep", seconds))
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    from app.sources import http

    for owner in (getattr(http, "UpstreamClient", None), getattr(http, "RateLimiter", None)):
        init = getattr(owner, "__init__", None)
        defaults = getattr(init, "__kwdefaults__", None)
        if defaults and "sleep" in defaults:
            monkeypatch.setattr(init, "__kwdefaults__", {**defaults, "sleep": sleep})
    return slept


PAUSE = 3.0


@pytest.fixture
def rad_env(monkeypatch: pytest.MonkeyPatch, settings: Any) -> Callable[..., None]:
    """Environment of the CLI under test; returns `set(**RAD_* overrides)` for one more setting."""
    from app.core.config import get_settings

    def set_env(**extra: str) -> None:
        for key, value in {
            "DATABASE_URL": settings.database_url,
            "SYNC_ENABLED": "false",
            "UPSTREAM_MAX_RPS": "0",
            "UPSTREAM_MAX_RETRIES": "0",
            "RAD_PAUSE_SECONDS": str(PAUSE),
            **extra,
        }.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    set_env()
    yield set_env
    get_settings.cache_clear()


async def run_rad(*argv: str, crash_ok: bool = False) -> int:
    """`python -m app.cli rad <argv>` in a worker thread; returns the exit code."""
    from app import cli

    try:
        return await asyncio.to_thread(cli.main, ["rad", *argv])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception:
        if crash_ok:
            return -1
        raise


def sleep_between_requests(events: list[tuple[str, Any]]) -> list[float]:
    """For every pair of consecutive requests: seconds slept between them."""
    gaps: list[float] = []
    total: float | None = None
    for kind, value in events:
        if kind == "req":
            if total is not None:
                gaps.append(total)
            total = 0.0
        elif kind == "sleep" and total is not None:
            total += float(value)
    return gaps


# --- DB helpers ----------------------------------------------------------------------------------


async def fetch(factory: Factory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


async def rad_files(factory: Factory) -> list[dict[str, Any]]:
    return await fetch(factory, "SELECT * FROM rad_files ORDER BY path")


async def rad_daily(factory: Factory) -> list[dict[str, Any]]:
    return await fetch(factory, "SELECT * FROM rad_daily ORDER BY sol")


async def rad_dataset(factory: Factory) -> dict[str, Any] | None:
    rows = await fetch(factory, "SELECT * FROM rad_dataset")
    return rows[0] if rows else None


async def file_row(factory: Factory, path: str) -> dict[str, Any]:
    rows = await fetch(factory, "SELECT * FROM rad_files WHERE path = :p", p=path)
    assert len(rows) == 1, (path, rows)
    return rows[0]


async def daily_row(factory: Factory, sol: int) -> dict[str, Any] | None:
    rows = await fetch(factory, "SELECT * FROM rad_daily WHERE sol = :s", s=sol)
    return rows[0] if rows else None


async def wipe_rad(factory: Factory) -> None:
    async with factory() as session:
        await session.execute(text("TRUNCATE rad_files, rad_daily, rad_dataset"))
        await session.commit()


async def insert_daily(
    factory: Factory,
    sol: int,
    *,
    earth_date: str = "2026-03-21",
    dose_b: tuple[float, float, float] = (6.0, 5.0, 7.0),
    dose_e: tuple[float, float, float] = (5.5, 4.5, 6.5),
    bu_count: int = 7200,
    live_s: float = 900.0,
    used: int = 1,
    dropped_qc: int = 0,
    dropped_low_rate: int = 0,
    dropped_invalid: int = 0,
    qc_bits: int = 0,
    timing_caveat: bool | None = False,
    source_path: str | None = None,
    updated_at: str = "2026-11-01T01:42:10+00:00",
) -> None:
    """Insert one `rad_daily` row (doses in uGy/h; `(mean, min, max)` tuples)."""
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO rad_daily (sol, earth_date, dose_b_mean, dose_b_min, dose_b_max, "
                "dose_e_mean, dose_e_min, dose_e_max, bu_count, live_s, n_used, n_dropped_qc, "
                "n_dropped_low_rate, n_dropped_invalid, qc_bits, timing_caveat, source_path, "
                "updated_at) VALUES (:sol, :ed, :bm, :bn, :bx, :em, :en, :ex, :bu, "
                ":live, :used, :dq, :dl, :di, :qc, :tc, :sp, :ua)"
            ),
            {
                "sol": sol,
                "ed": date.fromisoformat(earth_date),
                "bm": dose_b[0],
                "bn": dose_b[1],
                "bx": dose_b[2],
                "em": dose_e[0],
                "en": dose_e[1],
                "ex": dose_e[2],
                "bu": bu_count,
                "live": live_s,
                "used": used,
                "dq": dropped_qc,
                "dl": dropped_low_rate,
                "di": dropped_invalid,
                "qc": qc_bits,
                "tc": timing_caveat,
                "sp": source_path
                or f"DATA/SOL_{sol // 100 * 100:05d}_{sol // 100 * 100 + 99:05d}/"
                f"RAD_RDR_2026_080_17_18_{sol:04d}_V00.TXT",
                "ua": datetime.fromisoformat(updated_at),
            },
        )
        await session.commit()


async def insert_dataset(factory: Factory, **values: Any) -> None:
    values = {
        k: datetime.fromisoformat(v) if k.endswith("_at") and isinstance(v, str) else v
        for k, v in values.items()
    }
    columns = ["id", *values]
    marks = ["1", *[f":{k}" for k in values]]
    async with factory() as session:
        await session.execute(
            text(f"INSERT INTO rad_dataset ({', '.join(columns)}) VALUES ({', '.join(marks)})"),
            values,
        )
        await session.commit()


def attr(obj: Any, *names: str) -> Any:
    """First existing attribute of `obj` among `names` (the contract does not fix all names)."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"{type(obj).__name__} has none of {names}")


Runner = Callable[..., Any]


def synthetic_host(
    router: respx.MockRouter,
    events: list[tuple[str, Any]],
    files: dict[str, bytes],
    timing: bytes = b"",
) -> RadHost:
    """A mounted host serving exactly `files` (volume path -> bytes)."""
    host = RadHost(events, timing=timing)
    host.files.update(files)
    return host.mount(router)


def twenty_sols(
    router: respx.MockRouter, events: list[tuple[str, Any]]
) -> tuple[RadHost, dict[int, str]]:
    """Sols 100..119, one small valid file each; returns the host and sol -> path."""
    paths = {sol: synthetic_path(synthetic_name(sol)) for sol in range(100, 120)}
    files = {paths[sol]: good_file(sol, dose_b=6.0 + sol / 1000) for sol in paths}
    return synthetic_host(router, events, files), paths
