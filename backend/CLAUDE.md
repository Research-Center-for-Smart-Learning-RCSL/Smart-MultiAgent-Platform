# Backend — Python 3.12 / FastAPI / DDD

## Stack

- **Runtime**: Python 3.12, FastAPI 0.137, Uvicorn 0.32
- **Async DB**: SQLAlchemy 2.0 (asyncpg), Alembic 1.13
- **Worker**: Arq 0.26 (Redis-backed task queue)
- **Validation**: Pydantic 2.9 with email plugin
- **Auth**: Argon2id hashing, JWT via Vault Transit (RS256), session cookies
- **Lint**: Ruff (line-length 110, rules: E/F/W/I/B/UP/N/S/A/C4/PT/RET/SIM/TID/PL/RUF)
- **Types**: mypy with pydantic plugin; strict mode on `contexts.*.domain.*` and `shared_kernel.*`

## DDD Structure

Each bounded context in `contexts/` follows this layout:

```
contexts/{name}/
  domain/         Models, enums, domain errors (pure Python, no framework imports)
  application/    Services — orchestrate domain logic, call repositories
  infrastructure/ SQLAlchemy tables, repository implementations, external adapters
  interfaces/     Facade (public API for other layers), error mappers, decorators
```

**Import rules:**
- `app/api/v1/` calls only `contexts/*/interfaces/facade.py` — never reach into application/ or infrastructure/
- `application/` depends on `domain/` and repository interfaces — never on SQLAlchemy directly
- `infrastructure/` implements repository interfaces defined in `application/`
- `shared_kernel/` is cross-cutting — may be imported by any context but never imports from a context

## API Routes

38 route files in `app/api/v1/`. WebSocket routes in `app/api/ws/` (6 files).

Route handlers must:
1. Validate input via Pydantic request models
2. Call the context facade — never instantiate services directly
3. Return Pydantic response models — never return raw dicts or ORM objects
4. Use `Depends()` for auth, pagination, and rate limiting

## Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description"

# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

Migrations are in `alembic/versions/` (0000–0035). Each migration must be forward-compatible (old code runs on new schema).

## Testing

```bash
# Unit tests (no DB/Redis required)
pytest tests/unit/ -q

# Integration tests (requires Postgres + Redis)
pytest tests/integration/ -q -m integration

# Wiring tests (full stack with Vault)
pytest tests/ -q -m wiring

# With coverage
pytest -q --cov=app --cov=contexts --cov=shared_kernel
```

~890 unit tests, ~65% coverage. Test files mirror source structure in `tests/unit/`.

## Lint & Type Check

```bash
ruff check .                  # lint
ruff format --check .         # format check
ruff format .                 # auto-format
mypy .                        # type check
```

## Workers (Arq)

Background tasks run via `arq` with Redis. Worker entry: `app/workers/main.py`.

Key workers: workflow execution, RAG ingestion, retention cleanup, key usage rollup, GraphRAG reconciler, notification dispatch.

## Services (Standalone)

- `services/egress_proxy/` — Forward-proxy for MCP sandbox traffic (separate Dockerfile)
- `services/mcp_supervisor/` — gVisor container lifecycle manager (separate Dockerfile)
