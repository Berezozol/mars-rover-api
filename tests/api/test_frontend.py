"""Serving of the Sol Index web interface (docs/API.md "Веб-интерфейс Sol Index (этап 10)").

The build is faked: `index.html` with a marker + `assets/app-abc123.js` + `.css` in a temp
directory handed to the app as ``frontend_dist_dir`` (FRONTEND_DIST_DIR). The suite's default
settings point at a directory without a build, so the other tests never see the real
``frontend/dist``.

- `/` and every non-API path -> 200 text/html, the index.html, ``Cache-Control: no-cache``.
- `/assets/*` -> hashed files, ``public, max-age=31536000, immutable``, gzip; unknown -> 404.
- Not intercepted: /api/*, /mars-photos/*, /health, /health/sync, /docs, /redoc,
  /openapi.json, /developers.
- The interface and the assets are outside the rate limit.
- Without a build `/` is the developer page and interface paths are 404.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

INDEX_MARKER = "SOL-INDEX-FAKE-BUILD-MARKER"
SECRET_MARKER = "TOP-SECRET-OUTSIDE-DIST"
JS_BODY = "console.log('sol index');\n" * 200  # well above the gzip minimum size
CSS_BODY = "body{margin:0}\n" * 200
INDEX_HTML = (
    f"<!doctype html><html><head><title>Sol Index</title>"
    f'<link rel="stylesheet" href="/assets/app-abc123.css"></head>'
    f'<body><div id="root"></div><!-- {INDEX_MARKER} -->'
    f'<script type="module" src="/assets/app-abc123.js"></script></body></html>'
)

IMMUTABLE = "public, max-age=31536000, immutable"

UI_PATHS = [
    pytest.param("/", id="root"),
    pytest.param("/rovers/curiosity", id="rover"),
    pytest.param("/rovers/spirit/timeline?from_sol=1", id="timeline-with-query"),
    pytest.param("/photos/123", id="photo"),
    pytest.param("/some/unknown/page", id="unknown-page"),
]


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_bytes(JS_BODY.encode())
    (root / "assets" / "app-abc123.css").write_bytes(CSS_BODY.encode())
    # Next to the dist, i.e. reachable by `..` only.
    (root / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET_MARKER, encoding="utf-8")
    return root


async def _dispose(application: FastAPI) -> None:
    closer = getattr(getattr(application.state, "mer_image_service", None), "aclose", None)
    if closer is not None:
        await closer()
    await application.state.engine.dispose()


@pytest.fixture
async def ui_app(settings: Settings, dist: Path, tmp_path: Path) -> AsyncIterator[FastAPI]:
    application = create_app(
        settings.model_copy(
            update={"frontend_dist_dir": dist, "mer_image_cache_dir": tmp_path / "mer-images"}
        )
    )
    try:
        yield application
    finally:
        await _dispose(application)


@pytest.fixture
async def ui(ui_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=ui_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --- interface pages ----------------------------------------------------------------------


@pytest.mark.parametrize("path", UI_PATHS)
async def test_ui_path_returns_index_html_with_no_cache(ui: httpx.AsyncClient, path: str) -> None:
    response = await ui.get(path)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER in response.text
    assert response.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("path", UI_PATHS)
async def test_ui_path_head_has_headers_without_body(ui: httpx.AsyncClient, path: str) -> None:
    response = await ui.head(path)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers.get("cache-control") == "no-cache"


async def test_ui_root_is_not_the_developer_page(ui: httpx.AsyncClient) -> None:
    text = (await ui.get("/")).text

    assert "NASA/JPL-Caltech" not in text
    assert "mars-photos/api/v1" not in text


# --- assets -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "content_types", "body"),
    [
        pytest.param(
            "/assets/app-abc123.js",
            ("text/javascript", "application/javascript"),
            JS_BODY,
            id="js",
        ),
        pytest.param("/assets/app-abc123.css", ("text/css",), CSS_BODY, id="css"),
    ],
)
async def test_asset_is_served_with_immutable_cache(
    ui: httpx.AsyncClient, path: str, content_types: tuple[str, ...], body: str
) -> None:
    response = await ui.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in content_types
    assert response.headers.get("cache-control") == IMMUTABLE
    assert response.text == body


async def test_asset_head_has_headers_without_body(ui: httpx.AsyncClient) -> None:
    response = await ui.head("/assets/app-abc123.js")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers.get("cache-control") == IMMUTABLE


async def test_asset_is_gzipped_when_accepted(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert response.text == JS_BODY  # httpx decoded it
    assert response.headers.get("cache-control") == IMMUTABLE


async def test_asset_is_not_gzipped_without_accept_encoding(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/assets/app-abc123.js", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


@pytest.mark.parametrize(
    "path",
    ["/assets/nope.js", "/assets/nope/deeper.css", "/assets/", "/assets/app-abc123.js.map"],
    ids=["missing-js", "missing-nested", "directory", "missing-map"],
)
async def test_missing_asset_is_404_not_index_html(ui: httpx.AsyncClient, path: str) -> None:
    response = await ui.get(path)

    assert response.status_code == 404
    assert INDEX_MARKER not in response.text


async def _raw_get(app: FastAPI, path: str, raw_path: bytes) -> tuple[int, bytes]:
    """Call the app with an exact path (httpx would normalize `..` away)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "client": ("203.0.113.5", 50000),
        "server": ("test", 80),
    }
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(int(m["status"]) for m in messages if m["type"] == "http.response.start")  # type: ignore[call-overload]
    body = b"".join(
        m.get("body", b"")  # type: ignore[misc]
        for m in messages
        if m["type"] == "http.response.body"
    )
    return status, body


TRAVERSAL = [
    pytest.param("/assets/../secret.txt", b"/assets/../secret.txt", id="dotdot"),
    pytest.param("/assets/../../secret.txt", b"/assets/../../secret.txt", id="dotdot-twice"),
    pytest.param("/assets/../pyproject.toml", b"/assets/../pyproject.toml", id="project-file"),
    pytest.param("/assets/%2e%2e/secret.txt", b"/assets/%2e%2e/secret.txt", id="encoded-dots-raw"),
    pytest.param("/assets/../secret.txt", b"/assets/%2e%2e/secret.txt", id="encoded-dots-decoded"),
    pytest.param("/assets/..%2fsecret.txt", b"/assets/..%2fsecret.txt", id="encoded-slash-raw"),
    pytest.param("/assets/../secret.txt", b"/assets/..%2fsecret.txt", id="encoded-slash-decoded"),
    pytest.param("/assets/..\\secret.txt", b"/assets/..%5csecret.txt", id="backslash"),
    pytest.param("/assets//../secret.txt", b"/assets//../secret.txt", id="double-slash"),
    pytest.param("/assets/app-abc123.js/../../secret.txt", b"/x", id="through-file"),
]


@pytest.mark.parametrize(("path", "raw_path"), TRAVERSAL)
async def test_asset_path_traversal_does_not_leak_files_outside_dist(
    ui_app: FastAPI, path: str, raw_path: bytes
) -> None:
    status, body = await _raw_get(ui_app, path, raw_path)

    assert SECRET_MARKER.encode() not in body
    assert b"[project]" not in body  # pyproject.toml of the repository
    assert status in (200, 400, 404), status
    if status == 200:
        # Falling through to the interface page is acceptable; serving a file is not.
        assert INDEX_MARKER.encode() in body


@pytest.mark.parametrize(
    "url",
    [
        "/assets/%2e%2e/secret.txt",
        "/assets/..%2fsecret.txt",
        "/assets/%2e%2e%2fsecret.txt",
        "/assets/%252e%252e/secret.txt",
        "/assets/../secret.txt",
    ],
    ids=["encoded-dots", "encoded-slash", "encoded-both", "double-encoded", "plain"],
)
async def test_asset_traversal_over_http_does_not_leak_files(
    ui: httpx.AsyncClient, url: str
) -> None:
    response = await ui.get(url)

    assert SECRET_MARKER not in response.text
    assert "[project]" not in response.text
    assert response.status_code in (200, 400, 404)


async def test_missing_asset_is_json_404_in_v2_format(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/assets/nope.js")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_top_level_dist_file_is_served_with_one_hour_cache(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/robots.txt")

    assert response.status_code == 200
    assert response.text == "User-agent: *"
    assert response.headers.get("cache-control") == "public, max-age=3600"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("path", ["/", "/rovers/curiosity"], ids=["root", "page"])
async def test_non_get_on_interface_path_is_json_404_not_405(
    ui: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await ui.request(method, path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- not intercepted ----------------------------------------------------------------------


async def test_api_v2_rovers_is_still_json(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/api/v2/rovers")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert {rover["id"] for rover in response.json()["items"]} >= {"curiosity", "spirit"}


async def test_api_v2_unknown_path_is_json_404_in_v2_format(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/api/v2/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"
    assert INDEX_MARKER not in response.text


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"], ids=["v1", "v1-old-prefix"])
async def test_api_v1_unknown_path_is_json_404_in_v1_format(
    ui: httpx.AsyncClient, prefix: str
) -> None:
    response = await ui.get(f"{prefix}/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "errors" in response.json()
    assert INDEX_MARKER not in response.text


async def test_unknown_path_under_api_is_json_404(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/api/nope")

    assert response.status_code == 404
    assert INDEX_MARKER not in response.text
    assert response.headers["content-type"].startswith("application/json")


async def test_health_is_not_intercepted(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_health_sync_is_not_intercepted(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/health/sync")

    assert response.status_code in (200, 503)  # depends on data freshness, not on the interface
    assert response.headers["content-type"].startswith("application/json")
    assert "status" in response.json()
    assert INDEX_MARKER not in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_api_docs_pages_are_not_intercepted(ui: httpx.AsyncClient, path: str) -> None:
    response = await ui.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER not in response.text


async def test_openapi_json_is_not_intercepted_and_hides_interface_routes(
    ui: httpx.AsyncClient,
) -> None:
    response = await ui.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v2/rovers" in paths
    for hidden in ("/", "/developers"):
        assert hidden not in paths
    assert not [path for path in paths if path.startswith("/assets")]


async def test_developers_page_is_not_intercepted(ui: httpx.AsyncClient) -> None:
    response = await ui.get("/developers")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER not in response.text
    assert "NASA/JPL-Caltech" in response.text
    assert response.headers.get("cache-control") == "public, max-age=3600"


# --- rate limit ---------------------------------------------------------------------------


async def test_interface_and_assets_do_not_consume_the_rate_limit(
    settings: Settings, dist: Path, tmp_path: Path
) -> None:
    limited = settings.model_copy(
        update={
            "frontend_dist_dir": dist,
            "mer_image_cache_dir": tmp_path / "mer-images",
            "rate_limit_enabled": True,
            "rate_limit_per_minute": 3,
            "rate_limit_per_hour": 1000,
        }
    )
    application = create_app(limited)
    try:
        transport = httpx.ASGITransport(app=application, client=("203.0.113.77", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(10):
                assert (await client.get("/")).status_code == 200
                assert (await client.get("/rovers/curiosity")).status_code == 200
                assert (await client.get("/assets/app-abc123.js")).status_code == 200
                assert (await client.get("/assets/app-abc123.css")).status_code == 200

            # The API budget is untouched: three calls pass, the fourth is limited.
            api = [(await client.get("/api/v2/rovers")).status_code for _ in range(4)]
            assert api == [200, 200, 200, 429]

            # And the interface still works while the API is limited.
            assert (await client.get("/")).status_code == 200
            assert (await client.get("/assets/app-abc123.js")).status_code == 200
    finally:
        await _dispose(application)


# --- without a build ----------------------------------------------------------------------


@pytest.fixture
async def plain(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The default `app`: its frontend_dist_dir does not exist (no build)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://dev.example.test") as c:
        yield c


async def test_without_build_root_is_the_developer_page(plain: httpx.AsyncClient) -> None:
    response = await plain.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "unofficial" in response.text.lower()
    assert "NASA/JPL-Caltech" in response.text
    assert "dev.example.test/api/v2/" in response.text
    assert response.headers.get("cache-control") == "public, max-age=3600"


async def test_without_build_interface_path_is_404(plain: httpx.AsyncClient) -> None:
    response = await plain.get("/rovers/curiosity")

    assert response.status_code == 404


async def test_without_build_developers_page_still_works(plain: httpx.AsyncClient) -> None:
    response = await plain.get("/developers")

    assert response.status_code == 200
    assert "NASA/JPL-Caltech" in response.text


async def test_without_build_empty_dist_dir_is_treated_as_no_build(
    settings: Settings, tmp_path: Path
) -> None:
    """A directory without index.html is "no build" as well."""
    empty = tmp_path / "empty-dist"
    empty.mkdir()
    application = create_app(
        settings.model_copy(
            update={"frontend_dist_dir": empty, "mer_image_cache_dir": tmp_path / "mer-images"}
        )
    )
    try:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            root = await client.get("/")
            page = await client.get("/rovers/curiosity")
    finally:
        await _dispose(application)

    assert root.status_code == 200
    assert "NASA/JPL-Caltech" in root.text
    assert page.status_code == 404
