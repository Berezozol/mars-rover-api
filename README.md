# Mars Rover API

An open-source, **unofficial** replacement for the archived Mars Rover Photos API.

The original API (api.nasa.gov/mars-photos, built by Chris Cerami) was retired in October 2025.
This service ingests JPL raw-image feeds into PostgreSQL and serves them through:

- **v1** — compatible with the original API: change the host, keep your code
  (`/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz`);
- **v2** — a richer API: all image sizes and sample types, real capture timestamps, consistent errors.

Status: public beta at <https://mars-api.sungrazing-menkent.ts.net> (stage 9). See `docs/PLAN.md`.

Rate limit: 60 requests per minute and 2000 per hour per client IP, no API key needed. Over the limit the API answers `429` with a `Retry-After` header (seconds). On v1 paths a response costs the larger of two numbers, at least one request: one per started 256 KiB of the body as sent to the client (after gzip), and one per started 1 MiB of the uncompressed body the server built (this also applies to HEAD and 304 responses). v2 responses always cost one request. A whole-sol v1 request without `page` for Curiosity (about 8 MB) costs about 31 requests uncompressed and 8 with `Accept-Encoding: gzip`, so gzip alone does not make it cheap: use `page` for such requests. Interactive docs: `/docs`; migration guide: `/`.

Not affiliated with or endorsed by NASA or JPL. Images courtesy of NASA/JPL-Caltech and instrument partners.

## v2 endpoints for summaries and bulk lookups

Two v2 endpoints for summaries and bulk lookups:

- `GET /api/v2/photos/stats` — photo and sol counts with first and last date, grouped by rover, camera, year or month
  (`/api/v2/photos/stats?rovers=perseverance&group_by=month`). `sample_type` accepts only `full`;
  `date_min`/`date_max` filter by the date of the sol, not of each photo.
- `GET /api/v2/photos/batch?ids=1,2,3` — up to 100 photos by id in one request. Ids that are not found
  are listed in `missing` (no `404`).

v2 photos have two time fields: `taken_at` is Earth time (UTC); `local_time`
(`{"time": "17:40:12", "kind": "lmst", "planet": "mars"}`) is the local solar time **on Mars** where the rover stood
(`lmst` mean time, `ltst` true time for Spirit and Opportunity), or `null` if the source gives none.
`GET /api/v2/photos?rovers=perseverance&near=LAT,LON&radius_km=2` also returns `near_stop` for each photo:
the rover stop it is tied to (`sol`, `site`, `drive`, `lat`, `lon`) and `distance_km` to the point.

`mast` (`{"azimuth": 309.89, "elevation": 16.9}`, degrees) is the camera direction relative to the rover body,
not to north, so it is not a compass bearing. Photo lists accept `fields=sol,img_src,camera` to return only those
keys (`id` and `credit` are always included).

## Quick start

Requirements: Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
cp .env.example .env              # PowerShell: Copy-Item .env.example .env
docker compose up -d db           # Postgres 16 on localhost:5433 (databases: mars, mars_test)
uv sync                           # create .venv and install dependencies
uv run alembic upgrade head       # apply migrations to the dev database
uv run pytest                     # tests migrate the test database themselves
uv run uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/health> and the interactive docs at <http://127.0.0.1:8000/docs>.

Useful commands:

```bash
uv run pytest -m unit             # fast tests, no database needed
uv run ruff check . && uv run ruff format .
```

## Project docs

- `docs/LEGACY-API.md` — v1 contract: behaviour of the original API, reconstructed from its code
- `docs/API.md` — v2 contract
- `docs/ARCHITECTURE.md` — layers, data model, sync strategy
- `docs/DATA-SOURCES.md` — upstream JPL feeds and their quirks
- `docs/PLAN.md` — stages and progress

## License

Copyright (C) 2026 Alexander Zolotaryev.

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License, version 3 (see [`LICENSE`](LICENSE)). If you run a modified
version as a network service, you must offer its users the corresponding source code.

Photos and their metadata come from NASA/JPL/Caltech and the other credited sources; their
`credit` is kept as published and is not covered by this license.
