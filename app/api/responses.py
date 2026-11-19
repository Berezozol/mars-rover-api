"""Fast JSON responses for large bodies built as plain data (no response_model validation).

The routes still declare ``response_model`` for the OpenAPI schema; returning a Response
instance makes FastAPI skip validating and re-encoding the body. Builders must produce the
documented key order themselves (see the module docstrings of the schema modules).

Serialized by pydantic-core's Rust encoder (``pydantic_core.to_json``): compact separators,
UTF-8 without ``\\u`` escapes, ``date`` as ``YYYY-MM-DD`` - the same bytes the models gave.
Datetimes must be formatted by the builders (v2 uses ``YYYY-MM-DDTHH:MM:SSZ``).
"""

from typing import Any

from fastapi.responses import JSONResponse
from pydantic_core import to_json


class FastJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return to_json(content)


class GeoJSONResponse(FastJSONResponse):
    media_type = "application/geo+json"
