# syntax=docker/dockerfile:1
#
# Mars Rover API application image.
# Build:  docker build -t mars-rover-api .
# The image never runs migrations on start; run `alembic upgrade head` as a separate step
# (see deploy/README.md).

ARG PYTHON_IMAGE=python:3.13.18-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.8

FROM ${UV_IMAGE} AS uv

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
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    FORWARDED_ALLOW_IPS=127.0.0.1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app /app

# On-disk cache of converted MER images (MER_IMAGE_CACHE_DIR, default data/mer-images).
# Writable by `app`; /app itself stays root-owned. A named volume mounted here inherits
# this owner on first use (deploy/laptop/compose.yml).
RUN mkdir -p /app/data/mer-images && chown -R app:app /app/data

USER app
EXPOSE 8000

# /health answers 503 when the database is unreachable, which marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# One process: the in-process scheduler must not be multiplied by workers.
# --forwarded-allow-ips is taken from FORWARDED_ALLOW_IPS (uvicorn reads it natively).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
