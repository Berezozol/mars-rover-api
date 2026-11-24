"""AC-4 (docs/proposals/msl-thumbnails.md): the MSL sync of a sol also fetches its thumbnails.

`SyncService.run(MslAdapter, ...)` on one sol makes the usual frame requests plus exactly one
thumbnail request (`extended=thumbnail::sample_type::eq`, `per_page=1000`,
`condition_2=<sol>:sol:in`).
A failing thumbnail request must not fail the sync: frames are stored, `img_small` stays NULL,
a warning is logged. The feed is mocked with respx; frame items are built from the recorded
thumbnail items of the same sol (same envelope), thumbnails come from the recorded answers.
"""

import copy
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.sync import SyncService
from app.sources import http
from app.sources.msl import MslAdapter
from tests.integration._fakes import fetch_all
from tests.integration._thumbs import ThumbFeed, expected_small, frames_of, stored_small
from tests.unit._upstream import MSL_URL, FakeClock, load_fixture, make_settings, query

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
SOL = 5073


@pytest.fixture
async def upstream_client() -> AsyncIterator[http.UpstreamClient]:
    clock = FakeClock()
    client = http.UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def service(session_factory: SessionFactory, settings: Settings) -> SyncService:
    return SyncService(session_factory, settings.model_copy(update={"sync_chunk_sols": 10}))


def frame_items(sol: int) -> list[dict[str, Any]]:
    """Feed items (not thumbnails) of the stored frames of the sol, in the real item shape."""
    template = load_fixture(f"msl/thumbnails_sol_{sol}.json")["items"][0]
    items = []
    for f in frames_of(sol):
        item = copy.deepcopy(template)
        item.update(
            id=int(f["source_id"]),
            imageid=f["imageid"],
            sol=sol,
            instrument=f["camera"],
            url=f["img_full"],
            https_url=f["img_full"],
            is_thumbnail=False,
            link=f"/raw_images/{f['source_id']}",
        )
        item["extended"] = {
            **item["extended"],
            "sample_type": f["sample_type"],
            "url_list": f["img_full"],
        }
        items.append(item)
    return items


class SolFeed:
    """Frames of ``SOL`` for the usual per-sol query, thumbnails through ``ThumbFeed``."""

    def __init__(self, thumbs: ThumbFeed) -> None:
        self.thumbs = thumbs
        self.frame_requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = query(request)
        if params.get("extended") == "thumbnail::sample_type::eq":
            return self.thumbs(request)
        self.frame_requests += 1
        sol = int(params["condition_2"].split(":")[0])
        items = frame_items(sol) if sol == SOL else []
        return httpx.Response(
            200, json={"items": items, "more": False, "total": len(items), "page": 0}
        )


def mock_feed(router: respx.MockRouter, thumbs: ThumbFeed) -> SolFeed:
    feed = SolFeed(thumbs)
    router.get(MSL_URL).mock(side_effect=feed)
    return feed


async def test_sync_sol_makes_one_extra_thumbnail_request_and_writes_img_small_ac4(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    thumbs = ThumbFeed()
    feed = mock_feed(upstream_router, thumbs)

    result = await service.run(
        MslAdapter(upstream_client, make_settings()), from_sol=SOL, to_sol=SOL
    )

    assert result.status == "success"
    assert feed.frame_requests == 1
    assert thumbs.calls == [([SOL], 0)]
    assert thumbs.bad_requests == []
    assert await stored_small(session_factory, SOL) == expected_small(SOL)


async def test_sync_sol_thumbnail_request_error_does_not_fail_the_sync_ac4(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    thumbs = ThumbFeed(fail_when=lambda sols: True)
    mock_feed(upstream_router, thumbs)
    caplog.set_level(logging.INFO)

    result = await service.run(
        MslAdapter(upstream_client, make_settings()), from_sol=SOL, to_sol=SOL
    )

    assert result.status != "failed"
    stored = await stored_small(session_factory, SOL)
    assert len(stored) == len(frames_of(SOL))  # every frame is stored
    assert all(url is None for url in stored.values())
    runs = await fetch_all(session_factory, "SELECT status, failed_sols FROM sync_runs")
    assert [r["status"] for r in runs] == [result.status]
    assert not runs[0]["failed_sols"]
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "thumbnail" in r.getMessage().lower()
    ]
    assert warnings, "a WARNING about the thumbnails is expected"


async def test_sync_sol_resync_keeps_img_small_when_the_thumbnail_request_fails_ac4(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    upstream_client: http.UpstreamClient,
) -> None:
    """Re-upserted frames carry no thumbnail; a failed 2nd thumbnail request must keep it."""
    thumbs = ThumbFeed()
    mock_feed(upstream_router, thumbs)
    adapter = MslAdapter(upstream_client, make_settings())
    await service.run(adapter, from_sol=SOL, to_sol=SOL)
    assert await stored_small(session_factory, SOL) == expected_small(SOL)

    thumbs.fail_when = lambda sols: True
    result = await service.run(adapter, from_sol=SOL, to_sol=SOL)

    assert result.status != "failed"
    assert await stored_small(session_factory, SOL) == expected_small(SOL)
