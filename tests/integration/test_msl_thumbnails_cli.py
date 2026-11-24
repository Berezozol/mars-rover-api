"""AC-2, AC-3, AC-5, AC-6 (docs/proposals/msl-thumbnails.md): Curiosity thumbnails -> img_small.

Driven through the public entry point `python -m app.cli thumbnails --rover curiosity
--from-sol A --to-sol B` (called in-process like tests/integration/test_backfill_cli.py), so the
tests do not depend on the signature of `apply_thumbnails`. The feed is mocked with respx on the
recorded answers `tests/fixtures/upstream/msl/thumbnails_sol_{100,5073}.json`; frames are the
rows of `tests/fixtures/msl_thumbnail_frames.json`. Expected URLs come from that file.
"""

import asyncio
import logging
import signal
from collections.abc import Iterator

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from app.services.thumbnails import MSL_THUMBNAIL_CAMERAS
from tests.conftest import build_record
from tests.integration._fakes import fetch_all
from tests.integration._thumbs import (
    ThumbFeed,
    expected_small,
    frames_of,
    seed_frames,
    stored_small,
)
from tests.unit._upstream import MSL_URL

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]

CHEMCAM = "CR0_847839755EDR_F1252274CCAM03072M_"
SHARED_C00 = "0100ML0004900050102961C00_DXXX"
SHARED_E01 = "0100ML0004900050102961E01_DXXX"


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    env = {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "UPSTREAM_RETRY_BASE_SECONDS": "0",
        "MSL_FEED_URL": MSL_URL,
        "LOG_LEVEL": "INFO",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def feed(upstream_router: respx.MockRouter) -> ThumbFeed:
    double = ThumbFeed()
    upstream_router.get(MSL_URL).mock(side_effect=double)
    return double


async def run_thumbnails(from_sol: int, to_sol: int, rover: str = "curiosity") -> int:
    argv = ["thumbnails", "--rover", rover, "--from-sol", str(from_sol), "--to-sol", str(to_sol)]
    try:
        return await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


async def row_versions(factory: SessionFactory) -> dict[str, str]:
    """source_id -> xmin: changes whenever the row is rewritten."""
    rows = await fetch_all(
        factory, "SELECT source_id, xmin::text AS v FROM photos WHERE rover_id = 'curiosity'"
    )
    return {r["source_id"]: r["v"] for r in rows}


# --- AC-2: which frames get img_small ----------------------------------------------------------


async def test_thumbnails_sol_5073_writes_img_small_only_for_msss_frames_with_a_pair_ac2(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    await seed_frames(session_factory, 5073)

    assert await run_thumbnails(5073, 5073) == 0

    stored = await stored_small(session_factory, 5073)
    assert stored == expected_small(5073)
    assert (
        sum(url is not None for url in stored.values()) == 9
    )  # 3x MAST_LEFT, 3x MAST_RIGHT, 2x MAHLI, MARDI
    assert feed.bad_requests == []


async def test_thumbnails_sol_100_writes_pairs_and_ignores_unpaired_thumbnails_ac2(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    await seed_frames(session_factory, 100)

    assert await run_thumbnails(100, 100) == 0

    stored = await stored_small(session_factory, 100)
    assert stored == expected_small(100)
    # Thumbnails of Navcam / Hazcam have no frame in the base or are out of scope: no new rows.
    assert len(stored) == len(frames_of(100))


@pytest.mark.parametrize(
    "imageid",
    [
        "5073ML1102670011307122E01_DXXX",  # Mastcam frame whose thumbnail is not published
        "FLB_847851928EDR_F1252490FHAZ00302M_",  # Hazcam, thumbnail 64x64 in the feed
        "NLB_847853270EDR_F1252490NCAM00354M_",  # Navcam, thumbnail in the feed
        "NRB_847853302EDR_F1252490NCAM00354M_",
        "RLB_847852011EDR_S1252490RHAZ00303M_",
        CHEMCAM,
    ],
    ids=["mastcam-no-pair", "fhaz", "navcam-left", "navcam-right", "rhaz", "chemcam"],
)
async def test_thumbnails_frames_without_pair_or_out_of_scope_keep_img_small_null_ac2(
    feed: ThumbFeed, session_factory: SessionFactory, imageid: str
) -> None:
    await seed_frames(session_factory, 5073)

    await run_thumbnails(5073, 5073)

    assert (await stored_small(session_factory, 5073))[imageid] is None


async def test_thumbnails_camera_outside_the_list_is_not_written_even_with_a_matching_key_ac2(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    """A Navcam frame that shares the key of a Mastcam thumbnail still gets nothing."""
    from app.repositories.photos import upsert_photos

    record = build_record(
        source_id="navcam-with-mastcam-key",
        sol=5073,
        camera_code="NAV_LEFT_B",
        sample_type="full",
        extra={"imageid": "5073ML1102680061307131C00_DXXX"},
    )
    async with session_factory() as session:
        await upsert_photos(session, "curiosity", [record])
        await session.commit()

    await run_thumbnails(5073, 5073)

    assert (await stored_small(session_factory, 5073))["5073ML1102680061307131C00_DXXX"] is None


def test_msl_thumbnail_cameras_constant_lists_the_four_msss_cameras_ac2() -> None:
    assert set(MSL_THUMBNAIL_CAMERAS) == {"MAST_LEFT", "MAST_RIGHT", "MAHLI", "MARDI"}


async def test_thumbnails_other_columns_of_a_photo_stay_untouched_ac2(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    await seed_frames(session_factory, 5073)
    before = await fetch_all(
        session_factory,
        "SELECT source_id, img_full, img_medium, img_large, sample_type, sol FROM photos "
        "ORDER BY source_id",
    )

    await run_thumbnails(5073, 5073)

    after = await fetch_all(
        session_factory,
        "SELECT source_id, img_full, img_medium, img_large, sample_type, sol FROM photos "
        "ORDER BY source_id",
    )
    assert after == before
    assert all(row["img_medium"] is None and row["img_large"] is None for row in after)


# --- AC-3: one thumbnail, several frames -------------------------------------------------------


async def test_thumbnails_c00_and_e01_of_one_shot_get_the_same_url_ac3(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    await seed_frames(session_factory, 100)

    await run_thumbnails(100, 100)

    stored = await stored_small(session_factory, 100)
    expected = "https://mars.jpl.nasa.gov/msl-raw-images/msss/00100/mcam/0100ML0004900050102961I01_DXXX.jpg"
    assert stored[SHARED_C00] == expected
    assert stored[SHARED_E01] == expected
    right_c00 = stored["0100MR0004900040104454C00_DXXX"]
    assert right_c00 is not None
    assert right_c00 == stored["0100MR0004900040104454E01_DXXX"]
    assert right_c00 != expected


# --- AC-5: a thumbnail arrives before its frame ------------------------------------------------


async def test_thumbnails_without_frame_write_nothing_and_do_not_fail_ac5(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    """No frames at all: exit 0, no photo appears."""
    assert await run_thumbnails(5073, 5073) == 0

    assert await fetch_all(session_factory, "SELECT id FROM photos") == []


async def test_thumbnails_second_pass_writes_the_frame_that_appeared_later_ac5(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    late = {"5073ML1102680061307131C00_DXXX", "5073MR1102630002602329C00_DXXX"}
    await seed_frames(session_factory, 5073, skip=late)

    assert await run_thumbnails(5073, 5073) == 0

    first = await stored_small(session_factory, 5073)
    assert set(first).isdisjoint(late)
    assert first == expected_small(5073, only=set(first))
    assert any(url is not None for url in first.values())

    await seed_frames(session_factory, 5073, only=late)
    assert await run_thumbnails(5073, 5073) == 0

    second = await stored_small(session_factory, 5073)
    assert second == expected_small(5073)
    assert all(second[imageid] is not None for imageid in late)


# --- AC-6: the CLI -----------------------------------------------------------------------------


async def test_thumbnails_cli_one_request_per_batch_of_at_most_20_sols_ac6(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    assert await run_thumbnails(0, 44) == 0

    assert len(feed.calls) == 3
    assert all(page == 0 for _, page in feed.calls)
    assert all(len(sols) <= 20 for sols, _ in feed.calls)
    assert sorted(sol for sols, _ in feed.calls for sol in sols) == list(range(45))
    assert feed.bad_requests == []


async def test_thumbnails_cli_requests_more_pages_when_a_batch_has_more_than_a_page_ac6(
    session_factory: SessionFactory, upstream_router: respx.MockRouter
) -> None:
    feed = ThumbFeed(page_size=5)  # sol 100 has 12 thumbnails -> 3 pages
    upstream_router.get(MSL_URL).mock(side_effect=feed)
    await seed_frames(session_factory, 100)

    assert await run_thumbnails(0, 120) == 0

    assert len(feed.sol_batches) == 7  # 121 sols in batches of 20
    batch_with_100 = next(sols for sols in feed.sol_batches if 100 in sols)
    pages = sorted(page for sols, page in feed.calls if frozenset(sols) == batch_with_100)
    assert pages == [0, 1, 2]
    assert len(feed.calls) == 7 + 2
    assert await stored_small(session_factory, 100) == expected_small(100)


async def test_thumbnails_cli_rerun_changes_nothing_ac6(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    await seed_frames(session_factory, 5073)
    await seed_frames(session_factory, 100)
    assert await run_thumbnails(100, 5073) == 0
    state = await stored_small(session_factory)
    versions = await row_versions(session_factory)

    assert await run_thumbnails(100, 5073) == 0

    assert await stored_small(session_factory) == state
    assert await row_versions(session_factory) == versions  # no row was rewritten


async def test_thumbnails_cli_replaces_an_outdated_url_ac6(
    feed: ThumbFeed, session_factory: SessionFactory
) -> None:
    stale = {"5073MH0001530001900057R00_DXXX": "https://example.test/old.jpg"}
    await seed_frames(session_factory, 5073, img_small=stale)

    await run_thumbnails(5073, 5073)

    assert (await stored_small(session_factory, 5073)) == expected_small(5073)


async def test_thumbnails_cli_after_a_failed_batch_a_rerun_completes_the_job_ac6(
    session_factory: SessionFactory, upstream_router: respx.MockRouter
) -> None:
    broken = ThumbFeed(fail_when=lambda sols: 5073 in sols)
    route = upstream_router.get(MSL_URL).mock(side_effect=broken)
    await seed_frames(session_factory, 100)
    await seed_frames(session_factory, 5073)

    assert await run_thumbnails(100, 5100) == 1  # failed batches remain

    route.mock(side_effect=ThumbFeed())
    assert await run_thumbnails(100, 5100) == 0

    stored = await stored_small(session_factory)
    assert stored == {**expected_small(100), **expected_small(5073)}


@pytest.mark.parametrize("rover", ["perseverance", "opportunity", "spirit"])
async def test_thumbnails_cli_other_rover_is_an_error_with_a_clear_message_ac6(
    feed: ThumbFeed,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    rover: str,
) -> None:
    caplog.set_level(logging.INFO)

    code = await run_thumbnails(0, 10, rover=rover)

    captured = capsys.readouterr()
    text = (captured.out + captured.err + caplog.text).lower()
    assert code == 2
    assert "curiosity" in text
    assert feed.calls == []
    assert feed.bad_requests == []


@pytest.mark.parametrize(
    ("from_sol", "to_sol"), [(10, 5), (-1, 5)], ids=["from-after-to", "negative-from"]
)
async def test_thumbnails_cli_bad_sol_range_exits_2_without_requests_ac6(
    feed: ThumbFeed, from_sol: int, to_sol: int
) -> None:
    assert await run_thumbnails(from_sol, to_sol) == 2
    assert feed.calls == [] and feed.bad_requests == []


def test_thumbnails_cli_ctrl_c_exits_130_and_a_rerun_completes_ac6(
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    settings: Settings,
) -> None:
    """SIGINT in the middle of the walk -> exit 130; the same command run again finishes."""
    fired: list[bool] = []

    def interrupt_once(sols: list[int]) -> bool:
        if not fired:
            fired.append(True)
            signal.raise_signal(signal.SIGINT)
        return False

    route = upstream_router.get(MSL_URL).mock(side_effect=ThumbFeed(fail_when=interrupt_once))
    argv = ["thumbnails", "--rover", "curiosity", "--from-sol", "0", "--to-sol", "120"]
    try:
        code = cli.main(argv)
    except KeyboardInterrupt:
        pytest.fail("thumbnails let KeyboardInterrupt escape instead of exiting with 130")
    assert code == 130

    route.mock(side_effect=ThumbFeed())
    assert cli.main(argv) == 0


def test_thumbnails_cli_help_lists_the_options_ac6(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = cli.main(["thumbnails", "--help"])
    except SystemExit as exc:  # main() may either return or exit with the argparse code
        code = exc.code

    assert code == 0
    out = capsys.readouterr().out
    assert "--from-sol" in out and "--to-sol" in out and "--rover" in out
