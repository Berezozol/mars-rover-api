"""One-time download of the Spirit/Opportunity PDS index files into ``MER_CACHE_DIR``.

Per volume: ``index/edrindex.lbl``, then ``index/edrindex.tab``. A download is complete when
its size equals ``Content-Length`` (checked by ``UpstreamClient.download``); a table is valid
when its row count equals the label's ``ROWS`` (``ROWS x ROW_BYTES`` does not match the
file: rows are variable-width). Valid files already in the cache are skipped unless forced.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.core.config import Settings
from app.sources.http import UpstreamClient, UpstreamError
from app.sources.mer import (
    INDEX_DIR,
    LABEL_FILE,
    TABLE_FILE,
    IndexLabel,
    index_paths,
    parse_label,
    volume_ids,
)

logger = logging.getLogger(__name__)

Status = Literal["skipped", "downloaded", "failed"]


@dataclass(frozen=True)
class FileOutcome:
    path: Path
    status: Status
    detail: str = ""


@dataclass
class DownloadReport:
    outcomes: list[FileOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(outcome.status != "failed" for outcome in self.outcomes)

    def lines(self) -> list[str]:
        lines = [f"{o.status:<10} {o.path} {o.detail}".rstrip() for o in self.outcomes]
        counts = {status: 0 for status in ("downloaded", "skipped", "failed")}
        for outcome in self.outcomes:
            counts[outcome.status] += 1
        lines.append(", ".join(f"{status}={count}" for status, count in counts.items()))
        return lines


def count_rows(path: Path) -> int:
    """Non-empty lines of an index table (blocking)."""
    rows = 0
    with path.open("rb") as file:
        for line in file:
            if line.strip():
                rows += 1
    return rows


def read_label(path: Path) -> IndexLabel | None:
    """The parsed label, or None when it is missing or unparsable."""
    try:
        return parse_label(path.read_text(encoding="latin-1"))
    except (OSError, ValueError):
        return None


async def download_indexes(
    rovers: list[str],
    settings: Settings,
    *,
    force: bool = False,
    client: UpstreamClient | None = None,
) -> DownloadReport:
    """Download (or check) the index files of ``rovers``; never raises on a single file."""
    report = DownloadReport()
    if client is None:
        async with UpstreamClient(settings) as own_client:
            await _download_all(own_client, rovers, settings, force, report)
    else:
        await _download_all(client, rovers, settings, force, report)
    return report


async def _download_all(
    client: UpstreamClient,
    rovers: list[str],
    settings: Settings,
    force: bool,
    report: DownloadReport,
) -> None:
    cache_dir = Path(settings.mer_cache_dir)
    base_url = settings.mer_pds_base_url.rstrip("/")
    for rover in rovers:
        for volume in volume_ids(rover):
            await _download_volume(client, base_url, cache_dir, volume, force, report)


async def _download_volume(
    client: UpstreamClient,
    base_url: str,
    cache_dir: Path,
    volume: str,
    force: bool,
    report: DownloadReport,
) -> None:
    label_path, table_path = index_paths(cache_dir, volume)
    url = f"{base_url}/{volume}/{INDEX_DIR}"
    label = None if force else read_label(label_path)
    if label is not None:
        report.outcomes.append(FileOutcome(label_path, "skipped", f"ROWS={label.rows}"))
    else:
        label = await _fetch_label(client, f"{url}/{LABEL_FILE}", label_path, report)
        if label is None:
            return
    if not force and table_path.is_file():
        rows = await asyncio.to_thread(count_rows, table_path)
        if rows == label.rows:
            report.outcomes.append(FileOutcome(table_path, "skipped", f"rows={rows}"))
            return
        logger.info(
            "MER %s: %d rows, label says %d; downloading again", table_path, rows, label.rows
        )
    await _fetch_table(client, f"{url}/{TABLE_FILE}", table_path, label.rows, report)


async def _fetch_label(
    client: UpstreamClient, url: str, path: Path, report: DownloadReport
) -> IndexLabel | None:
    try:
        size = await client.download(url, path)
    except UpstreamError as exc:
        report.outcomes.append(FileOutcome(path, "failed", str(exc)))
        return None
    label = read_label(path)
    if label is None:
        report.outcomes.append(FileOutcome(path, "failed", "not a PDS index label"))
        return None
    report.outcomes.append(FileOutcome(path, "downloaded", f"{size} bytes, ROWS={label.rows}"))
    return label


async def _fetch_table(
    client: UpstreamClient, url: str, path: Path, expected_rows: int, report: DownloadReport
) -> None:
    try:
        size = await client.download(url, path)
    except UpstreamError as exc:
        report.outcomes.append(FileOutcome(path, "failed", str(exc)))
        return
    rows = await asyncio.to_thread(count_rows, path)
    if rows != expected_rows:
        detail = f"{rows} rows, label ROWS={expected_rows}"
        report.outcomes.append(FileOutcome(path, "failed", detail))
        return
    report.outcomes.append(FileOutcome(path, "downloaded", f"{size} bytes, rows={rows}"))
