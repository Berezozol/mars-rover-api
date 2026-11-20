"""Command line of the service.

- ``sync --rover R [--from-sol N --to-sol M]``: one sync run.
  Exit codes: 0 success, 1 partial, 2 failed or invalid input, 3 sync already in progress.
- ``backfill --rover R [--from-sol A] [--to-sol B] [--batch-sols N] [--no-resume]``: full
  history in batches. Exit codes: 0 every sol loaded, 1 failed sols remain, too many
  consecutive failed batches or the latest sol is unavailable, 2 invalid input or the rover
  stayed locked by another sync longer than BACKFILL_LOCK_MAX_WAIT_SECONDS,
  130 interrupted (Ctrl+C; rerun to resume).
- ``reconcile --rover R [--offline] [--legacy-manifest PATH] [--output PATH]``: compare
  stored data with the source. Exit codes: 0 no unexplained differences, 1 otherwise,
  2 invalid input.
- ``mer-download [--rover spirit|opportunity] [--force]``: download the PDS index files of
  Spirit/Opportunity (both by default) into MER_CACHE_DIR; complete files are skipped.
  Exit codes: 0 every file present and valid, 1 a download or check failed, 2 invalid input,
  130 interrupted.
- ``import-mer --rover spirit|opportunity [--from-sol A] [--to-sol B] [--batch-sols N]
  [--no-resume]``: one-time import from the cached index files, offline; same batches,
  resume and exit codes as ``backfill``, plus 2 when the cache is missing (run mer-download).
- ``locations [--rover perseverance|curiosity] [--force]``: load the MMGIS position files now
  (both rovers by default), ignoring the once-a-day rule; the conditional request is still
  sent unless ``--force``, which also skips the no-op and shrink checks. Exit codes: 0 every
  file changed, unchanged or skipped, 1 a file was rejected or failed, 2 unknown rover or a
  rover without a location source (Spirit, Opportunity).
- ``samples [--force]``: load the Perseverance sample table from the NASA samples page now,
  ignoring the once-a-day rule; ``--force`` drops the conditional request, the no-op and the
  "no stored sample may disappear" check. Exit codes: 0 changed or unchanged, 1 rejected or failed.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import NoSourceForRover, SyncInProgress
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.services import reconcile
from app.services.backfill import (
    BackfillArgumentError,
    BackfillOptions,
    BackfillResult,
    BackfillService,
)
from app.services.location_sync import DatasetResult, LocationSyncService
from app.services.mer_download import download_indexes
from app.services.sample_sync import SampleSyncResult, SampleSyncService
from app.services.sync import SyncResult, SyncService, compact_ranges
from app.sources.base import SourceAdapter
from app.sources.http import UpstreamClient, UpstreamError
from app.sources.locations import LOCATION_MISSIONS
from app.sources.mer import ROVER_PREFIXES, MerAdapter
from app.sources.registry import SYNCABLE_ROVERS, build_adapter

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2
EXIT_IN_PROGRESS = 3
EXIT_INTERRUPTED = 130

# backfill / reconcile
EXIT_REMAINING = 1
EXIT_USAGE = 2

_STATUS_EXIT_CODES = {"success": EXIT_SUCCESS, "partial": EXIT_PARTIAL, "failed": EXIT_FAILED}

MER_ROVERS: tuple[str, ...] = tuple(ROVER_PREFIXES)

AdapterBuilder = Callable[[AsyncExitStack], Awaitable[SourceAdapter]]
BatchesCommand = Callable[[str, int, int | None, int, bool, Settings], Awaitable[int]]


def _rover_argument(parser: argparse.ArgumentParser, *, one_off: bool = False) -> None:
    rovers = (*SYNCABLE_ROVERS, "ingenuity") if one_off else SYNCABLE_ROVERS
    parser.add_argument(
        "--rover",
        required=True,
        type=str.lower,
        help=f"rover slug, case-insensitive ({', '.join(rovers)})",
    )


def _batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from-sol", type=int, default=0, help="lowest sol (default 0)")
    parser.add_argument(
        "--to-sol", type=int, default=None, help="highest sol (default: the source's latest)"
    )
    parser.add_argument(
        "--batch-sols",
        type=int,
        default=None,
        help="sols per batch, one sync run each (default: BACKFILL_BATCH_SOLS)",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="redo batches already loaded by a successful backfill run",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command")
    sync = commands.add_parser(
        "sync",
        help="sync one rover: incremental without a range, backfill with --from-sol/--to-sol",
    )
    _rover_argument(sync)
    sync.add_argument("--from-sol", type=int, default=None, help="first sol of a backfill")
    sync.add_argument("--to-sol", type=int, default=None, help="last sol of a backfill")

    backfill = commands.add_parser(
        "backfill", help="load the full history in batches, from --to-sol down to --from-sol"
    )
    _rover_argument(backfill, one_off=True)
    _batch_arguments(backfill)

    download = commands.add_parser(
        "mer-download", help="download the Spirit/Opportunity PDS index files into the cache"
    )
    download.add_argument(
        "--rover",
        type=str.lower,
        choices=MER_ROVERS,
        default=None,
        help="one rover (default: both)",
    )
    download.add_argument(
        "--force", action="store_true", help="download again even if the files are complete"
    )

    mer = commands.add_parser(
        "import-mer", help="import Spirit/Opportunity from the cached PDS index files"
    )
    mer.add_argument("--rover", required=True, type=str.lower, choices=MER_ROVERS)
    _batch_arguments(mer)

    locations = commands.add_parser(
        "locations", help="load the Perseverance/Curiosity position files (MMGIS) now"
    )
    locations.add_argument(
        "--rover", type=str.lower, default=None, help="one rover (default: both)"
    )
    locations.add_argument(
        "--force",
        action="store_true",
        help="no conditional request, no no-op or shrink checks: replace the data",
    )

    samples = commands.add_parser(
        "samples", help="load the Perseverance sample table from the NASA samples page now"
    )
    samples.add_argument(
        "--force",
        action="store_true",
        help="no conditional request or no-op, accept a page where a stored sample is missing",
    )

    rec = commands.add_parser("reconcile", help="compare stored photos with the source")
    rec.add_argument("--rover", required=True, type=str.lower, help="rover slug")
    rec.add_argument(
        "--offline", action="store_true", help="do not ask the source for its overall total"
    )
    rec.add_argument(
        "--legacy-manifest", type=Path, default=None, help="old API manifest JSON to compare"
    )
    rec.add_argument("--output", type=Path, default=None, help="write the report as JSON here")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits on --help (0) and on usage errors (2); return the code instead.
        return exc.code if isinstance(exc.code, int) else EXIT_FAILED
    if args.command == "sync":
        return _main_sync(args)
    if args.command == "backfill":
        return _main_batches(args, run_backfill_command)
    if args.command == "reconcile":
        return _main_reconcile(args)
    if args.command == "mer-download":
        return _main_mer_download(args)
    if args.command == "import-mer":
        return _main_batches(args, run_import_mer_command)
    if args.command == "locations":
        return _main_locations(args)
    if args.command == "samples":
        return _main_samples(args)
    parser.print_help(sys.stderr)
    return EXIT_FAILED


def _main_sync(args: argparse.Namespace) -> int:
    if (args.from_sol is None) != (args.to_sol is None):
        print("error: --from-sol and --to-sol must be given together", file=sys.stderr)
        return EXIT_FAILED
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(run_sync_command(args.rover, args.from_sol, args.to_sol, settings))


def _main_batches(args: argparse.Namespace, command: BatchesCommand) -> int:
    """``backfill`` and ``import-mer``: same options, same exit codes."""
    settings = get_settings()
    configure_logging(settings.log_level)
    batch_sols = args.batch_sols if args.batch_sols is not None else settings.backfill_batch_sols
    try:
        return asyncio.run(
            command(args.rover, args.from_sol, args.to_sol, batch_sols, args.resume, settings)
        )
    except KeyboardInterrupt:
        # The running sync run was marked failed on cancellation; a rerun resumes.
        print("interrupted: rerun the same command to resume", file=sys.stderr)
        return EXIT_INTERRUPTED


def _main_mer_download(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    rovers = [args.rover] if args.rover else list(MER_ROVERS)
    try:
        report = asyncio.run(download_indexes(rovers, settings, force=args.force))
    except KeyboardInterrupt:
        print("interrupted: rerun the same command to continue", file=sys.stderr)
        return EXIT_INTERRUPTED
    for line in report.lines():
        print(line)
    return EXIT_SUCCESS if report.ok else EXIT_REMAINING


def _main_locations(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(run_locations_command(args.rover, args.force, settings))


def _main_samples(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(run_samples_command(args.force, settings))


def _main_reconcile(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    legacy: dict[int, int] | None = None
    if args.legacy_manifest is not None:
        try:
            data = json.loads(args.legacy_manifest.read_text(encoding="utf-8"))
            legacy = reconcile.parse_legacy_manifest(data)
        except (OSError, ValueError) as exc:
            print(f"error: {args.legacy_manifest}: {exc}", file=sys.stderr)
            return EXIT_USAGE
    return asyncio.run(
        run_reconcile_command(args.rover, args.offline, legacy, args.output, settings)
    )


async def run_sync_command(
    rover: str, from_sol: int | None, to_sol: int | None, settings: Settings
) -> int:
    """Run one sync and return the process exit code."""
    engine = create_engine(
        settings.database_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    try:
        async with UpstreamClient(settings) as client:
            adapter = build_adapter(rover, client, settings)
            service = SyncService(create_session_factory(engine), settings)
            result = await service.run(adapter, from_sol=from_sol, to_sol=to_sol)
    except (NoSourceForRover, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except SyncInProgress as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_IN_PROGRESS
    except Exception:
        logger.exception("Sync of rover %s crashed", rover)
        return EXIT_FAILED
    finally:
        await engine.dispose()
    print(_summary(result))
    return _STATUS_EXIT_CODES.get(result.status, EXIT_FAILED)


async def run_locations_command(rover: str | None, force: bool, settings: Settings) -> int:
    """Load the position files and return the exit code (0 ok, 1 rejected/failed, 2 usage)."""
    rovers = list(LOCATION_MISSIONS) if rover is None else [rover]
    unsupported = [r for r in rovers if r not in LOCATION_MISSIONS]
    if unsupported:
        reason = "no location source" if unsupported[0] in MER_ROVERS else "unknown rover"
        print(f"error: {reason}: {unsupported[0][:64]}", file=sys.stderr)
        return EXIT_USAGE
    engine = create_engine(
        settings.database_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    results: list[DatasetResult] = []
    try:
        async with UpstreamClient(settings) as client:
            service = LocationSyncService(engine, client, settings)
            for name in rovers:
                results.extend(await service.refresh(name, force=force))
    except Exception:
        logger.exception("Location load crashed")
        return EXIT_PARTIAL
    finally:
        await engine.dispose()
    for result in results:
        print(_location_line(result))
    bad = any(result.status in ("rejected", "failed") for result in results)
    return EXIT_PARTIAL if bad else EXIT_SUCCESS


def _location_line(result: DatasetResult) -> str:
    line = (
        f"{result.rover_id} {result.kind}: {result.status}, "
        f"items={result.items}, dropped={result.dropped}"
    )
    return f"{line}, error: {result.error}" if result.error else line


async def run_samples_command(force: bool, settings: Settings) -> int:
    """Load the samples page and return the exit code (0 changed/unchanged, 1 otherwise)."""
    engine = create_engine(
        settings.database_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    try:
        async with UpstreamClient(settings) as client:
            result = await SampleSyncService(engine, client, settings).refresh(force=force)
    except Exception:
        logger.exception("Samples load crashed")
        return EXIT_PARTIAL
    finally:
        await engine.dispose()
    print(_samples_line(result))
    return EXIT_SUCCESS if result.status in ("changed", "unchanged") else EXIT_PARTIAL


def _samples_line(result: SampleSyncResult) -> str:
    line = f"perseverance samples: {result.status}, items={result.items}"
    return f"{line}, error: {result.error}" if result.error else line


async def run_backfill_command(
    rover: str,
    from_sol: int,
    to_sol: int | None,
    batch_sols: int,
    resume: bool,
    settings: Settings,
) -> int:
    """Run a backfill and return the process exit code (cancellation propagates)."""

    async def build(stack: AsyncExitStack) -> SourceAdapter:
        # One client for the whole backfill: UPSTREAM_MAX_RPS holds across batches.
        client = await stack.enter_async_context(UpstreamClient(settings))
        return build_adapter(rover, client, settings)

    return await _run_backfill(build, rover, from_sol, to_sol, batch_sols, resume, settings)


async def run_import_mer_command(
    rover: str,
    from_sol: int,
    to_sol: int | None,
    batch_sols: int,
    resume: bool,
    settings: Settings,
) -> int:
    """Import Spirit/Opportunity from the index cache through the backfill machinery.

    Exit codes as ``run_backfill_command``; 2 when the rover is not a MER rover or index
    files are missing from the cache (checked before the database is touched).
    """
    try:
        adapter = MerAdapter(rover, settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    missing = adapter.missing_files()
    if missing:
        print(
            f"error: {len(missing)} MER index file(s) missing, e.g. {missing[0]}", file=sys.stderr
        )
        print(f"hint: run `python -m app.cli mer-download --rover {rover}`", file=sys.stderr)
        return EXIT_USAGE

    async def build(_stack: AsyncExitStack) -> SourceAdapter:
        return adapter

    return await _run_backfill(build, rover, from_sol, to_sol, batch_sols, resume, settings)


async def _run_backfill(
    build: AdapterBuilder,
    rover: str,
    from_sol: int,
    to_sol: int | None,
    batch_sols: int,
    resume: bool,
    settings: Settings,
) -> int:
    engine = create_engine(
        settings.database_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    try:
        async with AsyncExitStack() as stack:
            adapter = await build(stack)
            factory = create_session_factory(engine)
            service = BackfillService(SyncService(factory, settings), factory)
            options = BackfillOptions(
                batch_sols=batch_sols,
                resume=resume,
                lock_retry_seconds=settings.backfill_lock_retry_seconds,
                lock_max_wait_seconds=settings.backfill_lock_max_wait_seconds,
                max_failed_batches=settings.backfill_max_failed_batches,
            )
            result = await service.run(adapter, from_sol=from_sol, to_sol=to_sol, options=options)
    except (NoSourceForRover, BackfillArgumentError) as exc:
        # Only the up-front argument checks are usage errors; any other ValueError is a
        # crash (exit 1 below).
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SyncInProgress as exc:
        logger.error("Backfill stopped: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except UpstreamError as exc:
        print(f"error: latest sol unavailable: {exc}", file=sys.stderr)
        return EXIT_REMAINING
    except Exception:
        logger.exception("Backfill of rover %s crashed", rover)
        return EXIT_REMAINING
    finally:
        await engine.dispose()
    print(_backfill_summary(result))
    if result.stopped == "locked":
        return EXIT_USAGE
    return EXIT_SUCCESS if result.ok else EXIT_REMAINING


def _backfill_summary(result: BackfillResult) -> str:
    line = (
        f"{result.rover_id} backfill {result.from_sol}-{result.to_sol}: "
        f"batches={result.batches_total} skipped={result.batches_skipped} "
        f"runs={len(result.runs)} retry_runs={len(result.retry_runs)} "
        f"photos={result.photos_upserted}"
    )
    if result.failed_sols:
        failed = compact_ranges(result.failed_sols)
        return f"{line}\nfailed sols ({len(result.failed_sols)}): {failed}"
    return f"{line}, all sols loaded"


async def run_reconcile_command(
    rover: str,
    offline: bool,
    legacy: dict[int, int] | None,
    output: Path | None,
    settings: Settings,
) -> int:
    """Build the reconciliation report, print it, optionally save JSON; return the exit code."""
    engine = create_engine(
        settings.database_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    try:
        factory = create_session_factory(engine)
        source_total: int | None = None
        source_error: str | None = None
        if not offline:
            try:
                source_total, source_error = await _source_total(rover, settings)
            except NoSourceForRover as exc:
                # The hint fits only a known rover without a live feed, not a mistyped slug.
                hint = "; use --offline" if rover.lower() in ROVER_PREFIXES else ""
                print(f"error: {exc}{hint}", file=sys.stderr)
                return EXIT_USAGE
        try:
            async with factory() as session:
                report = await reconcile.build_report(
                    session,
                    rover,
                    source_total=source_total,
                    source_total_error=source_error,
                    legacy_manifest=legacy,
                )
        except reconcile.UnknownRover as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    finally:
        await engine.dispose()
    print(reconcile.render_text(report))
    if output is not None:
        await asyncio.to_thread(output.write_text, reconcile.render_json(report), encoding="utf-8")
    return EXIT_SUCCESS if report.ok else EXIT_REMAINING


async def _source_total(rover: str, settings: Settings) -> tuple[int | None, str | None]:
    """The source's overall total (one request); an upstream failure is reported, not raised."""
    async with UpstreamClient(settings) as client:
        adapter = build_adapter(rover, client, settings)
        try:
            return await adapter.source_total(), None
        except UpstreamError as exc:
            logger.warning("Source total unavailable rover=%s error=%s", rover, exc)
            return None, f"{type(exc).__name__}: {exc}"


def _summary(result: SyncResult) -> str:
    sol_range = f"{_sol(result.from_sol)}-{_sol(result.to_sol)}"
    return (
        f"{result.rover_id} {result.mode} {sol_range}: {result.status}, "
        f"photos={result.photos_upserted}, failed_sols={len(result.failed_sols)}, "
        f"run_id={result.run_id}"
    )


def _sol(value: int | None) -> str:
    return "?" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
