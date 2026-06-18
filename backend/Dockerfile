# syntax=docker/dockerfile:1.7

# ---------- Builder — compile C extensions; thrown away after wheel build ----------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.lock ./
RUN pip install --prefix=/install -r requirements.lock

# ---------- Base — runtime libs only (no compilers) ----------
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- Dev — adds `[dev]` extras for tests/linters/type-check ----------
# Build with `--target dev`; bind-mount source for HMR-style iteration.
FROM base AS dev

COPY pyproject.toml ./
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && pip install -e '.[dev]' \
 && apt-get purge -y --auto-remove build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY . .

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --gid 1001 --no-create-home appuser \
 && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------- Runtime — production image, no dev deps, no compilers ----------
# Listed LAST so a plain `docker build` (no --target) lands here, never on dev.
FROM base AS runtime

# Copy pre-built packages from builder stage (no build-essential in runtime).
COPY --from=builder /install /usr/local

COPY pyproject.toml ./
# Install the project itself (metadata only — deps already installed from lock).
RUN pip install --no-deps .

COPY . .

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --gid 1001 --no-create-home appuser \
 && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
