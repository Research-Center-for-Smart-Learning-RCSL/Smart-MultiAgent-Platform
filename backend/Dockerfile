# syntax=docker/dockerfile:1.7

# ---------- Base — system libs + python env (shared by runtime and dev) ----------
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl ca-certificates libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- Dev — adds `[dev]` extras for tests/linters/type-check ----------
# Build with `--target dev`; bind-mount source for HMR-style iteration.
FROM base AS dev

COPY pyproject.toml ./
RUN pip install -e '.[dev]'

COPY . .

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --gid 1001 --no-create-home appuser \
 && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------- Runtime — production image, no dev deps (default target) ----------
# Listed LAST so a plain `docker build` (no --target) lands here, never on dev.
FROM base AS runtime

COPY pyproject.toml ./
# Production install: no `[dev]` extras → no pytest/mypy/ruff bloat.
RUN pip install .

COPY . .

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --gid 1001 --no-create-home appuser \
 && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
