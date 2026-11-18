"""Shared helpers for the Spirit/Opportunity (MER, stage 8) tests.

The fixtures in tests/fixtures/upstream/mer/ are real PDS index rows (selected by
data-engineer from the full ``edrindex.tab`` files, see ``scripts/record_fixtures.py
--part mer``) laid out like MER_CACHE_DIR: ``<volume>/index/edrindex.{lbl,tab}``.

``reference_frames`` is an independent, deliberately simple reading of the contract
(docs/proposals/stage-8-mer-import.md + the verified facts in docs/DATA-SOURCES.md, which
win over the plan) used as the oracle of the adapter tests:
- columns by NAME from the label; ROVER_MOTION_COUNTER (ITEMS = 5) is 5 tab fields;
- product type = PRODUCT_ID[11:14]: EFF full, ESF subframe, EDN downsampled, others dropped;
- frame key PRODUCT_ID[:11] + PRODUCT_ID[14:25]; best product EFF > ESF > EDN, then the
  higher version, then the greater PRODUCT_ID; a PRODUCT_ID listed twice keeps the row with
  the later PRODUCT_CREATION_TIME;
- URLs: base + PATH_NAME (data -> browse) + FILE_NAME + ".jpg" / base + PATH_NAME +
  FILE_NAME; Descent (no PATH_NAME/FILE_NAME): /<volume>/data/sol0001/edr/ + lower-case
  PRODUCT_ID + ".img".
Hard-coded spot checks in the tests pin the oracle itself to the data.
"""

import csv
import io
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MER_FIXTURES = ROOT / "tests" / "fixtures" / "upstream" / "mer"
DEFAULT_BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/mer/"

# MER-1 = Opportunity, MER-2 = Spirit; instruments: no Navcam, ho Hazcam, po Pancam,
# mo Microscopic Imager, do Descent.
ROVER_PREFIX = {"opportunity": "mer1", "spirit": "mer2"}
KINDS = ("no", "ho", "po", "mo", "do")
VOLUMES = {
    rover: [f"{prefix}{kind}_0xxx" for kind in KINDS] for rover, prefix in ROVER_PREFIX.items()
}

SAMPLE_TYPES = {"EFF": "full", "ESF": "subframe", "EDN": "downsampled"}
PRIORITY = {"EFF": 3, "ESF": 2, "EDN": 1}
DROPPED_TYPES = ("ETH", "ERP", "EHG", "ERS", "ECS")

CREDITS = {
    "FHAZ": "NASA/JPL-Caltech",
    "RHAZ": "NASA/JPL-Caltech",
    "NAVCAM": "NASA/JPL-Caltech",
    "ENTRY": "NASA/JPL-Caltech",
    "PANCAM": "NASA/JPL-Caltech/Cornell",
    "MI": "NASA/JPL-Caltech/USGS",
}

# --- facts of the fixture files, counted by hand from the rows -------------------------

FIXTURE_ROWS = {
    "mer1no_0xxx": 30,
    "mer1ho_0xxx": 21,
    "mer1po_0xxx": 29,
    "mer1mo_0xxx": 20,
    "mer1do_0xxx": 9,
    "mer2no_0xxx": 24,
    "mer2ho_0xxx": 15,
    "mer2po_0xxx": 18,
    "mer2mo_0xxx": 15,
    "mer2do_0xxx": 9,
}
ROWS_TOTAL = {"opportunity": 109, "spirit": 81}
# Frames after the type filter and the frame dedup.
FRAMES_TOTAL = {"opportunity": 47, "spirit": 37}
FRAMES_BY_CAMERA = {
    "opportunity": {"NAVCAM": 11, "FHAZ": 10, "RHAZ": 1, "PANCAM": 13, "MI": 9, "ENTRY": 3},
    "spirit": {"NAVCAM": 9, "FHAZ": 8, "RHAZ": 1, "PANCAM": 10, "MI": 6, "ENTRY": 3},
}
FULL_FRAMES = {"opportunity": 16, "spirit": 12}
# Highest sol with a stored frame / with any index row.
MAX_PHOTO_SOL = {"opportunity": 5000, "spirit": 1925}
MAX_INDEX_SOL = {"opportunity": 5000, "spirit": 2116}
MAX_FULL_SOL = {"opportunity": 3718, "spirit": 1925}

# Spirit sol 296, Pancam: one frame sent as EDN and ESF -> one photo, the ESF product.
SPIRIT_MERGED_SOL = 296
SPIRIT_MERGED_PRODUCTS = ("2P152642481EDN8992P2111R1M1", "2P152642481ESF8992P2111R1M1")
SPIRIT_MERGED_KEPT = "2P152642481ESF8992P2111R1M1"
# Opportunity, PRODUCT_ID listed twice (re-released product): sol -> (product, kept
# PRODUCT_CREATION_TIME). Sol 3602: the later row is the second; sol 3757: the first.
OPPORTUNITY_REPEATED = {
    3602: ("1F447958540EFFCBCRP1214L0M1", "2015-07-14T23:50:33Z"),
    3757: ("1F461721193ESFCHJ3P1224L0M1", "2015-07-14T23:50:35Z"),
    3112: ("1N404461313EFFBWR7P1655L0M1", "2015-07-14T23:50:43Z"),
    3419: ("1N431713460EFFC7L2P0793R0M1", "2015-07-14T23:50:46Z"),
    3718: ("1P458253626EFFCEJMP2445R2M1", "2015-07-14T23:50:37Z"),
    3719: ("1P458342051ESFCEN7P2550R6M1", "2015-07-14T23:50:35Z"),
}


@dataclass
class Frame:
    """What the contract says one stored MER photo is."""

    rover: str
    volume: str
    source_id: str
    sol: int
    camera_code: str
    sample_type: str
    taken_at: datetime
    received_at: datetime | None
    img_full: str
    source_link: str
    credit: str
    site: int | None
    drive: int | None
    instrument_id: str
    products: set[str] = field(default_factory=set)
    row: dict[str, str] = field(default_factory=dict)


def label_names(label: str) -> list[str]:
    """Column names of the label, in COLUMN_NUMBER order (they are written in order)."""
    return re.findall(r"^\s*NAME\s*=\s*(\S+)", label, re.MULTILINE)


def label_rows(label: str) -> int:
    match = re.search(r"^\s*ROWS\s*=\s*(\d+)", label, re.MULTILINE)
    assert match is not None
    return int(match.group(1))


def expanded_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        if name == "ROVER_MOTION_COUNTER":
            out += [f"ROVER_MOTION_COUNTER_{i}" for i in range(5)]
        else:
            out.append(name)
    return out


def read_volume(cache_dir: Path, volume: str) -> list[dict[str, str]]:
    index = cache_dir / volume / "index"
    names = expanded_names(label_names((index / "edrindex.lbl").read_text(encoding="latin-1")))
    text = (index / "edrindex.tab").read_bytes().decode("latin-1")
    rows = []
    for raw in csv.reader(io.StringIO(text, newline=""), delimiter="\t"):
        assert len(raw) == len(names), (volume, len(raw), len(names))
        rows.append({name: value.strip() for name, value in zip(names, raw, strict=True)})
    return rows


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _camera(volume: str, instrument: str) -> str:
    kind = volume[4:6]
    if kind == "ho":
        return "FHAZ" if instrument.startswith("FRONT_") else "RHAZ"
    return {"no": "NAVCAM", "po": "PANCAM", "mo": "MI", "do": "ENTRY"}[kind]


def _urls(base: str, volume: str, row: dict[str, str]) -> tuple[str, str]:
    base = base.rstrip("/")
    path = row.get("PATH_NAME")
    name = row.get("FILE_NAME")
    if not path or not name:  # Descent
        path = f"/{volume}/data/sol{int(row['PLANET_DAY_NUMBER']):04d}/edr/"
        name = row["PRODUCT_ID"].lower() + ".img"
    browse = path.replace("/data/", "/browse/")
    return f"{base}{browse}{name}.jpg", f"{base}{path}{name}"


def reference_frames(
    rover: str, cache_dir: Path = MER_FIXTURES, base_url: str = DEFAULT_BASE_URL
) -> list[Frame]:
    frames: list[Frame] = []
    for volume in VOLUMES[rover]:
        # One row per PRODUCT_ID: the later PRODUCT_CREATION_TIME wins.
        by_pid: dict[str, dict[str, str]] = {}
        for row in read_volume(cache_dir, volume):
            pid = row["PRODUCT_ID"]
            if row["PRODUCT_ID"][11:14] not in SAMPLE_TYPES:
                continue
            old = by_pid.get(pid)
            if old is None or row["PRODUCT_CREATION_TIME"] > old["PRODUCT_CREATION_TIME"]:
                by_pid[pid] = row
        groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for pid, row in by_pid.items():
            groups[pid[:11] + pid[14:25]].append(row)
        for rows in groups.values():
            best = max(
                rows,
                key=lambda r: (
                    PRIORITY[r["PRODUCT_ID"][11:14]],
                    r["PRODUCT_ID"][26],
                    r["PRODUCT_ID"],
                ),
            )
            code = _camera(volume, best["INSTRUMENT_ID"])
            img_full, link = _urls(base_url, volume, best)
            received = best.get("EARTH_RECEIVED_START_TIME")
            frames.append(
                Frame(
                    rover=rover,
                    volume=volume,
                    source_id=best["PRODUCT_ID"],
                    sol=int(best["PLANET_DAY_NUMBER"]),
                    camera_code=code,
                    sample_type=SAMPLE_TYPES[best["PRODUCT_ID"][11:14]],
                    taken_at=_utc(best["START_TIME"]),
                    received_at=_utc(received) if received else None,
                    img_full=img_full,
                    source_link=link,
                    credit=CREDITS[code],
                    site=int(best["ROVER_MOTION_COUNTER_0"]),
                    drive=int(best["ROVER_MOTION_COUNTER_1"]),
                    instrument_id=best["INSTRUMENT_ID"],
                    products={r["PRODUCT_ID"] for r in rows},
                    row=best,
                )
            )
    return frames


def received_by_sol(rover: str, cache_dir: Path = MER_FIXTURES) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for volume in VOLUMES[rover]:
        for row in read_volume(cache_dir, volume):
            counts[int(row["PLANET_DAY_NUMBER"])] += 1
    return dict(counts)


def copy_cache(dest: Path, rovers: tuple[str, ...] = ("spirit", "opportunity")) -> Path:
    """A writable copy of the fixture cache (for tests that alter or remove files)."""
    for rover in rovers:
        for volume in VOLUMES[rover]:
            shutil.copytree(MER_FIXTURES / volume, dest / volume)
    return dest


def rewrite_rows(cache_dir: Path, volume: str, transform: Any) -> None:
    """Apply ``transform(list_of_raw_lines) -> list_of_raw_lines`` to a cached .tab.

    Lines keep their bytes and CRLF terminators; the label is left unchanged, so the
    transform must keep the number of rows.
    """
    path = cache_dir / volume / "index" / "edrindex.tab"
    lines = path.read_bytes().split(b"\r\n")
    assert lines[-1] == b""
    new = transform(lines[:-1])
    assert len(new) == len(lines) - 1
    path.write_bytes(b"\r\n".join(new) + b"\r\n")


def build_adapter(rover: str, cache_dir: Path, base_url: str = DEFAULT_BASE_URL) -> Any:
    """``MerAdapter`` for ``rover`` reading ``cache_dir`` (the one place that knows its
    constructor, so a signature change touches only this helper)."""
    from app.core.config import Settings
    from app.sources.mer import MerAdapter

    settings = Settings(mer_cache_dir=cache_dir, mer_pds_base_url=base_url)
    return MerAdapter(rover, settings=settings)
