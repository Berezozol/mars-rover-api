# Mars Rover API

[![CI](https://github.com/Berezozol/mars-rover-api/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Berezozol/mars-rover-api/actions/workflows/ci.yml)

An open-source, **unofficial** replacement for the archived Mars Rover Photos API.

The original API (api.nasa.gov/mars-photos, built by Chris Cerami) was retired in October 2025.
This service ingests JPL raw-image feeds into PostgreSQL and serves them through:

- **v1** — compatible with the original API: change the host, keep your code
  (`/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz`);
- **v2** — a richer API: all image sizes and sample types, real capture timestamps, consistent errors.

Status: public beta at <https://api.solindex.net> (web interface: <https://solindex.net>). See `docs/PLAN.md`.

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

## Radiation data (Curiosity)

`GET /api/v2/rovers/curiosity/radiation` returns one summary per sol from the RAD instrument
(NASA PDS dataset `MSL-M-RAD-3-RDR-V1.0`). Optional `sol_min` and `sol_max` (inclusive) narrow the range;
without them you get the whole history in one response, no pagination.

- `dose_b_ugy_day` and `dose_e_ugy_day` are the **absorbed dose rate** in two detectors (B: silicon, E: plastic),
  in microgray per day, as `mean`, `min`, `max`. This is not equivalent dose: there are no sieverts in the data
  and we do not convert.
- The dataset lags the mission by months and is updated every 4-5 months, so the latest sols are missing.
  Sols with no usable observation are absent.
- Other rovers answer `404 not_found`.
- The response always has `credit` and `citation`: keep them when you show or redistribute the data.
  The data belongs to the RAD team (SwRI) and NASA PDS; this project is unofficial.

```sh
curl "$BASE/api/v2/rovers/curiosity/radiation?sol_min=1600&sol_max=1700"
```

## Weather by sol (Curiosity and Perseverance)

`GET /api/v2/rovers/{rover}/weather` returns one daily summary per sol; `GET /api/v2/rovers/{rover}/weather/{sol}`
adds 24 hourly values and the source files of that sol. Only `curiosity` and `perseverance` have weather;
other rovers answer `404 not_found`. Optional `sol_min` and `sol_max` (inclusive) narrow the list; no pagination.

- Sources: Curiosity, REMS (NASA PDS `urn:nasa:pds:msl_rems`); Perseverance, MEDA (`urn:nasa:pds:mars2020_meda`).
- Units: air and ground temperature in °C, pressure in Pa, relative humidity in %, UV in W/m2, wind speed in m/s.
  Wind direction is an azimuth in degrees clockwise from north, as the source gives it; we do not say
  whether it is "from" or "to".
- `min` and `max` are the lowest and highest valid values of the sol. `mean` is the mean of the hourly means
  (local mean solar time, LMST), so a burst of readings in one hour does not skew it.
- `flags` list notes on a sol: `partial_coverage` (readings in fewer than 18 of 24 hours; `mean` is still given
  and may lean to day or night), `humidity_over_100` (values above 100 % are kept as they are),
  `out_of_range` (physically impossible values, ignored). Skip codes you do not know.
- Humidity is marked `preliminary` in `parameters`: the instrument teams treat its calibration as preliminary.
- Curiosity has no wind: the `wind` key is absent. Perseverance has wind only on sols 36-313 (`wind: null`
  elsewhere) and no UV. Ground temperature of Curiosity is measured; Perseverance's is an estimate
  (`quality: "estimate"` in `parameters`): brightness temperature from the MEDA TIRS upward infrared flux with
  emissivity 1, about ±3 K. Sol 1 and incomplete sols are not computed (`null`). `null` means the sensor exists but
  the sol has no valid value.
- The archive lags the mission by 6-8 months and is updated in batches, so the latest sols are missing
  (compare `source_max_sol` with `max_sol` from `/api/v2/rovers/{rover}`).
- The response always has `credit`: keep it when you show or redistribute the data. Perseverance:
  "Rodriguez-Manfredi, J. A.; de la Torre Juarez, M. (MEDA, Mars 2020) / NASA PDS Atmospheres Node,
  urn:nasa:pds:mars2020_meda". Curiosity: "REMS team (Centro de Astrobiologia) / NASA PDS Atmospheres Node,
  urn:nasa:pds:msl_rems". This project is unofficial.

```sh
curl "$BASE/api/v2/rovers/perseverance/weather?sol_min=100&sol_max=110"
curl "$BASE/api/v2/rovers/curiosity/weather/1000"
```

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
