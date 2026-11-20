"""Developer page ``GET /developers`` (docs/API.md): static HTML, not rate limited.

Without a web interface build it is also served at ``/``.
"""

import html
from functools import lru_cache
from pathlib import Path
from string import Template

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.origin import request_origin

PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"
PAGE_CACHE_CONTROL = "public, max-age=3600"

_METHODS = ["GET", "HEAD"]

# ``/developers`` always; ``/`` only when there is no web interface build (create_app).
router = APIRouter()
root_router = APIRouter()


@lru_cache(maxsize=1)
def _page_template() -> Template:
    return Template(PAGE_PATH.read_text(encoding="utf-8"))


def render_home(origin: str) -> str:
    # The origin comes from the Host header: escape it before it reaches the page.
    return _page_template().substitute(origin=html.escape(origin, quote=True))


async def home(request: Request) -> HTMLResponse:
    response = HTMLResponse(
        render_home(request_origin(request)),
        headers={"Cache-Control": PAGE_CACHE_CONTROL, "X-Content-Type-Options": "nosniff"},
    )
    if request.method == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response


router.add_api_route("/developers", home, methods=_METHODS, include_in_schema=False)
root_router.add_api_route("/", home, methods=_METHODS, include_in_schema=False)
