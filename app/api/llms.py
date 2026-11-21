"""``GET /llms.txt`` (docs/API.md): a short Markdown reference for LLM assistants.

Follows the llmstxt.org layout. The base URL comes from the request origin with the same
rules as ``/developers`` (a bad ``Host`` is replaced, see ``app.api.origin``). Not rate limited.
"""

import hashlib

from fastapi import APIRouter, Request, Response

from app.api.origin import request_origin

CACHE_CONTROL = "public, max-age=3600"
MEDIA_TYPE = "text/plain; charset=utf-8"

_METHODS = ["GET", "HEAD"]

router = APIRouter()

_TEMPLATE = """\
# Mars Rover API

> An open, unofficial replacement for the archived Mars Rover Photos API. Not affiliated with, \
endorsed by or operated by NASA or JPL. Raw photos and metadata of the Curiosity, Perseverance, \
Opportunity and Spirit rovers (and Ingenuity flights) from public NASA/JPL feeds and archives.

Base URL: {origin}

No API key is needed. An `api_key` query parameter is accepted and ignored. Rate limit per client \
IP: 60 requests per minute, 2000 per hour (`429` with `Retry-After` when exceeded). \
On v1 paths a response costs one request per started 256 KiB of the body as sent (after gzip), \
so whole-sol v1 requests without `page` should use `Accept-Encoding: gzip` or `page`.

## v1 (compatible with the old API)

Migrating: replace the host of the old API and keep the rest of the URL. Both `{origin}/api/v1` \
and `{origin}/mars-photos/api/v1` serve the same API.

- [Rovers]({origin}/api/v1/rovers): `GET /api/v1/rovers`, `GET /api/v1/rovers/{{rover}}`
- [Photos]({origin}/api/v1/rovers/curiosity/photos?sol=1000): \
`GET /api/v1/rovers/{{rover}}/photos` with `sol` or `earth_date`, `camera`, `page`, `per_page`
- [Latest photos]({origin}/api/v1/rovers/perseverance/latest_photos): \
`GET /api/v1/rovers/{{rover}}/latest_photos`
- [Photo by id]({origin}/api/v1/photos/1): `GET /api/v1/photos/{{id}}`
- [Manifest]({origin}/api/v1/manifests/curiosity): `GET /api/v1/manifests/{{rover}}`

## v2 (native API)

- [Rovers]({origin}/api/v2/rovers): `GET /api/v2/rovers`, `GET /api/v2/rovers/{{rover}}`, \
`GET /api/v2/rovers/{{rover}}/cameras`
- [Photos]({origin}/api/v2/rovers/perseverance/photos?sol=100&per_page=50): \
`GET /api/v2/rovers/{{rover}}/photos`, `GET /api/v2/rovers/{{rover}}/photos/latest`, \
`GET /api/v2/photos/{{id}}`
- [Search]({origin}/api/v2/photos?rovers=curiosity,perseverance&date_min=2026-11-06\
&sort=-taken_at): \
`GET /api/v2/photos` searches several rovers at once: `rovers` (comma-separated, default all), \
`sol`/`sol_min`/`sol_max` or `earth_date`/`date_min`/`date_max`, `camera` (up to 10 codes), \
`sort` (`-sol`, `sol`, `-taken_at`, `taken_at`), \
`local_time_min`/`local_time_max` or `golden_hour`, \
`min_width`/`min_height`, `near=LAT,LON` with `radius_km` \
(one rover only: curiosity or perseverance); `page` x `per_page` at most 10000. \
Heavy searches may get `503 service_busy` with `Retry-After`: retry later
- [Stats]({origin}/api/v2/photos/stats?rovers=perseverance&group_by=month): \
`GET /api/v2/photos/stats` (photo and sol counts with first and last date; `rovers`, `camera`, \
`sol_min`/`sol_max` or `date_min`/`date_max`, `sample_type=full` only, \
`group_by=rover|camera|year|month`). Dates filter by the date of the sol, not of each photo
- [Batch]({origin}/api/v2/photos/batch?ids=81234,81235): \
`GET /api/v2/photos/batch?ids=1,2,3` (1 to 100 ids, comma-separated; \
returns `items` and `missing`, \
unknown ids are not an error)
- [Manifest]({origin}/api/v2/rovers/perseverance/manifest): \
`GET /api/v2/rovers/{{rover}}/manifest`
- [Location]({origin}/api/v2/rovers/perseverance/location?latest=true): \
`GET /api/v2/rovers/{{rover}}/location` (by `sol`, `from_sol`/`to_sol` or `latest`), \
`GET /api/v2/rovers/{{rover}}/traverse` (GeoJSON route)
- [Flights]({origin}/api/v2/rovers/ingenuity/flights): `GET /api/v2/rovers/{{rover}}/flights`, \
`GET /api/v2/rovers/{{rover}}/flights/{{n}}`
- [Samples]({origin}/api/v2/rovers/perseverance/samples): \
`GET /api/v2/rovers/{{rover}}/samples`, `GET /api/v2/rovers/{{rover}}/samples/{{number}}`
- [Feed]({origin}/api/v2/rovers/perseverance/feed.xml): \
`GET /api/v2/rovers/{{rover}}/feed.xml` (RSS of new photos)

## Docs

- [OpenAPI schema]({origin}/openapi.json): machine-readable description of every endpoint
- [Interactive docs]({origin}/docs): Swagger UI
- [Developer page]({origin}/developers): migration guide and examples

## Credit

Images courtesy of NASA/JPL-Caltech and instrument partners. Every photo has a `credit` \
field: keep it when you show or redistribute an image.
"""


def render_llms(origin: str) -> str:
    return _TEMPLATE.format(origin=origin)


def _etag(body: bytes) -> str:
    return f'"{hashlib.sha256(body).hexdigest()[:32]}"'


def _matches(request: Request, etag: str) -> bool:
    values = request.headers.getlist("if-none-match")
    return any(c.strip() in ("*", etag) for v in values for c in v.split(","))


async def llms_txt(request: Request) -> Response:
    body = render_llms(request_origin(request)).encode("utf-8")
    headers = {
        "Cache-Control": CACHE_CONTROL,
        "ETag": _etag(body),
        "X-Content-Type-Options": "nosniff",
    }
    if _matches(request, headers["ETag"]):
        return Response(status_code=304, headers=headers)
    response = Response(body, media_type=MEDIA_TYPE, headers=headers)
    if request.method == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response


router.add_api_route("/llms.txt", llms_txt, methods=_METHODS, include_in_schema=False)
