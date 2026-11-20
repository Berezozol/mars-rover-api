"""Shared helpers for the stage-13 (Perseverance samples) tests.

Fixtures: ``tests/fixtures/upstream/samples/rock-samples-trimmed.html`` (the real NASA page cut
down to the head with ``article:modified_time`` and the table with rows 1, 8, 17, 25, 26, 29, 30)
and ``tests/fixtures/upstream/synthetic/samples_quirks.html`` (hand-written quirks). Variants for
the refusal cases are built in code from the trimmed page (`replace_in_page`, `build_page`).
Database helpers write ``rover_samples`` / ``sample_datasets`` with plain SQL (columns per
docs/proposals/stage-13-samples.md section 5), so API tests do not depend on repository code.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "upstream"
SAMPLES_URL = "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/"
ROVER = "perseverance"
SOURCE_MODIFIED = datetime(2026, 6, 20, 16, 45, 46, tzinfo=UTC)
SOURCE_MODIFIED_JSON = "2026-06-20T16:45:46Z"

HEADERS = [
    "Sample No.",
    "Date Sealed",
    "Sol Sealed",
    "Sample Name",
    "Feature Name",
    "Sampling Location",
    "Current Location",
    "Science Notes&nbsp;",
    "Sample Type",
    "Sample Height<br>cm inch",
    "Rock Type",
]
RECORD_FIELDS = [
    "number",
    "name",
    "feature_name",
    "sampling_location",
    "current_location",
    "status",
    "sample_type",
    "sample_type_label",
    "rock_type",
    "sealed_on",
    "sol",
    "height_cm",
    "height_note",
    "dossier_url",
]
# Keys of the Sample object of API v2, in contract order.
SAMPLE_KEYS = [
    "number",
    "name",
    "feature_name",
    "sampling_location",
    "status",
    "current_location",
    "sample_type",
    "sample_type_label",
    "rock_type",
    "sealed_on",
    "sol",
    "height_cm",
    "height_note",
    "dossier_url",
    "total_photos",
    "location",
]
SAMPLES_RESPONSE_KEYS = ["rover", "source_url", "source_updated_at", "updated_at", "items"]
SAMPLE_LOCATION_KEYS = ["sol", "site", "drive", "lat", "lon", "elev_geoid", "match"]

PDS = "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/initial_reports"
NUMBERS = [1, 8, 17, 25, 26, 29, 30]

# Reference values of the trimmed page (stage-13 proposal, section 6); height_cm as float,
# sealed_on as ISO text.
EXPECTED: dict[int, dict[str, Any]] = {
    1: {
        "number": 1,
        "name": "Roubion",
        "feature_name": "Roubion",
        "sampling_location": "Polygon Valley",
        "current_location": "Sample Depot",
        "status": "depot",
        "sample_type": "atmospheric",
        "sample_type_label": "Atmospheric",
        "rock_type": None,
        "sealed_on": "2021-08-06",
        "sol": 164,
        "height_cm": None,
        "height_note": None,
        "dossier_url": f"{PDS}/initial_reports_volume1.pdf#page=16",
    },
    8: {
        "number": 8,
        "name": 'Ha\'ahóni (aka "Hahonih")',
        "feature_name": "Sid",
        "sampling_location": "Octavia E. Butler Landing / Ch’ał outcrop",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Igneous",
        "sealed_on": "2022-03-07",
        "sol": 371,
        "height_cm": 6.5,
        "height_note": None,
        "dossier_url": f"{PDS}/initial_reports_volume1.pdf#page=128",
    },
    17: {
        "number": 17,
        "name": "Atmo Mountain",
        "feature_name": "Observation Mountain",
        "sampling_location": "Delta Front",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "regolith",
        "sample_type_label": "Regolith",
        "rock_type": "Likely mixed sedimentary and igneous grains",
        "sealed_on": "2022-12-02",
        "sol": 634,
        "height_cm": 5.3,
        "height_note": None,
        "dossier_url": f"{PDS}/initial_reports_volume2.pdf#page=191",
    },
    25: {
        "number": 25,
        "name": "Sapphire Canyon",
        "feature_name": "Cheyava Falls",
        "sampling_location": "Neretva Vallis",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Sedimentary",
        "sealed_on": "2024-07-21",
        "sol": 1215,
        "height_cm": 6.2,
        "height_note": None,
        "dossier_url": f"{PDS}/initial_reports_volume4.pdf#page=120",
    },
    26: {
        "number": 26,
        "name": "Silver Mountain",
        "feature_name": "Shallow Bay",
        "sampling_location": "Crater Rim (Witch Hazel Hill)",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Igneous/impactite",
        "sealed_on": "2025-01-28",
        "sol": 1401,
        "height_cm": 2.91,
        "height_note": None,
        "dossier_url": None,
    },
    29: {
        "number": 29,
        "name": "Bell Island",
        "feature_name": "Pine Pond",
        "sampling_location": "Crater Rim (Witch Hazel Hill)",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Igneous/impactite",
        "sealed_on": None,
        "sol": None,
        "height_cm": None,
        "height_note": "Est. 5-7 cm (2-2.8 in)",
        "dossier_url": None,
    },
    30: {
        "number": 30,
        "name": "Gallants",
        "feature_name": "Salmon Point",
        "sampling_location": "Crater Rim (Krokodillen)",
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": "Igneous or sedimentary",
        "sealed_on": None,
        "sol": None,
        "height_cm": 1.91,
        "height_note": None,
        "dossier_url": None,
    },
}


# --- page fixtures and variants ----------------------------------------------------------------


def trimmed_html() -> str:
    return (FIXTURES / "samples" / "rock-samples-trimmed.html").read_text(encoding="utf-8")


def trimmed_bytes() -> bytes:
    return trimmed_html().encode("utf-8")


def quirks_bytes() -> bytes:
    return (FIXTURES / "synthetic" / "samples_quirks.html").read_bytes()


def replace_in_page(old: str, new: str, *, html: str | None = None, count: int = 1) -> bytes:
    """The trimmed page with `old` replaced by `new` (fails loudly if `old` is absent)."""
    page = trimmed_html() if html is None else html
    assert old in page, f"fixture does not contain {old!r}"
    return page.replace(old, new, count).encode("utf-8")


def split_table(html: str | None = None) -> tuple[list[str], list[list[str]]]:
    """Raw header cells and raw data cells (inner HTML) of the trimmed page's table."""
    page = trimmed_html() if html is None else html
    table = re.search(r"<table>.*?</table>", page, re.S).group(0)  # type: ignore[union-attr]
    head, body = table.split("</thead>")
    headers = re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", head, re.S)
    rows = [
        re.findall(r"<td(?:\s[^>]*)?>(.*?)</td>", row, re.S) for row in body.split("</tr>")[:-1]
    ]
    return headers, rows


def build_page(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    modified: str | None = "2026-06-20T12:45:46-04:00",
    extra_html: str = "",
) -> bytes:
    """A minimal page: optional modified-time meta, one table, optional extra markup."""
    head = f'<meta property="article:modified_time" content="{modified}" />' if modified else ""
    thead = "".join(f"<th>{h}</th>" for h in headers)
    tbody = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8" />{head}</head><body>'
        f"<figure><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></figure>"
        f"{extra_html}</body></html>"
    ).encode()


def sample_row(
    number: int | str = 31,
    *,
    date: str = "Feb. 1, 2026",
    sol: str = "2000",
    name: str = "Test Sample",
    feature: str = "Feature",
    place: str = "Place",
    current: str = "Perseverance Rover",
    notes: str = "",
    kind: str = "Rock Core",
    height: str = "6.0&nbsp;&nbsp;&nbsp;2.36",
    rock: str = "Igneous",
) -> list[str]:
    return [str(number), date, sol, name, feature, place, current, notes, kind, height, rock]


def page_with_row(number: int | str, **cells: str) -> bytes:
    """The trimmed page with one more data row appended."""
    headers, rows = split_table()
    return build_page(headers, [*rows, sample_row(number, **cells)])


def page_without_number(number: int) -> bytes:
    headers, rows = split_table()
    return build_page(headers, [row for row in rows if row[0] != str(number)])


def page_with_cell(number: int, header_index: int, value: str) -> bytes:
    """The trimmed page with one cell of one row replaced (raw HTML)."""
    headers, rows = split_table()
    return build_page(
        headers,
        [
            [*row[:header_index], value, *row[header_index + 1 :]] if row[0] == str(number) else row
            for row in rows
        ],
    )


# Column indexes of the table.
COL_NUMBER, COL_DATE, COL_SOL, COL_NAME, COL_FEATURE, COL_PLACE = 0, 1, 2, 3, 4, 5
COL_CURRENT, COL_NOTES, COL_TYPE, COL_HEIGHT, COL_ROCK = 6, 7, 8, 9, 10


def ok_response(body: bytes) -> httpx.Response:
    """What the NASA page sends: 200 HTML, no ETag and no Last-Modified."""
    return httpx.Response(200, content=body, headers={"Content-Type": "text/html; charset=UTF-8"})


def mock_page(router: respx.MockRouter, body: bytes | None = None) -> respx.Route:
    return router.get(SAMPLES_URL).mock(
        return_value=ok_response(trimmed_bytes() if body is None else body)
    )


def set_page(route: respx.Route, body: bytes) -> None:
    route.mock(return_value=ok_response(body))


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def as_plain(record: Any) -> dict[str, Any]:
    """A parsed record (dataclass or mapping) as plain values: float height, ISO date."""
    if isinstance(record, Mapping):
        data = dict(record)
    else:
        data = {name: getattr(record, name) for name in RECORD_FIELDS}
    data = {name: data[name] for name in RECORD_FIELDS}
    if data["height_cm"] is not None:
        data["height_cm"] = float(data["height_cm"])
    if data["sealed_on"] is not None and not isinstance(data["sealed_on"], str):
        data["sealed_on"] = data["sealed_on"].isoformat()
    return data


# --- database ----------------------------------------------------------------------------------

Factory = async_sessionmaker[AsyncSession]


def db_sample(number: int, **overrides: Any) -> dict[str, Any]:
    """A `rover_samples` row (DB value types) with defaults; overrides win."""
    values: dict[str, Any] = {
        "name": f"Sample {number}",
        "feature_name": None,
        "sampling_location": None,
        "current_location": "Perseverance Rover",
        "status": "on_rover",
        "sample_type": "rock_core",
        "sample_type_label": "Rock Core",
        "rock_type": None,
        "sealed_on": None,
        "sol": None,
        "height_cm": None,
        "height_note": None,
        "dossier_url": None,
    }
    values.update(overrides)
    return {"number": number, **values}


async def insert_samples(
    factory: Factory, rows: Iterable[Mapping[str, Any]], rover: str = ROVER
) -> None:
    async with factory() as session:
        for row in rows:
            values = {"rover_id": rover, **db_sample(row["number"]), **row}
            columns = list(values)
            await session.execute(
                text(
                    f"INSERT INTO rover_samples ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + c for c in columns)})"
                ),
                values,
            )
        await session.commit()


async def insert_expected(factory: Factory, numbers: Iterable[int] = NUMBERS) -> None:
    """The reference rows (EXPECTED) as stored rows."""
    from datetime import date

    rows = []
    for number in numbers:
        row = {k: v for k, v in EXPECTED[number].items()}
        if row["sealed_on"] is not None:
            row["sealed_on"] = date.fromisoformat(row["sealed_on"])
        rows.append(row)
    await insert_samples(factory, rows)


async def insert_dataset(
    factory: Factory,
    *,
    rover: str = ROVER,
    created_at: datetime | None = None,
    checked_at: datetime | None = None,
    succeeded_at: datetime | None = None,
    changed_at: datetime | None = None,
    source_modified_at: datetime | None = None,
    content_sha256: str | None = None,
    items: int = 0,
    last_error: str | None = None,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO sample_datasets (rover_id, created_at, checked_at, succeeded_at, "
                "changed_at, source_modified_at, content_sha256, items, last_error) VALUES "
                "(:rover, COALESCE(:created_at, now()), :checked_at, :succeeded_at, :changed_at, "
                ":source_modified_at, :sha, :items, :last_error)"
            ),
            {
                "rover": rover,
                "created_at": created_at,
                "checked_at": checked_at,
                "succeeded_at": succeeded_at,
                "changed_at": changed_at,
                "source_modified_at": source_modified_at,
                "sha": content_sha256,
                "items": items,
                "last_error": last_error,
            },
        )
        await session.commit()


async def rows(factory: Factory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]


async def sample_rows(factory: Factory, rover: str = ROVER) -> list[dict[str, Any]]:
    return await rows(
        factory, "SELECT * FROM rover_samples WHERE rover_id = :r ORDER BY number", r=rover
    )


async def dataset(factory: Factory, rover: str = ROVER) -> dict[str, Any] | None:
    found = await rows(factory, "SELECT * FROM sample_datasets WHERE rover_id = :r", r=rover)
    return found[0] if found else None


async def count(factory: Factory, table: str) -> int:
    return (await rows(factory, f"SELECT count(*) AS n FROM {table}"))[0]["n"]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def iso_z(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
