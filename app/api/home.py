"""Public landing page ``GET /`` (docs/API.md, "GET /"): static HTML, not rate limited."""

import html
from functools import lru_cache
from pathlib import Path
from string import Template

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"
PAGE_CACHE_CONTROL = "public, max-age=3600"

router = APIRouter()


@lru_cache(maxsize=1)
def _page_template() -> Template:
    return Template(PAGE_PATH.read_text(encoding="utf-8"))


def request_origin(request: Request) -> str:
    """Scheme and host of the request (uvicorn applies trusted X-Forwarded-Proto/For)."""
    return str(request.base_url).rstrip("/")


def render_home(origin: str) -> str:
    # The origin comes from the Host header: escape it before it reaches the page.
    return _page_template().substitute(origin=html.escape(origin, quote=True))


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    response = HTMLResponse(
        render_home(request_origin(request)),
        headers={"Cache-Control": PAGE_CACHE_CONTROL},
    )
    if request.method == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response
