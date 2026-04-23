# SMAP backend

Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 async + Alembic + Arq + loguru.

## Layout (DDD bounded contexts, `REQUIREMENTS.md` §23)

```
app/                 # Composition root — FastAPI app + workers + thin routers
  api/               # Routers (thin); call contexts.*.interfaces facades only
  config/            # pydantic-settings
  workers/           # Arq worker entrypoints
  main.py            # FastAPI ASGI app
contexts/            # Nine bounded contexts; each is independent
  identity/          # users, admins, sessions, JWT, password hashing
  tenancy/           # orgs, projects, invites, OC transfer, permission matrix
  keys/              # BYO LLM/embed/rerank/search keys, Key Groups, envelope enc
  agents/            # agents, MCP tools, wake-up, A2A, approvals, instruct
  knowledge/         # RAG + GraphRAG
  conversation/      # workspaces, chatrooms, messages, WS, tus uploads, guest links
  workflow/          # versionless workflows, SEL v1, 11 executors, runs/steps
  audit/             # append-only audit_logs, redaction, admin queries
  notification/      # in-app notifications (R18.01/R18.02)
  {X}/
    domain/          # Pure: entities, value objects, domain events. No framework.
    application/     # Use-case services; orchestrate domain + ports.
    infrastructure/  # Adapters: SQLAlchemy, Redis, Vault, HTTP, SMTP (drivers).
    interfaces/      # Facades imported by app/api routers.
shared_kernel/       # Cross-cutting: auth primitives, errors, events, DB base, logging
  auth/              # JWT, password, permission checks — policy-level, not context state
  db/                # SQLAlchemy declarative base, UoW/session factory
  events/            # In-process event bus
  errors/            # RFC 7807 Problem + SmapError base (A.7)
  i18n/              # Translation helpers (frontend is the primary i18n surface)
  logging/           # loguru JSON sink + redaction (A.6)
  infra/             # Vault client (Phase B), external clients shared across contexts
tests/               # unit/, integration/, e2e/
```

## SoC rules — enforced by import-linter (see `pyproject.toml`)

1. **Layered**: `domain` ← `application` ← `infrastructure` ← `interfaces` (inner layers never import outer).
2. **Context independence**: `contexts.X` cannot import `contexts.Y`. Share via `shared_kernel` or event bus.
3. **Thin routers**: `app.api.*` imports `contexts.*.interfaces` only — never `.domain` or `.infrastructure`.
4. **Framework-free domain**: No `fastapi`, `sqlalchemy`, `httpx`, `redis`, `hvac`, `arq` imports in any `contexts.*.domain`.

Run `lint-imports` (via `make lint`) to verify.

## Commands

```bash
make -C .. install-backend     # install with dev extras
make -C .. fmt-backend         # ruff format + fix
make -C .. lint-backend        # ruff + import-linter
make -C .. typecheck-backend   # mypy (strict on domain + shared_kernel)
make -C .. test-backend        # pytest
make -C .. dev-backend         # uvicorn :8000
```
