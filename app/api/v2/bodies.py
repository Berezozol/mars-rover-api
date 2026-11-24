"""Reading the JSON body of a key endpoint by hand (stage 20B).

Order of checks: ``Content-Type`` (415), size (413), then the schema (422). The body is read
in chunks and refused as soon as it is over the limit, so a huge body is never held.
"""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.core.errors import PayloadTooLarge, UnsupportedMediaType

MAX_BODY_BYTES = 2048


def body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """``openapi_extra`` of an operation whose body is read by hand."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def _check_content_type(request: Request) -> None:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise UnsupportedMediaType()


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PayloadTooLarge(MAX_BODY_BYTES)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise PayloadTooLarge(MAX_BODY_BYTES)
        chunks.append(chunk)
    return b"".join(chunks)


async def read_json_body[B: BaseModel](request: Request, model: type[B]) -> B:
    """The validated body; ``UnsupportedMediaType``, ``PayloadTooLarge`` or a 422."""
    _check_content_type(request)
    raw = await _read_limited(request)
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        for error in errors:
            error["loc"] = ("body", *error["loc"])
        raise RequestValidationError(errors) from None
