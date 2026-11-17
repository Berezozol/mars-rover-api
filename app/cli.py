"""Command line: ``python -m app.cli sync --rover perseverance [--from-sol N --to-sol M]``.

Exit codes: 0 success, 1 partial, 2 failed or invalid input, 3 sync already in progress.
"""

import argparse
import asyncio
import logging
import sys

from app.core.config import Settings, get_settings
from app.core.errors import NoSourceForRover, SyncInProgress
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.services.sync import SyncResult, SyncService
from app.sources.http import UpstreamClient
from app.sources.registry import SYNCABLE_ROVERS, build_adapter

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2
EXIT_IN_PROGRESS = 3

_STATUS_EXIT_CODES = {"success": EXIT_SUCCESS, "partial": EXIT_PARTIAL, "failed": EXIT_FAILED}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    sync = commands.add_parser(
        "sync",
        help="sync one rover: incremental without a range, backfill with --from-sol/--to-sol",
    )
    sync.add_argument(
        "--rover",
        required=True,
        type=str.lower,
        help=f"rover slug, case-insensitive ({', '.join(SYNCABLE_ROVERS)})",
    )
    sync.add_argument("--from-sol", type=int, default=None, help="first sol of a backfill")
    sync.add_argument("--to-sol", type=int, default=None, help="last sol of a backfill")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits on --help (0) and on usage errors (2); return the code instead.
        return exc.code if isinstance(exc.code, int) else EXIT_FAILED
    if args.command != "sync":
        parser.print_help(sys.stderr)
        return EXIT_FAILED
    if (args.from_sol is None) != (args.to_sol is None):
        print("error: --from-sol and --to-sol must be given together", file=sys.stderr)
        return EXIT_FAILED
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(run_sync_command(args.rover, args.from_sol, args.to_sol, settings))


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
