"""Admin endpoints are not public: not in the schema, docs, examples or anonymous texts.

Owner decision 2026-11-26. The behaviour of the admin routes themselves is unchanged (their
401 / 404 / 200 contract is covered in tests/api/v2/test_admin.py; here only a short regression).
The application is built with every feature on: admin token, API keys, sign-up and SMTP.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from app.api.v2 import admin as admin_module
from app.api.v2.admin import require_admin
from app.core.config import Settings
from tests._apikeys import KeysApp, keys_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

ROOT = Path(__file__).resolve().parents[2]
RUNS = "/api/v2/admin/sync/runs"

FORBIDDEN_TEXT = ["X-Admin-Token", "ADMIN_TOKEN", "/admin"]
FORBIDDEN_SCHEMAS = [
    "IssueKeyIn",
    "BlockAccountIn",
    "SyncRequestIn",
    "SyncStartedOut",
    "RunsResponse",
]
ANON_PAGES = ["/", "/developers", "/llms.txt", "/docs", "/redoc"]
ANON_FORBIDDEN = ["X-Admin-Token", "ADMIN_TOKEN", "/api/v2/admin"]


def full_features(settings: Settings) -> dict[str, Any]:
    """Extra settings: sign-up and SMTP on top of keys and admin (set by `keys_app`)."""
    return {
        "key_signup_enabled": True,
        "smtp_host": "smtp.example.invalid",
        "mail_from": "sender@example.invalid",
        "public_base_url": "https://example.invalid",
    }


@pytest.fixture
async def full(settings: Settings) -> AsyncIterator[KeysApp]:
    async with keys_app(settings, **full_features(settings)) as env:
        yield env


def schema_property_names(schema: dict[str, Any]) -> list[str]:
    return list(schema.get("components", {}).get("schemas", {}))


def assert_no_admin(schema: dict[str, Any]) -> None:
    paths = [path for path in schema["paths"] if "admin" in path.lower()]
    assert not paths, paths
    schemes = schema.get("components", {}).get("securitySchemes", {})
    headers = [
        name for name, scheme in schemes.items() if "x-admin-token" in json.dumps(scheme).lower()
    ]
    assert not headers, headers
    text = json.dumps(schema)
    for needle in FORBIDDEN_TEXT:
        assert needle not in text, needle
    tags = {tag.get("name") for tag in schema.get("tags", [])}
    for operation in (op for item in schema["paths"].values() for op in item.values()):
        tags.update(operation.get("tags", []))
    assert "admin" not in {str(tag).lower() for tag in tags}, tags
    names = schema_property_names(schema)
    bad = [name for name in names if name.startswith("Admin") or name in FORBIDDEN_SCHEMAS]
    assert not bad, bad


# --- 1. the live schema ----------------------------------------------------------------------


async def test_openapi_json_with_all_features_has_no_admin(full: KeysApp) -> None:
    async with full.client() as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200, response.text
    schema = response.json()
    # Sanity: the key endpoints are really on, so the check is not vacuous.
    assert any("/keys" in path for path in schema["paths"]), list(schema["paths"])
    assert_no_admin(schema)


# --- 2. committed artefacts ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["docs/openapi.json", "examples/types.ts"])
def test_committed_artifact_has_no_admin(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8").lower()

    assert "admin" not in text


def test_docs_openapi_json_passes_the_schema_checks() -> None:
    assert_no_admin(json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")))


# --- 3. anonymous texts ----------------------------------------------------------------------


@pytest.mark.parametrize("path", ANON_PAGES)
async def test_anonymous_page_does_not_mention_admin(full: KeysApp, path: str) -> None:
    async with full.client() as client:
        response = await client.get(path)

    assert response.status_code == 200, (path, response.status_code)
    for needle in ANON_FORBIDDEN:
        assert needle not in response.text, (path, needle)


# --- 4. regression: the admin routes still work ----------------------------------------------


async def test_admin_runs_with_valid_token_returns_200(full: KeysApp) -> None:
    async with full.admin() as client:
        response = await client.get(RUNS)

    assert response.status_code == 200, response.text
    assert set(response.json()) >= {"items", "total"}


async def test_admin_runs_without_token_returns_401(full: KeysApp) -> None:
    async with full.client() as client:
        response = await client.get(RUNS)

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthorized"


# Without ADMIN_TOKEN the routes answer 404: tests/api/v2/test_admin.py
# (test_admin_routes_without_admin_token_return_404 and ..._are_not_in_openapi).


# --- 5. every admin route is hidden and protected --------------------------------------------


def flatten_routes(routes: list[Any]) -> list[Any]:
    """Leaf routes of the app. Newer FastAPI keeps included routers as lazy groups whose
    `effective_candidates()` give routes with the final path, schema flag and dependencies."""
    leaves: list[Any] = []
    for route in routes:
        if hasattr(route, "effective_candidates"):
            leaves.extend(flatten_routes(route.effective_candidates()))
        else:
            leaves.append(route)
    return leaves


def dependency_calls(route: Any) -> set[Any]:
    calls: set[Any] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            calls.add(dependant.call)
        stack.extend(dependant.dependencies)
    calls.update(dep.dependency for dep in route.dependencies)
    return calls


def test_every_admin_route_is_hidden_and_requires_the_admin_token(full: KeysApp) -> None:
    app: FastAPI = full.app
    routes = flatten_routes(list(app.routes))
    admin_routes = [
        route
        for route in routes
        if "/admin" in getattr(route, "path", "")
        or (
            getattr(route, "dependant", None) is not None
            and require_admin in dependency_calls(route)
        )
    ]
    expected = len(admin_module.router.routes) + len(admin_module.accounts_router.routes)

    assert expected > 0
    assert len(admin_routes) == expected, [getattr(route, "path", route) for route in admin_routes]
    for route in admin_routes:
        assert route.dependant is not None, route.path
        assert route.include_in_schema is False, route.path
        assert require_admin in dependency_calls(route), route.path
