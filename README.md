# Mars Rover API

An open-source, **unofficial** replacement for the archived Mars Rover Photos API.

The original API (api.nasa.gov/mars-photos, built by Chris Cerami) was retired in October 2025.
This service ingests JPL raw-image feeds into PostgreSQL and serves them through:

- **v1** — compatible with the original API: change the host, keep your code
  (`/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz`);
- **v2** — a richer API: all image sizes and sample types, real capture timestamps, consistent errors.

Status: stage 0 (project skeleton). See `docs/PLAN.md`.

Not affiliated with or endorsed by NASA or JPL. Images courtesy of NASA/JPL-Caltech and instrument partners.

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

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

To be decided before the first public release.
