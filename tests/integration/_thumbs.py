"""Helpers for the Curiosity thumbnail tests (docs/proposals/msl-thumbnails.md).

Frames come from `tests/fixtures/msl_thumbnail_frames.json` (rows of the dev database for sols
5073 and 100); thumbnails come from the recorded feed answers
`tests/fixtures/upstream/msl/thumbnails_sol_<N>.json`. Expected URLs are taken from the frames
file (`expected_img_small`), never computed by the application's matching code.

Frames keep their imageid in `extra["imageid"]` (what the MSL adapter stores).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from tests.conftest import build_record
from tests.integration._fakes import fetch_all
from tests.unit._upstream import load_fixture, query

SessionFactory = async_sessionmaker[AsyncSession]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
THUMBNAIL_SOLS = (100, 5073)
THUMBNAIL_FILTER = "thumbnail::sample_type::eq"


def frames_of(sol: int) -> list[dict[str, Any]]:
    with (FIXTURES / "msl_thumbnail_frames.json").open(encoding="utf-8") as fh:
        return copy.deepcopy(json.load(fh)["sols"][str(sol)])


def thumbnail_items(sol: int) -> list[dict[str, Any]]:
    return list(load_fixture(f"msl/thumbnails_sol_{sol}.json")["items"])


async def seed_frames(
    factory: SessionFactory,
    sol: int,
    *,
    only: Iterable[str] | None = None,
    skip: Iterable[str] = (),
    img_small: dict[str, str] | None = None,
) -> None:
    """Store the frames of ``sol`` (optionally a subset by imageid); ``img_small`` is NULL."""
    chosen = set(only) if only is not None else None
    skipped = set(skip)
    records = []
    for f in frames_of(sol):
        if (chosen is not None and f["imageid"] not in chosen) or f["imageid"] in skipped:
            continue
        records.append(
            build_record(
                source_id=f["source_id"],
                sol=sol,
                camera_code=f["camera"],
                sample_type=f["sample_type"],
                img_full=f["img_full"],
                img_small=(img_small or {}).get(f["imageid"]),
                extra={"imageid": f["imageid"]},
            )
        )
    async with factory() as session:
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(session, "curiosity", {sol})
        await session.commit()


async def stored_small(factory: SessionFactory, sol: int | None = None) -> dict[str, str | None]:
    """imageid -> img_small of the stored Curiosity photos."""
    rows = await fetch_all(
        factory,
        "SELECT extra->>'imageid' AS imageid, img_small FROM photos "
        "WHERE rover_id = 'curiosity' AND (CAST(:sol AS int) IS NULL OR sol = :sol)",
        sol=sol,
    )
    return {r["imageid"]: r["img_small"] for r in rows}


def expected_small(sol: int, *, only: Iterable[str] | None = None) -> dict[str, str | None]:
    """imageid -> expected img_small of the frames of the sol (None: stays NULL)."""
    chosen = set(only) if only is not None else None
    return {
        f["imageid"]: f["expected_img_small"]
        for f in frames_of(sol)
        if chosen is None or f["imageid"] in chosen
    }


class ThumbFeed:
    """respx side effect for the MSL feed, thumbnail requests only.

    - rejects (500, recorded in ``bad_requests``) any request that is not a thumbnail query:
      no ``extended=thumbnail::sample_type::eq``, no ``condition_2`` sol list, ``per_page`` != 1000;
    - answers the sols of the ``condition_2`` list from the recorded fixtures, paged by
      ``page_size`` when set (``more`` / ``total`` consistent);
    - ``fail_when``: optional predicate on the sol list -> answer 500;
    - ``calls``: list of ``(sols, page)`` of accepted requests.
    """

    def __init__(
        self,
        *,
        page_size: int | None = None,
        fail_when: Callable[[list[int]], bool] | None = None,
    ) -> None:
        self.page_size = page_size
        self.fail_when = fail_when
        self.calls: list[tuple[list[int], int]] = []
        self.bad_requests: list[dict[str, str]] = []
        self.override: dict[int, list[dict[str, Any]]] = {}

    def items_of(self, sol: int) -> list[dict[str, Any]]:
        if sol in self.override:
            return copy.deepcopy(self.override[sol])
        return thumbnail_items(sol) if sol in THUMBNAIL_SOLS else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = query(request)
        condition = params.get("condition_2", "")
        if (
            params.get("extended") != THUMBNAIL_FILTER
            or not condition.endswith(":sol:in")
            or params.get("per_page") != "1000"
        ):
            self.bad_requests.append(params)
            return httpx.Response(500, json={"error": "not a thumbnail request"})
        sols = [int(s) for s in condition.removesuffix(":sol:in").split(",")]
        page = int(params.get("page", 0))
        if self.fail_when is not None and self.fail_when(sols):
            return httpx.Response(500, json={"error": "synthetic upstream failure"})
        self.calls.append((sols, page))
        items = [item for sol in sols for item in self.items_of(sol)]
        size = self.page_size or max(len(items), 1)
        chunk = items[page * size : (page + 1) * size]
        return httpx.Response(
            200,
            json={
                "items": chunk,
                "more": (page + 1) * size < len(items),
                "total": len(items),
                "page": page,
                "per_page": 1000,
            },
        )

    @property
    def sol_batches(self) -> list[frozenset[int]]:
        """Distinct sol lists requested, in first-request order."""
        seen: list[frozenset[int]] = []
        for sols, _ in self.calls:
            if frozenset(sols) not in seen:
                seen.append(frozenset(sols))
        return seen
