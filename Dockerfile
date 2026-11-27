# syntax=docker/dockerfile:1
#
# Mars Rover API application image.
# Build:  docker build -t mars-rover-api .
# The image never runs migrations on start; run `alembic upgrade head` as a separate step
# (see deploy/README.md).

# Base images are pinned by tag. To pin by digest, resolve it once (needs network) and append it:
#   docker buildx imagetools inspect python:3.13.18-slim-trixie   # line "Digest: sha256:..."
#   ARG PYTHON_IMAGE=python:3.13.18-slim-trixie@sha256:<digest>
# (same for the uv and node images). Dependabot (docker) proposes updates of tag and digest.
ARG PYTHON_IMAGE=python:3.13.18-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.8
ARG NODE_IMAGE=node:26.13.1-alpine

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Frontend: build the Sol Index UI into /frontend/dist (Node stays in this stage only)
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend

WORKDIR /frontend

# npm dependencies first, so source changes do not invalidate this layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Builder: resolve the locked runtime dependencies into /app/.venv
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so code changes do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Runtime: no uv, no build cache, non-root user
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# FORWARDED_ALLOW_IPS is read by uvicorn for --forwarded-allow-ips;
# set it to the reverse proxy address in production.
# UVICORN_LIMIT_CONCURRENCY is read by uvicorn for --limit-concurrency: above this many open
# connections or requests in flight uvicorn answers 503 "Service Unavailable" (text/plain)
# before the application sees the request. Caddy does not cap its connections to the app (no
# keep-alive to it either), so this is the ceiling for everything that reaches the app and the
# safety net for callers that bypass Caddy (127.0.0.1:8000); far above the DB pool (10 + 5).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    UVICORN_LIMIT_CONCURRENCY=384 \
    FRONTEND_DIST_DIR=/app/frontend/dist

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app /app
# Built UI, served by the app at / (FRONTEND_DIST_DIR defaults to frontend/dist under /app).
COPY --from=frontend /frontend/dist /app/frontend/dist

# On-disk cache of converted MER images (MER_IMAGE_CACHE_DIR, default data/mer-images).
# Writable by `app`; /app itself stays root-owned. A named volume mounted here inherits
# this owner on first use (deploy/laptop/compose.yml).
RUN mkdir -p /app/data/mer-images && chown -R app:app /app/data

USER app
EXPOSE 8000

# /health answers 503 (JSON) when the database is unreachable, which marks the container
# unhealthy. If uvicorn is ever saturated (UVICORN_LIMIT_CONCURRENCY; behind Caddy this needs
# a direct flood on 127.0.0.1:8000) it answers the plain-text 503 "Service Unavailable" to every
# request, /health included: the process is alive and a restart would not help, so exactly that
# answer does not count as unhealthy (host-check.sh and the external monitor still alert).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request as r, urllib.error as e\ntry:\n    r.urlopen('http://127.0.0.1:8000/health', timeout=4)\nexcept e.HTTPError as x:\n    sys.exit(0 if x.code == 503 and x.read(64) == b'Service Unavailable' else 1)"]

# One process: the in-process scheduler must not be multiplied by workers.
# --forwarded-allow-ips is taken from FORWARDED_ALLOW_IPS and --limit-concurrency from
# UVICORN_LIMIT_CONCURRENCY (uvicorn reads both from the environment natively).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
