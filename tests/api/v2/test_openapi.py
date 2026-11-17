"""OpenAPI schema shows v1 and v2 (docs/PLAN.md, stage 5)."""

import re

import httpx
import pytest

pytestmark = pytest.mark.api

V2_PATHS = [
    "/api/v2/rovers",
    "/api/v2/rovers/{rover}",
    "/api/v2/rovers/{rover}/cameras",
    "/api/v2/rovers/{rover}/photos",
    "/api/v2/rovers/{rover}/photos/latest",
    "/api/v2/rovers/{rover}/manifest",
]
V2_PHOTO_DETAIL = re.compile(r"^/api/v2/photos/\{[a-z_]+\}$")
V1_PATHS = [
    "/api/v1/rovers",
    "/api/v1/rovers/{rover}",
    "/api/v1/rovers/{rover}/photos",
    "/api/v1/rovers/{rover}/latest_photos",
]

# Declared error responses per v2 path (the detail path is matched by regex).
V2_ERRORS = {
    "/api/v2/rovers/{rover}": {"404", "422"},
    "/api/v2/rovers/{rover}/cameras": {"404", "422"},
    "/api/v2/rovers/{rover}/photos": {"404", "422"},
    "/api/v2/rovers/{rover}/photos/latest": {"404", "422"},
    "/api/v2/rovers/{rover}/manifest": {"404", "422"},
}


@pytest.fixture
async def schema(client: httpx.AsyncClient) -> dict:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


async def test_openapi_lists_v2_paths(schema: dict) -> None:
    paths = set(schema["paths"])

    assert set(V2_PATHS) <= paths
    assert [path for path in paths if V2_PHOTO_DETAIL.match(path)], sorted(paths)


async def test_openapi_lists_v1_paths_once(schema: dict) -> None:
    paths = set(schema["paths"])

    assert set(V1_PATHS) <= paths
    assert not [path for path in paths if path.startswith("/mars-photos")]


async def test_openapi_v2_operations_have_response_models(schema: dict) -> None:
    for path, item in schema["paths"].items():
        if not path.startswith("/api/v2/") or "/admin" in path or path.endswith("/image"):
            continue
        ok = item["get"]["responses"]["200"]
        assert ok["content"]["application/json"]["schema"], path


async def test_openapi_v2_declares_error_responses(schema: dict) -> None:
    paths = schema["paths"]
    detail = next(path for path in paths if V2_PHOTO_DETAIL.match(path))
    expected = {**V2_ERRORS, detail: {"404", "422"}}

    for path, codes in expected.items():
        declared = set(paths[path]["get"]["responses"])
        assert codes <= declared, (path, declared)


async def test_docs_page_is_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "openapi.json" in response.text


async def test_openapi_v2_error_responses_use_error_response_model(schema: dict) -> None:
    """Every declared v2 4xx is the {"error": ...} envelope, not FastAPI's HTTPValidationError."""
    checked = 0
    for path, item in schema["paths"].items():
        if not path.startswith("/api/v2/"):
            continue
        for method, operation in item.items():
            for code, response in operation["responses"].items():
                if not code.startswith("4"):
                    continue
                ref = response["content"]["application/json"]["schema"].get("$ref", "")
                assert ref.endswith("/ErrorResponse"), (method, path, code, ref)
                checked += 1
    assert checked, "no v2 4xx responses declared"
