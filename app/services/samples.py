"""Read logic of the Perseverance sample endpoints (docs/API.md, stage 13).

A sample's stop is computed on read (ADR-5): the ``(site, drive)`` pair of the photos of the
sol it was sealed in, resolved like the photo card does; without exactly one pair, the last
stop of that sol or before it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, SampleNotFound
from app.db.models import RoverSample, RoverWaypoint
from app.repositories import samples as samples_repo
from app.services.rover_lookup import find_rover
from app.sources.samples import SAMPLES_ROVER

# Sample numbers are smallint; anything outside cannot exist and is answered without a query.
_MAX_NUMBER = 2**15 - 1

Match = Literal["exact", "nearest_before", "by_sol"]


@dataclass(frozen=True)
class SampleLocation:
    waypoint: RoverWaypoint
    match: Match


@dataclass(frozen=True)
class SampleView:
    sample: RoverSample
    # Photos of the sample's sol; None while the sol is unknown.
    total_photos: int | None
    location: SampleLocation | None


@dataclass(frozen=True)
class SamplesData:
    rover: str
    source_modified_at: datetime | None
    updated_at: datetime | None
    items: Sequence[SampleView]


async def list_samples(session: AsyncSession, slug: str) -> SamplesData:
    """All samples of Perseverance by number; no data yet: empty items and null dates."""
    rover_id = await _samples_rover(session, slug)
    samples = await samples_repo.list_samples(session, rover_id)
    dataset = await samples_repo.get_dataset(session, rover_id)
    views = await _views(session, rover_id, samples)
    return SamplesData(
        rover_id,
        dataset.source_modified_at if dataset else None,
        dataset.changed_at if dataset else None,
        views,
    )


async def get_sample(session: AsyncSession, slug: str, number: int) -> SampleView:
    """One sample; no such number (``0``, negative, out of range) -> ``SampleNotFound``."""
    rover_id = await _samples_rover(session, slug)
    if not 1 <= number <= _MAX_NUMBER:
        raise SampleNotFound()
    sample = await samples_repo.get_sample(session, rover_id, number)
    if sample is None:
        raise SampleNotFound()
    return (await _views(session, rover_id, [sample]))[0]


async def _samples_rover(session: AsyncSession, slug: str) -> str:
    """The rover of a sample endpoint: only Perseverance has samples."""
    rover = await find_rover(session, slug)
    if rover.id != SAMPLES_ROVER:
        raise NotFoundError(f"Rover {rover.id} has no samples")
    return rover.id


async def _views(
    session: AsyncSession, rover_id: str, samples: Sequence[RoverSample]
) -> list[SampleView]:
    sols = {s.sol for s in samples if s.sol is not None}
    positions = await samples_repo.sol_positions(session, rover_id, sols)
    totals = await samples_repo.sol_photo_totals(session, rover_id, sols)
    by_stop = {
        s.number: positions[s.sol][0]
        for s in samples
        if s.sol is not None and len(positions.get(s.sol, ())) == 1
    }
    by_sol = {s.number: s.sol for s in samples if s.sol is not None}
    stops, last = await samples_repo.waypoints_for_samples(session, rover_id, by_stop, by_sol)
    return [
        SampleView(
            sample,
            None if sample.sol is None else totals.get(sample.sol, 0),
            _location(sample, by_stop.get(sample.number), stops, last),
        )
        for sample in samples
    ]


def _location(
    sample: RoverSample,
    stop: tuple[int, int] | None,
    stops: dict[int, RoverWaypoint],
    last: dict[int, RoverWaypoint],
) -> SampleLocation | None:
    if stop is not None and (waypoint := stops.get(sample.number)) is not None:
        return SampleLocation(waypoint, "exact" if waypoint.drive == stop[1] else "nearest_before")
    if (waypoint := last.get(sample.number)) is not None:
        return SampleLocation(waypoint, "by_sol")
    return None
