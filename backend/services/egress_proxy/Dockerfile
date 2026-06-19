# syntax=docker/dockerfile:1.7
# Egress Proxy — standalone FastAPI forward-proxy for MCP sandbox traffic (R12.04).
# Build context: backend/ directory (same as the main backend Dockerfile).

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Minimal dependency set — fastapi + uvicorn to serve, httpx to forward,
# sqlalchemy[asyncio] + asyncpg for the allowlist DB query. No full backend
# bloat (no arq, redis, neo4j, boto3, ...).
RUN pip install \
    "fastapi==0.137.*" \
    "uvicorn[standard]==0.32.*" \
    "httpx==0.28.*" \
    "sqlalchemy[asyncio]==2.0.*" \
    "asyncpg==0.30.*"

# Copy entire backend source tree. The proxy imports two thin modules beyond
# its own package:
#   contexts.agents.infrastructure.mcp_tables  (SQLAlchemy Table definition)
#   shared_kernel.db                           (MetaData singleton)
COPY . .

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --gid 1001 --no-create-home appuser \
 && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c \
  "import urllib.request,sys;\
   sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).status==200 else 1)"

CMD ["uvicorn", "services.egress_proxy.main:app", "--host", "0.0.0.0", "--port", "8080"]
