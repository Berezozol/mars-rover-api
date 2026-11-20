"""Ingenuity adapter: the same feed as Perseverance (Mars 2020), category ``ingenuity``.

The helicopter has no stops: the feed's ``site`` (0..29) and ``drive`` ("None" or a counter)
are not positions. ``parse_m2020_image`` always stores NULL for both when it parses for
``ingenuity``, without a warning.
"""

import logging
from collections.abc import Mapping
from typing import Any

from app.sources.base import PhotoRecord
from app.sources.http import UpstreamError
from app.sources.m2020 import M2020Adapter, parse_m2020_image
from app.sources.normalize import to_int

logger = logging.getLogger(__name__)

ROVER_ID = "ingenuity"


def parse_ingenuity_image(raw: Mapping[str, Any]) -> PhotoRecord | None:
    """Map one feed image to a record; None for thumbnails and incomplete images."""
    return parse_m2020_image(raw, ROVER_ID)


class IngenuityAdapter(M2020Adapter):
    """Fetches Ingenuity photos by sol range (solar days of Perseverance)."""

    rover_id = ROVER_ID
    category = ROVER_ID

    async def latest_sol(self) -> int:
        """``latest_sol`` of the feed; the category may omit it, then the newest image's sol."""
        data = await self._client.get_json(self._url, {**self._base_params, "latest": "true"})
        if not isinstance(data, dict):
            raise UpstreamError(self._url, None, "response is not an object")
        sol = to_int(data.get("latest_sol"), field="latest_sol")
        if sol is not None:
            return sol
        sols = [
            value
            for item in data.get("images") or []
            if isinstance(item, Mapping)
            and (value := to_int(item.get("sol"), field="sol")) is not None
        ]
        if not sols:
            raise UpstreamError(self._url, None, "latest_sol is missing")
        logger.warning(
            "Ingenuity feed has no latest_sol; using the newest image's sol %d", max(sols)
        )
        return max(sols)

    def parse(self, raw: Mapping[str, Any]) -> PhotoRecord | None:
        return parse_ingenuity_image(raw)
