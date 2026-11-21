"""`stored_meta` over the projection of `extra` equals `stored_meta` over the full `extra`.

The backfill reads only `photo_meta.STORED_META_PATHS` of `extra` (not the whole ~1 KB JSONB), so
every path that `frame_size` / `local_time` read must be in that list. The projection built here
mirrors the SQL one: nested objects are rebuilt, a missing path or a non-object parent gives null.
The inputs are the `extra` of every recorded and synthetic fixture (all five rovers' adapters);
each extra is tried with every rover id, so a path read for any rover must be exported.
"""

from pathlib import Path
from typing import Any

import pytest

from app.sources import mer
from app.sources.ingenuity import parse_ingenuity_image
from app.sources.m2020 import parse_m2020_image
from app.sources.msl import parse_msl_item
from app.sources.photo_meta import STORED_META_PATHS, stored_meta
from tests.unit._mer import MER_FIXTURES
from tests.unit._upstream import load_fixture

pytestmark = pytest.mark.unit

ROVERS = ["curiosity", "perseverance", "ingenuity", "spirit", "opportunity"]
MER_BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/mer/"


def project(extra: Any, paths: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for path in paths:
        node = tree
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = None
    return _fill(tree, extra)


def _fill(level: dict[str, Any], source: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, child in level.items():
        value = source.get(key) if isinstance(source, dict) else None
        out[key] = _fill(child, value) if isinstance(child, dict) else value
    return out


def _records(items: list[dict[str, Any]], parse: Any) -> list[dict[str, Any]]:
    return [r.extra for r in (parse(i) for i in items) if r is not None]


def fixture_extras() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for name in (
        "m2020/list_page0",
        "m2020/sol_2029",
        "m2020/range_1000_page0",
        "synthetic/m2020_quirks",
    ):
        out += [
            (name, e) for e in _records(load_fixture(f"{name}.json")["images"], parse_m2020_image)
        ]
    for name in ("ingenuity/list_page0", "synthetic/ingenuity_quirks"):
        out += [
            (name, e)
            for e in _records(load_fixture(f"{name}.json")["images"], parse_ingenuity_image)
        ]
    for name in (
        "msl/sol_4327_page0",
        "msl/sol_4327_page1",
        "msl/sol_4327_page2",
        "synthetic/msl_quirks",
    ):
        out += [(name, e) for e in _records(load_fixture(f"{name}.json")["items"], parse_msl_item)]
    for rover in ("spirit", "opportunity"):
        index = mer.load_rover_index(Path(MER_FIXTURES), rover)
        for rows in index.rows.values():
            out += [(f"mer/{rover}", r.extra) for r in mer.sol_records(rows, MER_BASE_URL)]
    out += [
        ("hand/empty", {}),
        ("hand/extended-string", {"extended": "UNK", "date_taken_mars": "Sol-00001M01:02:03"}),
        ("hand/extended-null", {"extended": None, "local_true_solar_time": "01:02:03"}),
        ("hand/all-paths", {
            "extended": {"dimension": "(10,20)", "lmst": "Sol-00001M04:05:06"},
            "date_taken_mars": "Sol-00001M07:08:09",
            "local_true_solar_time": "10:11:12",
            "line_samples": 30,
            "lines": 40,
        }),
    ]  # fmt: skip
    return out


EXTRAS = fixture_extras()


def test_fixture_extras_are_not_vacuous() -> None:
    sources = {name.split("/")[0] for name, _ in EXTRAS}
    assert {"m2020", "ingenuity", "msl", "synthetic", "mer", "hand"} <= sources
    assert len(EXTRAS) > 40
    # Some of them really carry data in the projected paths.
    assert any(project(e, STORED_META_PATHS)["date_taken_mars"] for _, e in EXTRAS)
    assert any(project(e, STORED_META_PATHS)["extended"]["lmst"] for _, e in EXTRAS)
    assert any(project(e, STORED_META_PATHS)["line_samples"] for _, e in EXTRAS)


@pytest.mark.parametrize("rover", ROVERS)
def test_stored_meta_of_the_projection_equals_stored_meta_of_the_full_extra(rover: str) -> None:
    for name, extra in EXTRAS:
        assert stored_meta(rover, project(extra, STORED_META_PATHS)) == stored_meta(rover, extra), (
            name,
            rover,
        )


def test_stored_meta_paths_are_tuples_of_keys() -> None:
    assert STORED_META_PATHS
    assert all(
        isinstance(p, tuple) and p and all(isinstance(k, str) for k in p) for p in STORED_META_PATHS
    )
