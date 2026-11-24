"""``GET /llms.txt`` (docs/API.md): a short Markdown reference for LLM assistants.

Follows the llmstxt.org layout. The base URL comes from the request origin with the same
rules as ``/developers`` (a bad ``Host`` is replaced, see ``app.api.origin``). Not rate limited.
"""
# ruff: noqa: E501

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

An API key is optional. Without one the rate limit is per client IP: 60 requests per minute, 2000 per hour (`429` with `Retry-After` when exceeded). With a key (`si_` and 32 letters and digits) it is per account: 120 per minute, 10000 per hour. A key from api.nasa.gov or `DEMO_KEY` in `api_key` is ignored. On v1 paths a response costs the larger of: one request per started 256 KiB of the body as sent \
(after gzip), and one per started 1 MiB of the uncompressed body (also for HEAD and 304), \
so use `page` for whole-sol v1 requests: gzip alone does not make them cheap.

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
Heavy searches may get `503 service_busy` with `Retry-After`: retry later. \
Photos carry `taken_at` (Earth time, UTC) and `local_time` (local solar time on Mars: `time`, \
`kind` = `lmst` or `ltst`, `planet` = `mars`; `null` if unknown). With `near`, each photo also has \
`near_stop` (`sol`, `site`, `drive`, `lat`, `lon`, `distance_km`). \
`fields=sol,img_src,camera` (also on the rover photos, latest and batch lists) returns only the \
listed photo keys; `id` and `credit` are always included; it saves response size only. \
`mast` is the camera direction (`azimuth` 0-360, `elevation` -90..90, degrees) relative to the \
rover body, not to north; `null` if unknown
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
- [Radiation]({origin}/api/v2/rovers/curiosity/radiation?sol_min=1600&sol_max=1700): \
`GET /api/v2/rovers/curiosity/radiation` (Curiosity only; daily summaries of the RAD instrument, \
NASA PDS dataset MSL-M-RAD-3-RDR-V1.0; optional `sol_min`/`sol_max`, inclusive; no pagination). \
`dose_b_ugy_day` and `dose_e_ugy_day` (`mean`, `min`, `max`) are the absorbed dose rate in the \
instrument's detectors, in microgray per day: not equivalent dose, no sieverts. The dataset lags \
the mission by months, so the latest sols are missing. Other rovers: `404 not_found`. Keep the \
`credit` and `citation` fields when you show or redistribute the data
- [Weather]({origin}/api/v2/rovers/perseverance/weather?sol_min=100&sol_max=110): \
`GET /api/v2/rovers/{{rover}}/weather` (`curiosity` REMS and `perseverance` MEDA only, NASA PDS; \
daily summaries per sol; optional `sol_min`/`sol_max`, inclusive; no pagination), \
`GET /api/v2/rovers/{{rover}}/weather/{{sol}}` (the same plus 24 hourly values in `hours`, LMST, and \
`source.files`; unknown sol: `404 sol_not_found`). Units: °C, Pa, % relative humidity, W/m2 (UV), m/s. \
Wind direction: azimuth in degrees clockwise from north as the source gives it; from/to is not stated. \
`min`/`max` are the lowest and highest valid values; `mean` is the mean of the hourly means and is always \
given. `flags` (`code`, `parameter`, `count`): `partial_coverage` (readings in fewer than 18 of 24 hours), \
`humidity_over_100` (kept as is), `out_of_range` (ignored); skip unknown codes. Humidity is \
`preliminary` (see `parameters`). Curiosity has no wind: the `wind` key is absent. Perseverance has wind \
only on sols 36-313 (`null` elsewhere) and no UV; its ground temperature is an estimate \
(`quality: "estimate"`: brightness temperature from the MEDA TIRS upward infrared flux, emissivity 1, \
about ±3 K; sol 1 and incomplete sols are `null`), while Curiosity's is measured. `null` = the sensor \
exists, the sol has no valid value. The archive lags by 6-8 months, so the latest sols are missing. Other \
rovers: `404 not_found`. Keep the `credit` field when you show or redistribute the data

## API keys (optional)

- [Get a key]({origin}/keys): enter an email, open the one-time link from the email (valid 60 minutes), press the button; the key is shown once. Without the web interface: `POST /api/v2/keys/requests` with JSON `{{"email": "user@example.org"}}` answers `202` for any valid address. Key sign-up may be turned off on a server (`404`)
- Send the key in the `X-Api-Key` header (preferred) or the `api_key` parameter; the header wins. Works on v1 and v2. Wrong key: `401 invalid_api_key`; revoked: `401 api_key_revoked`; blocked account: `403 account_blocked`; on v1 these are `403` with `{{"errors": ...}}`
- `GET /api/v2/account` with the key: your keys, usage per day and limits. `POST /api/v2/account/keys` adds a key (at most 2 active), `DELETE /api/v2/account/keys/{{id}}` revokes one, `DELETE /api/v2/account` deletes the account and all its data
- [Manage keys]({origin}/keys/manage) and [delete your data]({origin}/keys/delete) (no key needed: a link comes by email). We store the email, key hashes and request counts per day

## Docs

- [OpenAPI schema]({origin}/openapi.json): machine-readable description of every endpoint
- [Interactive docs]({origin}/docs): Swagger UI
- [Developer page]({origin}/developers): migration guide, API keys and examples

## Credit

Images courtesy of NASA/JPL-Caltech and instrument partners. Every photo has a `credit` \
field: keep it when you show or redistribute an image.

Radiation data: MSL RAD (SwRI) / NASA PDS. Keep the `credit` and `citation` fields of the \
radiation response.

Weather data: Perseverance, MEDA team (Rodriguez-Manfredi, J. A.; de la Torre Juarez, M.) / NASA PDS \
Atmospheres Node; Curiosity, REMS team (Centro de Astrobiologia) / NASA PDS Atmospheres Node. Keep the \
`credit` field of the weather response.
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
