"""Record trimmed upstream fixtures from the JPL raw-image feeds.

Makes a small, fixed number of live GET requests (politely spaced) to the
Perseverance (Mars 2020) and Curiosity (MSL) raw-image feeds, writes trimmed
JSON responses to ``tests/fixtures/upstream/<mission>/`` and logs a summary of
the observed feed behaviour (filtering, pagination, field values).

Usage::

    uv run python scripts/record_fixtures.py [--raw-dir DIR]
        [--part all|base|m2020-range|mer|mmgis|rad]

``--part mer`` makes no requests: it copies the real MER index samples prepared by
data-engineer (``data/mer-samples/<volume>/``) into ``tests/fixtures/upstream/mer/``.

``--part mmgis`` (stage 11) makes no requests: it trims the four real MMGIS location files
prepared by data-engineer (``data/locations/``) into ``tests/fixtures/upstream/mmgis/``.

``--part mer-img`` (stage 9a) downloads at most six real MER ``.img`` products (the PDS paths
come from the ``source_link`` column of the dev database), stores every one under
``data/mer-img/`` (git-ignored, for manual checks) and copies into
``tests/fixtures/upstream/mer/img/`` only those not larger than ``SMALL_IMG_BYTES``.

``--part rad`` (stage 21) makes no requests: it trims the local MSL RAD samples
(``data/rad_sample/``) into ``tests/fixtures/upstream/rad/`` (PHA sections cut, a few
observations kept, ``CHECKSUMS.TXT`` rebuilt with md5 of the trimmed files).

``--raw-dir`` optionally stores full untrimmed responses and the summary
outside the repository for analysis. Untrimmed responses are never written
into the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

# Allow `uv run python scripts/record_fixtures.py` from the project root to import `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Settings  # noqa: E402

logger = logging.getLogger("record_fixtures")

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "upstream"
M20_DIR = FIXTURES_DIR / "m2020"
MSL_DIR = FIXTURES_DIR / "msl"

USER_AGENT = "mars-rover-api/0.1 (fixture recorder)"
TIMEOUT_S = 30.0
PAUSE_S = 1.5
MAX_KEEP = 5
MIN_KEEP = 3
MAX_MSL_PAGES = 5

M20_COMMON = {"feed": "raw_images", "category": "mars2020", "feedtype": "json"}

MSL_ORDER = "sol desc,instrument_sort asc,sample_type_sort asc, date_taken desc"
MSL_SOL = 4327
# Pinned so that re-runs overwrite the same fixture files (&sol= is ignored by the feed).
M20_SOL = 2029
SITE_ROOT = "https://mars.nasa.gov"


class Recorder:
    """Thin rate-limited HTTP wrapper that also keeps raw dumps."""

    def __init__(
        self, client: httpx.Client, raw_dir: Path | None, *, m20_url: str, msl_url: str
    ) -> None:
        self.client = client
        self.raw_dir = raw_dir
        # Feed URLs come from Settings (same source as the application).
        self.m20_url = m20_url
        self.msl_url = msl_url
        self.request_count = 0
        self._last_request = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < PAUSE_S:
            time.sleep(PAUSE_S - elapsed)

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self._wait()
        self.request_count += 1
        logger.info("GET #%d %s params=%s", self.request_count, url, params)
        try:
            response = self.client.get(url, params=params)
        finally:
            self._last_request = time.monotonic()
        logger.info("-> %s %s", response.status_code, response.url)
        return response

    def get_json(self, url: str, params: dict[str, Any], raw_name: str) -> dict[str, Any]:
        response = self.get(url, params)
        response.raise_for_status()
        data = response.json()
        if self.raw_dir is not None:
            write_json(self.raw_dir / raw_name, data)
        return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("wrote %s (%d bytes)", path, path.stat().st_size)


def pick_varied(
    elements: list[dict[str, Any]],
    keys: list[Any],
) -> list[dict[str, Any]]:
    """Choose up to MAX_KEEP elements, preferring new values of each key in turn.

    ``keys`` are callables extracting a feature (sample type, instrument, ...).
    Elements are kept in their original order.
    """
    chosen: list[int] = []
    for key in keys:
        seen = {key(elements[i]) for i in chosen}
        for i, element in enumerate(elements):
            if len(chosen) >= MAX_KEEP:
                break
            if i in chosen:
                continue
            value = key(element)
            if value not in seen:
                chosen.append(i)
                seen.add(value)
    for i in range(len(elements)):
        if len(chosen) >= MIN_KEEP:
            break
        if i not in chosen:
            chosen.append(i)
    return [elements[i] for i in sorted(chosen)]


def trim(data: dict[str, Any], array_key: str, keys: list[Any]) -> dict[str, Any]:
    if array_key not in data or not isinstance(data[array_key], list):
        return data
    trimmed = dict(data)
    trimmed[array_key] = pick_varied(data[array_key], keys)
    return trimmed


def m20_sample_type(image: dict[str, Any]) -> Any:
    return image.get("sample_type")


def m20_instrument(image: dict[str, Any]) -> Any:
    return (image.get("camera") or {}).get("instrument")


def msl_sample_type(item: dict[str, Any]) -> Any:
    return (item.get("extended") or {}).get("sample_type")


def msl_instrument(item: dict[str, Any]) -> Any:
    return item.get("instrument")


def msl_thumbnail(item: dict[str, Any]) -> Any:
    return item.get("is_thumbnail")


M20_KEYS = [m20_sample_type, m20_instrument]
MSL_KEYS = [msl_thumbnail, msl_sample_type, msl_instrument]


def describe_m20(data: dict[str, Any]) -> dict[str, Any]:
    images = data.get("images") or []
    top = {k: v for k, v in data.items() if k != "images"}
    return {
        "top_level": top,
        "top_level_types": {k: type(v).__name__ for k, v in top.items()},
        "image_count": len(images),
        "sols": dict(Counter(i.get("sol") for i in images)),
        "sample_types": dict(Counter(m20_sample_type(i) for i in images)),
        "instruments": dict(Counter(m20_instrument(i) for i in images)),
        "drive_types": dict(Counter(type(i.get("drive")).__name__ for i in images)),
        "date_taken_utc_examples": sorted({i.get("date_taken_utc") for i in images[:3]}),
        "image_keys": sorted(images[0].keys()) if images else [],
    }


def describe_msl(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("items") or []
    top = {k: v for k, v in data.items() if k != "items"}
    return {
        "top_level": top,
        "item_count": len(items),
        "sols": dict(Counter(i.get("sol") for i in items)),
        "item_keys": sorted(items[0].keys()) if items else [],
        "extended_keys": sorted((items[0].get("extended") or {}).keys()) if items else [],
    }


def msl_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": len(items),
        "unique_ids": len({i.get("id") for i in items}),
        "instruments": dict(Counter(msl_instrument(i) for i in items).most_common()),
        "sample_types": dict(Counter(msl_sample_type(i) for i in items).most_common()),
        "is_thumbnail": dict(Counter(str(msl_thumbnail(i)) for i in items)),
    }


def record_m20(rec: Recorder, summary: dict[str, Any]) -> None:
    latest = rec.get_json(rec.m20_url, {**M20_COMMON, "latest": "true"}, "m2020_latest.json")
    write_json(M20_DIR / "latest.json", latest)
    summary["m2020_latest"] = latest

    page0 = rec.get_json(
        rec.m20_url,
        {**M20_COMMON, "num": 5, "page": 0, "order": "sol desc"},
        "m2020_list_page0.json",
    )
    write_json(M20_DIR / "list_page0.json", trim(page0, "images", M20_KEYS))
    summary["m2020_list_page0"] = describe_m20(page0)

    sol = M20_SOL
    by_sol = rec.get_json(rec.m20_url, {**M20_COMMON, "sol": sol}, f"m2020_sol_{sol}.json")
    write_json(M20_DIR / f"sol_{sol}.json", trim(by_sol, "images", M20_KEYS))
    summary["m2020_sol"] = {"sol": sol, **describe_m20(by_sol)}

    total = int(by_sol.get("total_results") or 0)
    if len(by_sol.get("images") or []) >= total:
        summary["m2020_sol_page1"] = "skipped: sol response already contained all images"
        return
    page1 = rec.get_json(
        rec.m20_url,
        {**M20_COMMON, "sol": sol, "num": 50, "page": 1},
        f"m2020_sol_{sol}_page1.json",
    )
    write_json(M20_DIR / f"sol_{sol}_page1.json", trim(page1, "images", M20_KEYS))
    desc = describe_m20(page1)
    ids0 = {i.get("imageid") for i in by_sol.get("images") or []}
    ids1 = {i.get("imageid") for i in page1.get("images") or []}
    desc["overlap_with_sol_response"] = len(ids0 & ids1)
    summary["m2020_sol_page1"] = desc


M20_RANGE_SOL = 1000
M20_RANGE_PAGES = (0, 3)


def record_m20_range(rec: Recorder, summary: dict[str, Any]) -> None:
    """Range filter (condition_2/condition_3) for one sol, first and last page."""
    sol = M20_RANGE_SOL
    for page in M20_RANGE_PAGES:
        params = {
            **M20_COMMON,
            "order": "sol desc",
            "num": 100,
            "page": page,
            "condition_2": f"{sol}:sol:gte",
            "condition_3": f"{sol}:sol:lte",
        }
        data = rec.get_json(rec.m20_url, params, f"m2020_range_{sol}_page{page}.json")
        write_json(M20_DIR / f"range_{sol}_page{page}.json", trim(data, "images", M20_KEYS))
        summary[f"m2020_range_{sol}_page{page}"] = describe_m20(data)


def msl_params(page: int, per_page: int, sol: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "order": MSL_ORDER,
        "per_page": per_page,
        "page": page,
        "condition_1": "msl:mission",
    }
    if sol is not None:
        params["condition_2"] = f"{sol}:sol:in"
    return params


def record_msl_sol(
    rec: Recorder, sol: int, prefix: str, save: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk all pages of one sol while ``more`` is true. Returns (items, page descriptions)."""
    items: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for page in range(MAX_MSL_PAGES):
        data = rec.get_json(
            rec.msl_url, msl_params(page, 200, sol), f"msl_{prefix}_page{page}.json"
        )
        if save:
            write_json(MSL_DIR / f"{prefix}_page{page}.json", trim(data, "items", MSL_KEYS))
        items.extend(data.get("items") or [])
        pages.append(describe_msl(data))
        if not data.get("more"):
            break
    return items, pages


def record_msl(rec: Recorder, summary: dict[str, Any]) -> None:
    latest = rec.get_json(rec.msl_url, msl_params(0, 1), "msl_latest.json")
    write_json(MSL_DIR / "latest.json", trim(latest, "items", MSL_KEYS))
    latest_sol = int(latest["items"][0]["sol"])
    summary["msl_latest"] = {"latest_sol": latest_sol, **describe_msl(latest)}

    items, pages = record_msl_sol(rec, MSL_SOL, f"sol_{MSL_SOL}", save=True)
    summary["msl_sol_4327"] = {"pages": pages, "counts": msl_counts(items)}

    if items:
        link = items[0].get("link")
        response = rec.get(SITE_ROOT + str(link))
        summary["msl_link_check"] = {
            "link": link,
            "status": response.status_code,
            "final_url": str(response.url),
            "redirects": [str(r.url) for r in response.history],
        }

    params = msl_params(0, 200, MSL_SOL)
    params["extended"] = "thumbnail::sample_type::noteq"
    no_thumbs = rec.get_json(rec.msl_url, params, "msl_sol_4327_no_thumbnails.json")
    summary["msl_extended_filter"] = {
        "top_level": {k: v for k, v in no_thumbs.items() if k != "items"},
        "counts": msl_counts(no_thumbs.get("items") or []),
    }

    latest_items, latest_pages = record_msl_sol(
        rec, latest_sol, f"latest_sol_{latest_sol}", save=False
    )
    summary["msl_latest_sol"] = {
        "sol": latest_sol,
        "pages": [p["top_level"] | {"item_count": p["item_count"]} for p in latest_pages],
        "counts": msl_counts(latest_items),
    }


MER_DIR = FIXTURES_DIR / "mer"
MER_SAMPLES_DIR = ROOT / "data" / "mer-samples"
_ROWS_RE = re.compile(rb"^(\s*(?:ROWS|FILE_RECORDS)\s*=\s*)(\d+)", re.MULTILINE)


def build_mer_fixtures(samples_dir: Path = MER_SAMPLES_DIR, out_dir: Path = MER_DIR) -> None:
    """Copy real MER index samples into the fixture cache layout (offline, no requests).

    ``samples_dir/<volume>/edrindex.{lbl,tab}`` (real label + real rows selected by
    data-engineer from the full PDS indexes) becomes
    ``out_dir/<volume>/index/edrindex.{lbl,tab}`` - the MER_CACHE_DIR layout. The only
    change is ``ROWS`` / ``FILE_RECORDS`` in the label, set to the number of rows kept; the
    bytes of the rows (CRLF, tabs, quotes, padding) and the wrong ``ROW_BYTES`` /
    ``START_BYTE`` values of the label are kept as they are.
    """
    volumes = sorted(p for p in samples_dir.iterdir() if (p / "edrindex.tab").is_file())
    if not volumes:
        raise SystemExit(f"no MER samples in {samples_dir}")
    for volume in volumes:
        table = (volume / "edrindex.tab").read_bytes()
        rows = table.count(b"\r\n")
        label = _ROWS_RE.sub(rb"\g<1>" + str(rows).encode(), (volume / "edrindex.lbl").read_bytes())
        target = out_dir / volume.name / "index"
        target.mkdir(parents=True, exist_ok=True)
        (target / "edrindex.tab").write_bytes(table)
        (target / "edrindex.lbl").write_bytes(label)
        logger.info("wrote %s: %d rows", target, rows)


MER_IMG_DIR = MER_DIR / "img"
MER_IMG_DATA_DIR = ROOT / "data" / "mer-img"
SMALL_IMG_BYTES = 200_000
# Spirit products picked from the dev database: one per camera / product type. Only the
# source_link of each is read from the database; nothing else is trusted from it.
MER_IMG_SAMPLES = {
    "navcam_eff": "2N135232528EFF2700P1556L0M1",  # Navcam 1024x1024 full frame
    "pancam_eff": "2P135241228EFF2702P2544L2M1",  # Pancam full frame
    "hazcam_eff": "2F135251873EFF27MCP1201L0M1",  # Hazcam full frame
    "mi_eff": "2M135687370EFF3000P2956M2M1",  # Microscopic Imager
    "pancam_esf": "2P135232363ESF2700P2682L8M1",  # subframe
    "navcam_edn": "2N160633049EDNA2K1F0006R0M1",  # downsampled
}


def _mer_image_links(source_ids: list[str]) -> dict[str, str]:
    import asyncio

    import asyncpg

    async def fetch() -> dict[str, str]:
        url = Settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "SELECT source_id, source_link FROM photos WHERE source_id = ANY($1::text[])",
                source_ids,
            )
        finally:
            await conn.close()
        return {r["source_id"]: r["source_link"] for r in rows}

    return asyncio.run(fetch())


def _pds3_facts(data: bytes) -> dict[str, str]:
    """Label keys that matter for the parser (read from the ASCII head, up to END)."""
    head = re.split(rb"^END\s*$", data[:200_000], maxsplit=1, flags=re.MULTILINE)[0].decode(
        "latin-1", "replace"
    )
    keys = (
        "RECORD_BYTES",
        "LABEL_RECORDS",
        "^IMAGE",
        "LINES",
        "LINE_SAMPLES",
        "BANDS",
        "SAMPLE_BITS",
        "SAMPLE_TYPE",
        "LINE_PREFIX_BYTES",
        "LINE_SUFFIX_BYTES",
        "BAND_STORAGE_TYPE",
        "INSTRUMENT_ID",
    )
    facts: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in keys:
            facts.setdefault(key.strip(), value.strip())
    return facts


def record_mer_images() -> None:
    """Download the MER .img samples; big ones go to data/mer-img, small ones into tests."""
    links = _mer_image_links(list(MER_IMG_SAMPLES.values()))
    MER_IMG_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=120.0, follow_redirects=True) as client:
        for index, (name, source_id) in enumerate(MER_IMG_SAMPLES.items()):
            link = links.get(source_id)
            if link is None:
                logger.warning("%s: %s not in the dev database, skipped", name, source_id)
                continue
            if index:
                time.sleep(PAUSE_S)
            response = client.get(link)
            response.raise_for_status()
            data = response.content
            filename = f"{source_id.lower()}.img"
            (MER_IMG_DATA_DIR / filename).write_bytes(data)
            in_repo = len(data) <= SMALL_IMG_BYTES
            if in_repo:
                (MER_IMG_DIR / filename).write_bytes(data)
            summary[name] = {
                "source_id": source_id,
                "link": link,
                "bytes": len(data),
                "in_repo": in_repo,
                **_pds3_facts(data),
            }
    sys.stdout.write(json.dumps(summary, indent=2) + chr(10))


MMGIS_SOURCE_DIR = ROOT / "data" / "locations"
MMGIS_DIR = FIXTURES_DIR / "mmgis"
MMGIS_KEEP_POINTS = 6  # coordinates kept per trimmed LineString (first five and the last)

# Hand-picked feature indexes of the full files (2026-11-19 snapshot).
MMGIS_PICKS: dict[str, list[int]] = {
    # site 3 (drive gaps 0/110/386/578), three stops on one sol (site 9), a -9999 point with
    # `images` (site 81, then a drive gap 3608 -> 4488), site 91 -> 92 -> last stop (93, 0).
    "M20_waypoints": [0, 1, 2, 3, 80, 81, 82, 612, 613, 704, 705, 706, 707],
    # first stops, site 42 -> 43 (first stop of 43 is drive 216), the last stop (125, 2490).
    "MSL_waypoints": [0, 1, 2, 3, 241, 242, 243, 244, 245, 1385, 1386, 1387, 1388],
    # 181 has the junk elevation (~2e-5) in z; 558 is the last segment (sol 2022).
    "M20_traverse": [0, 1, 2, 181, 182, 557, 558],
    # 0 is the MultiLineString (kept intact), the rest are LineStrings.
    "MSL_traverse": [0, 1, 2, 3, 4, 5],
}


def build_mmgis_fixtures(source_dir: Path = MMGIS_SOURCE_DIR, out_dir: Path = MMGIS_DIR) -> None:
    """Trim the real MMGIS files to a few features (offline, no requests).

    Structure is unchanged (root members `type`, `name`, `crs` kept, every kept feature as is);
    only the `coordinates` of LineStrings are shortened, except the leading MSL
    MultiLineString which stays intact.
    """
    for name, picks in MMGIS_PICKS.items():
        data = json.loads((source_dir / f"{name}.json").read_text(encoding="utf-8"))
        features = []
        for index in picks:
            feature = data["features"][index]
            geometry = feature["geometry"]
            if (
                geometry["type"] == "LineString"
                and len(geometry["coordinates"]) > MMGIS_KEEP_POINTS
            ):
                coords = geometry["coordinates"]
                geometry["coordinates"] = [*coords[: MMGIS_KEEP_POINTS - 1], coords[-1]]
            features.append(feature)
        data["features"] = features
        path = out_dir / f"{name.lower()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8")
        logger.info("wrote %s (%d features, %d bytes)", path, len(features), path.stat().st_size)


RAD_SOURCE_DIR = ROOT / "data" / "rad_sample"
RAD_DIR = FIXTURES_DIR / "rad"
RAD_VOLUME_FOLDERS = {
    10: "SOL_00000_00089",
    222: "SOL_00180_00269",
    1649: "SOL_01649_01772",
    4842: "SOL_04710_04843",
    4843: "SOL_04710_04843",
}
# Sections kept verbatim in every kept block; PHA is cut, histograms/LET only on request.
_RAD_KEEP = (b"OBSERVATION", b"COUNTERS", b"COUNTER_L1", b"COUNTER_L2", b"DOSIMETRY")
_RAD_SECTION = re.compile(rb"(?m)^(?=\[[A-Z0-9_]+: \d+\])")


def _rad_trim_block(block: bytes, *, pha_lines: int, extras: bool) -> bytes:
    out = []
    for section in _RAD_SECTION.split(block):
        name = re.match(rb"\[([A-Z0-9_]+): ", section)
        if name is None:
            continue
        key = name.group(1)
        if key == b"PHA":
            lines = section.splitlines(keepends=True)
            out.append(b"".join(lines[: 2 + pha_lines]))
        elif key.startswith((b"HISTOGRAM", b"DOSIMETRY_LET")):
            if extras:
                out.append(section)
        elif key.startswith(_RAD_KEEP):
            out.append(section)
    return b"".join(out)


def _rad_pick(
    src: Path, picks: dict[int, int], *, extras: tuple[int, ...] = ()
) -> tuple[bytes, list[bytes]]:
    """Header and trimmed blocks of the observations in ``picks`` (index -> PHA lines kept)."""
    data = src.read_bytes()
    head, *blocks = re.split(rb"(?m)^(?=\[OBSERVATION: )", data)
    kept = []
    for block in blocks:
        index = int(re.match(rb"\[OBSERVATION: (\d+)\]", block).group(1))  # type: ignore[union-attr]
        if index in picks:
            kept.append(_rad_trim_block(block, pha_lines=picks[index], extras=index in extras))
    return head, kept


def build_rad_fixtures(source_dir: Path = RAD_SOURCE_DIR, out_dir: Path = RAD_DIR) -> None:
    """Trim the local MSL RAD RDR samples (offline, no requests); see rad/README.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}

    # Sol 10 (early format): garbage UTC (12), low BU rate (13), last block (25).
    head, blocks = _rad_pick(
        source_dir / "RAD_RDR_2012_228_20_41_0010_V00.TXT", {0: 8, 12: 8, 13: 8, 25: 8}
    )
    files["RAD_RDR_2012_228_20_41_0010_V00.TXT"] = head + b"".join(blocks)

    # Sol 4843 (recent format, CRLF): twins (PHA only), QC=00000001 (22), wrapped time (174).
    picks = {0: 120, 1: 3, 2: 3, 22: 3, 23: 3, 174: 3, 175: 3}
    head, blocks = _rad_pick(source_dir / "RAD_RDR_2026_080_17_18_4843_V00.TXT", picks, extras=(0,))
    body = head + b"".join(blocks)
    files["RAD_RDR_2026_080_17_18_4843_V00.TXT"] = body
    files["RAD_RDR_2026_079_17_02_4842_V00.TXT"] = body.replace(
        b"START_SOL=4843", b"START_SOL=4842", 1
    ).replace(b'START_TIME="2026-080 17:18:00"', b'START_TIME="2026-079 17:02:00"', 1)

    # Sol 1649: two copies, different headers, identical body (> 64 KiB so that the tail of
    # the probe covers the body only); a UTC jump of about +6 h (17), QC=00000001 (14).
    for name in (
        "RAD_RDR_2017_085_15_54_1649_V00.TXT",
        "RAD_RDR_2017_085_16_28_1649_V00.TXT",
    ):
        head, blocks = _rad_pick(source_dir / name, {1: 60, 3: 60, 14: 60, 17: 60})
        files[name] = head + b"".join(blocks)

    for name, data in files.items():
        (out_dir / name).write_bytes(data)
        logger.info("wrote %s (%d bytes)", name, len(data))

    # CHECKSUMS.TXT: md5 of the trimmed files plus decoys (md5 of the real volume for the rest).
    lines = [
        f"{hashlib.md5(data).hexdigest()}  ./DATA/{RAD_VOLUME_FOLDERS[int(name[-12:-8])]}/{name}"
        for name, data in sorted(files.items())
    ]
    lines += [
        "2f163cee76c3eeb82e8825286680f873  ./DATA/SOL_00180_00269/"
        "RAD_RDR_2013_081_04_32_0222_V00.TXT",
        "3705ba26fdf486deb3f1d15fb39c89e3  ./DATA/SOL_00180_00269/"
        "RAD_RDR_2013_123_21_52_0222_V00.TXT",
        "ef47364389027e9d601d7d4a6e5f6f0e  ./DATA/SOL_00000_00089/"
        "RAD_RDR_2012_228_20_41_0010_V00.LBL",
        "7a3d810511291f6585fb6f1b3273d526  ./DATA/CRUISE/D_LETCNT.FMT",
        "0077b6b04eee7b13d925126720190938  ./DATA/CRUISE/RAD_RDR_2011_341_17_42_C011_V00.TXT",
        "923244344bc49a2395f29ced5dd0558b  ./DOCUMENT/RAD_RDR_SIS.HTM",
        "af10ea76e30a7ae0149ca852217f73b3  ./AAREADME.TXT",
        "this line is not a checksum entry",
    ]
    (out_dir / "CHECKSUMS.TXT").write_bytes(("\n".join(lines) + "\n").encode("ascii"))

    # TIMING_ERRORS.TXT: 2 Timing Error + 1 Date Range Error, one name is the sol 10 file.
    def block(kind: str, text: str, name: str, obs: int) -> str:
        crlf = "\r\n"
        return crlf.join(
            [
                f"***** {kind} *****",
                f"     {text}",
                f"     Found in File: {name}",
                f"     Observation: {obs}",
                "     Time of previous observation: 2012-229 07:50:45 (28245000) UTC",
                "     Time of current observation: 2013-123 21:52:00 (78720000) UTC",
                "",
            ]
        )

    back = "Time found running backward, this can not occur!"
    timing = (
        block("Timing Error", back, "RAD_RDR_2011_354_00_00_C024_V00.TXT", 136)
        + block("Timing Error", back, "RAD_RDR_2012_002_00_03_C037_V00.TXT", 2)
        + block(
            "Date Range Error",
            "The Observation Date was found to be Out-of-Range!",
            "RAD_RDR_2012_228_20_41_0010_V00.TXT",
            12,
        )
    )
    (out_dir / "TIMING_ERRORS.TXT").write_bytes(timing.encode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=None, help="dir for untrimmed dumps")
    parser.add_argument(
        "--part",
        choices=["all", "base", "m2020-range", "mer", "mer-img", "mmgis", "rad"],
        default="all",
        help="which group of requests to record (default: all); "
        "'mer' copies data/mer-samples into tests/fixtures/upstream/mer offline; "
        "'mer-img' downloads up to six MER .img samples (paths from the dev database)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.part == "mer":
        build_mer_fixtures()
        return
    if args.part == "mer-img":
        record_mer_images()
        return
    if args.part == "mmgis":
        build_mmgis_fixtures()
        return
    if args.part == "rad":
        build_rad_fixtures()
        return
    settings = Settings()

    summary: dict[str, Any] = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=TIMEOUT_S, follow_redirects=True) as client:
        rec = Recorder(
            client,
            args.raw_dir,
            m20_url=settings.m2020_feed_url,
            msl_url=settings.msl_feed_url,
        )
        if args.part in ("all", "base"):
            record_m20(rec, summary)
            record_msl(rec, summary)
        if args.part in ("all", "m2020-range"):
            record_m20_range(rec, summary)
        summary["request_count"] = rec.request_count

    if args.raw_dir is not None:
        write_json(args.raw_dir / f"summary_{args.part}.json", summary)
    # Final human-readable summary.
    sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    main()
