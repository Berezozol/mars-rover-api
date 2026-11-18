"""Contract tests for app.sources.mer (stage 8: Spirit and Opportunity from the PDS index).

Inputs are real index rows in tests/fixtures/upstream/mer/ (see tests/unit/_mer.py). The
oracle ``reference_frames`` reads the contract independently; spot checks pin it to the
data. Requirements: docs/proposals/stage-8-mer-import.md (sections 2 and 4) and the
verified facts of docs/DATA-SOURCES.md ("Spirit и Opportunity"), which win over the plan:
ROW_BYTES/START_BYTE are wrong, ROVER_MOTION_COUNTER is 5 tab fields, Descent has no
PATH_NAME/FILE_NAME, rows are not sorted, a few Opportunity PRODUCT_IDs are listed twice.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.sources import mer
from app.sources.base import PhotoRecord
from app.sources.http import UpstreamError
from tests.unit._mer import (
    CREDITS,
    FIXTURE_ROWS,
    FRAMES_BY_CAMERA,
    FRAMES_TOTAL,
    FULL_FRAMES,
    MAX_INDEX_SOL,
    MAX_PHOTO_SOL,
    MER_FIXTURES,
    OPPORTUNITY_REPEATED,
    ROWS_TOTAL,
    SPIRIT_MERGED_KEPT,
    SPIRIT_MERGED_PRODUCTS,
    SPIRIT_MERGED_SOL,
    VOLUMES,
    build_adapter,
    copy_cache,
    received_by_sol,
    reference_frames,
    rewrite_rows,
)

pytestmark = pytest.mark.unit

ROVERS = ["spirit", "opportunity"]
BASE = "https://planetarydata.jpl.nasa.gov/img/data/mer"


async def fetch(adapter: Any, from_sol: int, to_sol: int) -> tuple[list[tuple[int, list]], Any]:
    stream = adapter.fetch_sols(from_sol, to_sol)
    batches = [(sol, list(records)) async for sol, records in stream]
    return batches, stream.stats


async def all_records(rover: str, cache_dir: Path = MER_FIXTURES, **kw: Any) -> list[PhotoRecord]:
    adapter = build_adapter(rover, cache_dir, **kw)
    batches, _ = await fetch(adapter, 0, MAX_INDEX_SOL[rover])
    return [record for _, records in batches for record in records]


def by_source_id(records: list[PhotoRecord]) -> dict[str, PhotoRecord]:
    result = {record.source_id: record for record in records}
    assert len(result) == len(records), "duplicate source_id"
    return result


def row(product_id: str, *, creation: str = "2005-01-01T00:00:00Z", **kw: Any) -> mer.IndexRow:
    values: dict[str, Any] = {
        "volume_id": "mer2no_0xxx",
        "product_id": product_id,
        "sol": 4,
        "instrument_id": "NAVCAM_LEFT",
        "path_name": "/mer2no_0xxx/data/sol0004/edr/",
        "file_name": product_id.lower() + ".img",
        "creation_time": creation,
        "start_time": "2004-01-07T02:30:01Z",
        "received_time": "2004-01-07T03:00:00Z",
        "sclk": "126714695.123",
        "sequence_id": "p1500",
        "local_true_solar_time": "12:00:00",
        "lines": 1024,
        "line_samples": 1024,
        "frame_type": "MONO",
        "filter_name": "MI_OPEN",
        "motion_counter": (2, 0, 2, 1499, 42),
    }
    values.update(kw)
    return mer.IndexRow(**values)


# --- label -------------------------------------------------------------------------------


def _label(volume: str) -> str:
    return (MER_FIXTURES / volume / "index" / "edrindex.lbl").read_text(encoding="latin-1")


@pytest.mark.parametrize(
    ("volume", "columns", "fields"),
    [
        ("mer2no_0xxx", 55, 59),
        ("mer1ho_0xxx", 55, 59),
        ("mer1po_0xxx", 55, 59),
        ("mer2mo_0xxx", 55, 59),
        ("mer1do_0xxx", 48, 52),
        ("mer2do_0xxx", 48, 52),
    ],
)
def test_parse_label_motion_counter_items_expand_to_five_fields(
    volume: str, columns: int, fields: int
) -> None:
    label = mer.parse_label(_label(volume))

    assert label.rows == FIXTURE_ROWS[volume]
    assert label.field_count == fields
    assert len(label.fields) == columns
    start, items = label.fields["ROVER_MOTION_COUNTER"]
    assert items == 5
    # Columns after the counter are shifted by the 4 extra fields.
    assert label.index("FLAT_FIELD_CORRECTION_FLAG") == start + 5


@pytest.mark.parametrize("volume", ["mer2no_0xxx", "mer1do_0xxx"])
def test_parse_label_every_fixture_row_has_field_count_fields(volume: str) -> None:
    label = mer.parse_label(_label(volume))
    table = (MER_FIXTURES / volume / "index" / "edrindex.tab").read_bytes().decode("latin-1")
    lines = table.split("\r\n")[:-1]

    assert len(lines) == label.rows
    assert {len(mer.split_row(line)) for line in lines} == {label.field_count}


def test_parse_label_descent_has_no_path_columns_but_names_resolve() -> None:
    label = mer.parse_label(_label("mer2do_0xxx"))

    assert label.index("PATH_NAME") is None
    assert label.index("FILE_NAME") is None
    for name in ("PRODUCT_ID", "PLANET_DAY_NUMBER", "START_TIME", "INSTRUMENT_ID"):
        assert label.index(name) is not None, name


def test_parse_label_ignores_row_bytes_and_start_byte() -> None:
    """ROW_BYTES = 933 and START_BYTE are the label's claims; rows are ~910 bytes."""
    text = _label("mer2no_0xxx")
    assert "ROW_BYTES                   = 933" in text
    garbled = text.replace("ROW_BYTES                   = 933", "ROW_BYTES = 1").replace(
        "START_BYTE", "START_BYTE_IGNORED"
    )

    assert mer.parse_label(garbled) == mer.parse_label(text)


def test_split_row_strips_quotes_and_padding_keeps_empty_values() -> None:
    assert mer.split_row('"mer2no_0xxx"\t"  A B  "\t4    \t""\t\r\n') == [
        "mer2no_0xxx",
        "A B",
        "4",
        "",
        "",
    ]


# --- product type, frame key, product choice --------------------------------------------


def test_frame_key_drops_type_creator_and_version() -> None:
    assert mer.frame_key("1N128285132EDN0000P1500L0M1") == "1N1282851320000P1500L0"
    assert mer.frame_key("1N128285132EFF0000P1500L0M2") == mer.frame_key(
        "1N128285132EDN0000P1500L0M1"
    )


def test_frame_key_differs_by_eye_and_sclk() -> None:
    assert mer.frame_key("1N128285132EFF0000P1500L0M1") != mer.frame_key(
        "1N128285132EFF0000P1500R0M1"
    )
    assert mer.frame_key("1M215179576ESF76POP0068M1M1") != mer.frame_key(
        "1M215179665ESF76POP0068M1M1"
    )


@pytest.mark.parametrize(
    ("products", "expected"),
    [
        (["2N1EDN0000P1500L0M1", "2N1ESF0000P1500L0M1", "2N1EFF0000P1500L0M1"], "EFF"),
        (["2N1EDN0000P1500L0M1", "2N1ESF0000P1500L0M1"], "ESF"),
        (["2N1EDN0000P1500L0M2", "2N1ESF0000P1500L0M1"], "ESF"),
        (["2N1EDN0000P1500L0M1"], "EDN"),
    ],
    ids=["eff-wins", "esf-over-edn", "type-before-version", "edn-alone"],
)
def test_pick_product_prefers_eff_then_esf_then_edn(products: list[str], expected: str) -> None:
    pids = [p.replace("2N1", "2N126714695") for p in products]

    chosen = mer.pick_product([row(pid) for pid in pids])

    assert mer.product_type(chosen.product_id) == expected


def test_pick_product_same_type_higher_version_wins() -> None:
    m1, m2 = "2N126714695EFF0000P1500L0M1", "2N126714695EFF0000P1500L0M2"

    assert mer.pick_product([row(m2), row(m1)]).product_id == m2
    assert mer.pick_product([row(m1), row(m2)]).product_id == m2


def test_pick_product_is_deterministic_regardless_of_order() -> None:
    rows = [
        row("2N126714695EDN0000P1500L0M1"),
        row("2N126714695ESF0000P1500L0M1"),
        row("2N126714695ESF0000P1500L0N1"),
    ]

    picks = {
        mer.pick_product(order).product_id for order in (rows, rows[::-1], rows[1:] + rows[:1])
    }

    assert len(picks) == 1


def test_pick_product_same_product_id_later_creation_time_wins() -> None:
    pid = "1F447958540EFFCBCRP1214L0M1"
    early = row(pid, creation="2014-10-08T22:22:50Z", received_time="2001-01-01T00:00:00Z")
    late = row(pid, creation="2015-07-14T23:50:33Z", received_time="2014-03-13T08:04:34Z")

    assert mer.pick_product([early, late]) is late
    assert mer.pick_product([late, early]) is late


# --- URLs --------------------------------------------------------------------------------


@pytest.mark.parametrize("base", [BASE, BASE + "/"], ids=["no-slash", "slash"])
def test_browse_and_product_url_from_path_and_file_name(base: str) -> None:
    r = row(
        "2N160633049EDNA2K1F0006R0M1",
        path_name="/mer2no_0xxx/data/sol0386/edr/",
        file_name="2n160633049edna2k1f0006r0m1.img",
    )

    assert mer.browse_url(base, r) == (
        f"{BASE}/mer2no_0xxx/browse/sol0386/edr/2n160633049edna2k1f0006r0m1.img.jpg"
    )
    assert mer.product_url(base, r) == (
        f"{BASE}/mer2no_0xxx/data/sol0386/edr/2n160633049edna2k1f0006r0m1.img"
    )


def test_browse_url_descent_built_from_product_id() -> None:
    r = row(
        "2E126462398EDN0000F0006N0M1",
        volume_id="mer2do_0xxx",
        sol=1,
        instrument_id="DESCAM",
        path_name=None,
        file_name=None,
    )

    assert mer.browse_url(BASE, r) == (
        f"{BASE}/mer2do_0xxx/browse/sol0001/edr/2e126462398edn0000f0006n0m1.img.jpg"
    )
    assert mer.product_url(BASE, r) == (
        f"{BASE}/mer2do_0xxx/data/sol0001/edr/2e126462398edn0000f0006n0m1.img"
    )


@pytest.mark.parametrize("camera", sorted(CREDITS))
def test_camera_credit_per_camera(camera: str) -> None:
    assert mer.camera_credit(camera) == CREDITS[camera]


# --- adapter: records --------------------------------------------------------------------


@pytest.mark.parametrize("rover", ROVERS)
async def test_adapter_records_match_contract_for_every_frame(rover: str) -> None:
    records = by_source_id(await all_records(rover))
    expected = {frame.source_id: frame for frame in reference_frames(rover)}

    assert set(records) == set(expected)
    for source_id, frame in expected.items():
        record = records[source_id]
        assert record.sol == frame.sol, source_id
        assert record.camera_code == frame.camera_code, source_id
        assert record.sample_type == frame.sample_type, source_id
        assert record.taken_at == frame.taken_at, source_id
        assert record.taken_at.tzinfo is not None
        assert record.earth_date == frame.taken_at.date(), source_id
        assert record.received_at == frame.received_at, source_id
        assert record.img_full == frame.img_full, source_id
        assert record.source_link == frame.source_link, source_id
        assert (record.img_small, record.img_medium, record.img_large) == (None, None, None)
        assert (record.title, record.caption) == (None, None)
        assert record.credit == frame.credit, source_id
        assert (record.site, record.drive) == (frame.site, frame.drive), source_id
        assert set(record.extra["products"]) == frame.products, source_id
        assert record.extra["instrument_id"] == frame.instrument_id, source_id
        assert None not in record.extra.values(), source_id


@pytest.mark.parametrize("rover", ROVERS)
async def test_adapter_counts_per_camera_and_full_frames(rover: str) -> None:
    records = await all_records(rover)

    assert len(records) == FRAMES_TOTAL[rover]
    assert Counter(r.camera_code for r in records) == FRAMES_BY_CAMERA[rover]
    assert sum(r.sample_type == "full" for r in records) == FULL_FRAMES[rover]
    assert max(r.sol for r in records) == MAX_PHOTO_SOL[rover]


@pytest.mark.parametrize("rover", ROVERS)
async def test_adapter_drops_thumbnails_and_engineering_products(rover: str) -> None:
    records = await all_records(rover)
    types = {r.source_id[11:14] for r in records}

    assert types <= {"EFF", "ESF", "EDN"}
    assert {r.sample_type for r in records} <= {"full", "subframe", "downsampled"}


async def test_adapter_type_maps_to_sample_type() -> None:
    records = by_source_id(await all_records("spirit"))

    assert records["2N161703572EFFA500P0874L0M1"].sample_type == "full"
    assert records["2N169236851ESFAAB2P1560L0M1"].sample_type == "subframe"
    assert records["2N160633049EDNA2K1F0006R0M1"].sample_type == "downsampled"


@pytest.mark.parametrize(
    ("rover", "source_id", "camera", "eye"),
    [
        ("spirit", "2F133383579EDN2232P1141R0M1", "FHAZ", "R"),
        ("spirit", "2R294863587ESFB1D1P1344L0M1", "RHAZ", "L"),
        ("opportunity", "1R170621134ESF55NSP1344R0M1", "RHAZ", "R"),
        ("spirit", "2N161703572EFFA500P0874L0M1", "NAVCAM", "L"),
        ("opportunity", "1P140611395ESF3182P2111L3M1", "PANCAM", "L"),
        ("opportunity", "1M192379108EFF64KWP2956M2M1", "MI", None),
        ("spirit", "2E126462398EDN0000F0006N0M1", "ENTRY", None),
    ],
)
async def test_adapter_camera_by_instrument_and_eye_in_extra(
    rover: str, source_id: str, camera: str, eye: str | None
) -> None:
    record = by_source_id(await all_records(rover))[source_id]

    assert record.camera_code == camera
    assert record.extra.get("eye") == eye


async def test_adapter_hazcam_volume_splits_front_and_rear() -> None:
    records = await all_records("opportunity")
    hazcam = [r for r in records if r.source_id[1] in "FR"]

    assert {r.camera_code for r in hazcam if r.source_id[1] == "F"} == {"FHAZ"}
    assert {r.camera_code for r in hazcam if r.source_id[1] == "R"} == {"RHAZ"}


async def test_adapter_spot_check_spirit_navcam_record() -> None:
    record = by_source_id(await all_records("spirit"))["2N160633049EDNA2K1F0006R0M1"]

    assert record.sol == 386
    assert record.camera_code == "NAVCAM"
    assert record.sample_type == "downsampled"
    assert record.taken_at == datetime(2005, 2, 2, 16, 12, 0, tzinfo=UTC)
    assert record.earth_date == date(2005, 2, 2)
    assert record.img_full == (
        f"{BASE}/mer2no_0xxx/browse/sol0386/edr/2n160633049edna2k1f0006r0m1.img.jpg"
    )
    assert (
        record.source_link == f"{BASE}/mer2no_0xxx/data/sol0386/edr/2n160633049edna2k1f0006r0m1.img"
    )
    assert record.credit == "NASA/JPL-Caltech"
    assert (record.site, record.drive) == (102, 461)
    assert record.extra["products"] == ["2N160633049EDNA2K1F0006R0M1"]


@pytest.mark.parametrize(
    ("rover", "volume", "day"), [("spirit", "mer2do_0xxx", 4), ("opportunity", "mer1do_0xxx", 25)]
)
async def test_adapter_descent_frames_are_entry_on_sol_1(rover: str, volume: str, day: int) -> None:
    entry = [r for r in await all_records(rover) if r.camera_code == "ENTRY"]

    assert len(entry) == 3
    for record in entry:
        assert record.sol == 1
        assert record.sample_type == "downsampled"
        assert record.earth_date == date(2004, 1, day)
        lower = record.source_id.lower()
        assert record.img_full == f"{BASE}/{volume}/browse/sol0001/edr/{lower}.img.jpg"
        assert record.source_link == f"{BASE}/{volume}/data/sol0001/edr/{lower}.img"
        assert record.credit == "NASA/JPL-Caltech"


async def test_adapter_merges_esf_and_edn_of_one_frame() -> None:
    records = [r for r in await all_records("spirit") if r.sol == SPIRIT_MERGED_SOL]

    assert [r.source_id for r in records] == [SPIRIT_MERGED_KEPT]
    assert records[0].sample_type == "subframe"
    assert sorted(records[0].extra["products"]) == sorted(SPIRIT_MERGED_PRODUCTS)


async def test_adapter_repeated_product_id_yields_one_record() -> None:
    records = await all_records("opportunity")
    ids = Counter(r.source_id for r in records)

    for sol, (product_id, _) in OPPORTUNITY_REPEATED.items():
        assert ids[product_id] == 1, sol


@pytest.mark.parametrize("sol", sorted(OPPORTUNITY_REPEATED))
async def test_adapter_repeated_product_id_keeps_later_creation_row(
    tmp_path: Path, sol: int
) -> None:
    """Rows of a re-released product differ only in PRODUCT_CREATION_TIME and the telemetry
    file; make the earlier row's EARTH_RECEIVED_START_TIME visibly wrong to see which row
    was used (sol 3602: the later row comes second, sol 3757: first)."""
    product_id, later = OPPORTUNITY_REPEATED[sol]
    cache = copy_cache(tmp_path, ("opportunity",))
    volume = next(v for v in VOLUMES["opportunity"] if v.startswith("mer1" + _kind(product_id)))
    frame = next(f for f in reference_frames("opportunity") if f.source_id == product_id)
    received = frame.row["EARTH_RECEIVED_START_TIME"].encode()

    def spoil_earlier(lines: list[bytes]) -> list[bytes]:
        out = []
        for line in lines:
            if product_id.encode() in line and later.encode() not in line:
                assert received in line
                line = line.replace(received, b"1999-01-01T00:00:00Z")
            out.append(line)
        return out

    rewrite_rows(cache, volume, spoil_earlier)
    record = by_source_id(await all_records("opportunity", cache))[product_id]

    assert record.received_at == frame.received_at


def _kind(product_id: str) -> str:
    return {"N": "no", "F": "ho", "R": "ho", "P": "po", "M": "mo", "E": "do"}[product_id[1]]


async def test_adapter_base_url_comes_from_settings() -> None:
    records = await all_records("spirit", base_url="https://mirror.example.test/mer/")
    record = by_source_id(records)["2N160633049EDNA2K1F0006R0M1"]

    assert record.img_full == (
        "https://mirror.example.test/mer/mer2no_0xxx/browse/sol0386/edr/"
        "2n160633049edna2k1f0006r0m1.img.jpg"
    )


# --- adapter: order, ranges, statistics --------------------------------------------------


@pytest.mark.parametrize("rover", ROVERS)
async def test_fetch_sols_descending_each_sol_once_unique_ids(rover: str) -> None:
    batches, _ = await fetch(build_adapter(rover, MER_FIXTURES), 0, MAX_INDEX_SOL[rover])
    sols = [sol for sol, _ in batches]

    assert sols == sorted(set(sols), reverse=True)
    assert sols == sorted({f.sol for f in reference_frames(rover)}, reverse=True)
    for sol, records in batches:
        assert records, sol
        assert {r.sol for r in records} == {sol}
        assert len({r.source_id for r in records}) == len(records)


async def test_fetch_sols_unsorted_rows_still_descending_and_grouped(tmp_path: Path) -> None:
    """Rows reversed and interleaved: a sol split across the file and across volumes
    (Spirit sol 483: Navcam + Pancam) is still yielded once with all its photos."""
    cache = copy_cache(tmp_path, ("spirit",))
    for volume in VOLUMES["spirit"]:
        rewrite_rows(cache, volume, lambda lines: lines[1::2][::-1] + lines[0::2])

    batches, _ = await fetch(build_adapter("spirit", cache), 0, MAX_INDEX_SOL["spirit"])
    sols = [sol for sol, _ in batches]

    assert sols == sorted(set(sols), reverse=True)
    sol_483 = dict(batches)[483]
    assert sorted(r.camera_code for r in sol_483) == ["NAVCAM", "PANCAM"]
    assert sum(len(records) for _, records in batches) == FRAMES_TOTAL["spirit"]


async def test_fetch_sols_range_limits_sols() -> None:
    batches, stats = await fetch(build_adapter("spirit", MER_FIXTURES), 400, 500)

    assert [sol for sol, _ in batches] == [493, 483, 444, 434, 432]
    assert stats.complete
    assert set(stats.sols) == set(range(400, 501))


@pytest.mark.parametrize("rover", ROVERS)
async def test_fetch_stats_received_counts_every_index_row_of_the_sol(rover: str) -> None:
    batches, stats = await fetch(build_adapter(rover, MER_FIXTURES), 0, MAX_INDEX_SOL[rover])
    delivered = {sol: len(records) for sol, records in batches}

    assert stats.complete
    received = received_by_sol(rover)
    for sol in range(MAX_INDEX_SOL[rover] + 1):
        sol_stats = stats.sols[sol]
        assert sol_stats.received == received.get(sol, 0), sol
        assert sol_stats.dropped == sol_stats.received - delivered.get(sol, 0), sol
        assert sol_stats.source_total is None, sol
    assert sum(s.received for s in stats.sols.values()) == ROWS_TOTAL[rover]


async def test_fetch_stats_sol_with_only_engineering_rows_is_all_dropped() -> None:
    """Spirit sol 2116 has one ECS row only; sol 444 has an EDN and an ETH."""
    batches, stats = await fetch(build_adapter("spirit", MER_FIXTURES), 2116, 2116)

    assert batches == []
    assert (stats.sols[2116].received, stats.sols[2116].dropped) == (1, 1)

    batches, stats = await fetch(build_adapter("spirit", MER_FIXTURES), 444, 444)
    assert [len(records) for _, records in batches] == [1]
    assert (stats.sols[444].received, stats.sols[444].dropped) == (2, 1)


@pytest.mark.parametrize(("from_sol", "to_sol"), [(5, 4), (-1, 3)])
async def test_fetch_sols_invalid_range_raises_value_error(from_sol: int, to_sol: int) -> None:
    with pytest.raises(ValueError):
        await fetch(build_adapter("spirit", MER_FIXTURES), from_sol, to_sol)


@pytest.mark.parametrize("rover", ROVERS)
async def test_latest_sol_is_max_sol_of_the_index(rover: str) -> None:
    assert await build_adapter(rover, MER_FIXTURES).latest_sol() == MAX_INDEX_SOL[rover]


@pytest.mark.parametrize("rover", ROVERS)
async def test_source_total_is_sum_of_label_rows(rover: str) -> None:
    assert await build_adapter(rover, MER_FIXTURES).source_total() == ROWS_TOTAL[rover]


# --- cache missing -----------------------------------------------------------------------


Call = Callable[[Any], Any]


async def _latest(adapter: Any) -> Any:
    return await adapter.latest_sol()


async def _total(adapter: Any) -> Any:
    return await adapter.source_total()


async def _fetch_all(adapter: Any) -> Any:
    return await fetch(adapter, 0, 10)


@pytest.mark.parametrize("call", [_latest, _total, _fetch_all], ids=["latest", "total", "fetch"])
async def test_missing_cache_raises_mer_cache_missing(tmp_path: Path, call: Call) -> None:
    adapter = build_adapter("spirit", tmp_path / "empty")

    with pytest.raises(mer.MerCacheMissing) as info:
        await call(adapter)

    assert isinstance(info.value, UpstreamError)
    assert "mer-download" in str(info.value)


async def test_one_missing_index_file_raises_mer_cache_missing(tmp_path: Path) -> None:
    cache = copy_cache(tmp_path, ("spirit",))
    (cache / "mer2po_0xxx" / "index" / "edrindex.tab").unlink()

    with pytest.raises(mer.MerCacheMissing) as info:
        await build_adapter("spirit", cache).latest_sol()

    assert any("mer2po_0xxx" in str(path) for path in info.value.missing)


def test_rover_without_mer_volumes_is_rejected() -> None:
    with pytest.raises(ValueError):
        mer.volume_ids("curiosity")


# --- robustness of the index reader (stage 8 review fixes) ---------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPOT_VOLUME = "mer2no_0xxx"
SPOT_PRODUCT = "2N160633049EDNA2K1F0006R0M1"
SPOT_SOL = 386
SPOT_PATH = rb'"/mer2no_0xxx/data/sol0386/edr/ *"'
SPOT_FILE = rb'"2n160633049edna2k1f0006r0m1\.img *"'


def _rewrite_spot(cache: Path, edit: Callable[[bytes], bytes]) -> None:
    def transform(lines: list[bytes]) -> list[bytes]:
        out = []
        hits = 0
        for line in lines:
            if SPOT_PRODUCT.encode() in line:
                new = edit(line)
                assert new != line, "the edit did not apply"
                line = new
                hits += 1
            out.append(line)
        assert hits == 1
        return out

    rewrite_rows(cache, SPOT_VOLUME, transform)


@pytest.mark.parametrize(
    ("path", "file"),
    [
        pytest.param(b'"N/A"', None, id="path-not-a-path"),
        pytest.param(b'"/mer2no_0xxx/data/../../../../etc/"', None, id="path-traversal"),
        pytest.param(None, b'"garbage"', id="file-not-the-product"),
        pytest.param(None, b'"../../x.img"', id="file-traversal"),
        pytest.param(b'"N/A"', b'"N/A"', id="both-bad"),
    ],
)
async def test_adapter_bad_path_or_file_name_falls_back_to_product_id_path(
    tmp_path: Path, path: bytes | None, file: bytes | None
) -> None:
    original = by_source_id(await all_records("spirit"))[SPOT_PRODUCT]
    cache = copy_cache(tmp_path, ("spirit",))

    def edit(line: bytes) -> bytes:
        if path is not None:
            line = re.sub(SPOT_PATH, path, line, count=1)
        if file is not None:
            line = re.sub(SPOT_FILE, file, line, count=1)
        return line

    _rewrite_spot(cache, edit)
    record = by_source_id(await all_records("spirit", cache))[SPOT_PRODUCT]

    # The original row's path is exactly the product-id path, so the URLs must not change.
    assert (
        record.img_full
        == original.img_full
        == (f"{BASE}/mer2no_0xxx/browse/sol0386/edr/2n160633049edna2k1f0006r0m1.img.jpg")
    )
    assert (
        record.source_link
        == original.source_link
        == (f"{BASE}/mer2no_0xxx/data/sol0386/edr/2n160633049edna2k1f0006r0m1.img")
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(b'"MARS  "', b'"MARS \r"', id="in-padding"),
        pytest.param(b'"EXTENDED MISSION', b'"EXTENDED\rMISSION', id="inside-value"),
    ],
)
async def test_adapter_lone_cr_inside_field_does_not_break_parsing(
    tmp_path: Path, old: bytes, new: bytes
) -> None:
    """A stray CR (not followed by LF) inside a quoted field is not a row terminator."""
    original = await all_records("spirit")
    cache = copy_cache(tmp_path, ("spirit",))
    _rewrite_spot(cache, lambda line: line.replace(old, new, 1))

    records = await all_records("spirit", cache)

    assert by_source_id(records) == by_source_id(original)
    _, stats = await fetch(build_adapter("spirit", cache), SPOT_SOL, SPOT_SOL)
    _, stats_original = await fetch(build_adapter("spirit", MER_FIXTURES), SPOT_SOL, SPOT_SOL)
    assert stats.sols[SPOT_SOL] == stats_original.sols[SPOT_SOL]
    assert stats.unattributed_dropped == stats_original.unattributed_dropped


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param(SPOT_PRODUCT[:-1], id="26-chars"),
        pytest.param(SPOT_PRODUCT + "X", id="28-chars"),
    ],
)
async def test_adapter_product_id_of_wrong_length_is_dropped_and_counted_apart(
    tmp_path: Path, bad_id: str
) -> None:
    cache = copy_cache(tmp_path, ("spirit",))
    _rewrite_spot(
        cache,
        lambda line: line.replace(f'"{SPOT_PRODUCT}"'.encode(), f'"{bad_id}"'.encode(), 1),
    )

    batches, stats = await fetch(build_adapter("spirit", cache), SPOT_SOL, SPOT_SOL)
    ids = {r.source_id for _, records in batches for r in records}

    assert SPOT_PRODUCT not in ids and bad_id not in ids
    sol_stats = stats.sols[SPOT_SOL]
    assert sol_stats.received == received_by_sol("spirit")[SPOT_SOL]
    assert sol_stats.dropped == sol_stats.received - len(ids)

    # Counted on its own, not as an unknown instrument (NAVCAM_RIGHT is a known one).
    index = mer.load_rover_index(cache, "spirit")
    assert "NAVCAM_RIGHT" not in index.unknown_instruments
    assert sum(index.unknown_instruments.values()) == sum(
        mer.load_rover_index(MER_FIXTURES, "spirit").unknown_instruments.values()
    )


async def test_adapter_relative_cache_dir_resolves_against_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    relative = MER_FIXTURES.relative_to(PROJECT_ROOT)

    records = await all_records("spirit", relative)

    assert len(records) == FRAMES_TOTAL["spirit"]


async def test_adapter_uppercase_path_and_file_name_accepted_url_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Upper-case PATH_NAME/FILE_NAME are valid values: no fallback, same (lower-case) URLs."""
    original = by_source_id(await all_records("spirit"))[SPOT_PRODUCT]
    cache = copy_cache(tmp_path, ("spirit",))

    def upper(line: bytes) -> bytes:
        line = re.sub(SPOT_PATH, lambda m: m.group(0).upper(), line, count=1)
        return re.sub(SPOT_FILE, lambda m: m.group(0).upper(), line, count=1)

    _rewrite_spot(cache, upper)
    caplog.set_level(logging.WARNING)
    record = by_source_id(await all_records("spirit", cache))[SPOT_PRODUCT]

    assert record.img_full == original.img_full
    assert record.source_link == original.source_link
    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING and SPOT_PRODUCT in r.getMessage()
    ]
