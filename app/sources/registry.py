"""Which rovers can be synced and which adapter serves each of them."""

from app.core.config import Settings
from app.core.errors import NoSourceForRover
from app.sources.base import SourceAdapter
from app.sources.http import UpstreamClient
from app.sources.m2020 import M2020Adapter
from app.sources.msl import MslAdapter

SYNCABLE_ROVERS: tuple[str, ...] = ("perseverance", "curiosity")

_ADAPTERS: dict[str, type[M2020Adapter] | type[MslAdapter]] = {
    "perseverance": M2020Adapter,
    "curiosity": MslAdapter,
}


def build_adapter(rover_id: str, client: UpstreamClient, settings: Settings) -> SourceAdapter:
    """Create the adapter of ``rover_id``; raises ``NoSourceForRover`` if there is none."""
    adapter_cls = _ADAPTERS.get(rover_id.lower())
    if adapter_cls is None:
        raise NoSourceForRover(rover_id)
    return adapter_cls(client, settings)
