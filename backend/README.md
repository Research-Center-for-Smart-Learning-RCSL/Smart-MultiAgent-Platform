# SMAP Backend

The backend is built with Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, Arq, and loguru, and integrates PostgreSQL, Redis, Qdrant, Neo4j, MinIO, and HashiCorp Vault with Prometheus and OpenTelemetry observability. It is organized as a Domain-Driven Design (DDD) system with distinct bounded contexts, each responsible for a specific business capability.

## Project Layout

The codebase follows DDD principles (see `REQUIREMENTS.md` §23) with clear separation of concerns across ten bounded contexts:

```
app/                 # Application entry point: FastAPI app, worker processes, request routing
  api/               # HTTP routers (minimal logic; delegate to context interfaces)
  config/            # Configuration via pydantic-settings
  workers/           # Background job entrypoints (Arq)
  main.py            # FastAPI ASGI application
contexts/            # Ten bounded contexts (independent business domains)
  identity/          # User accounts, admin roles, sessions, JWT, password management
  tenancy/           # Organizations, projects, membership, invites, OC transfers
  keys/              # Bring-your-own-key support for LLM/embedding/search services
  agents/            # Agent definitions, MCP tools, built-in tools (file; web_search via Tavily/Brave/Serper/Google CSE; code_exec on a session-scoped Code-Interpreter kernel)
  knowledge/         # RAG (with citations + per-agent scoping) and GraphRAG (Neo4j + Qdrant)
  conversation/      # Workspaces, chatrooms, messages, WebSockets, tus uploads, guests, exports
  orchestration/     # A2A streams, wakeup configs, instructions, sub-agent inheritance
  workflow/          # Workflow definitions, SEL v1, 11 executors, 6 triggers, runs FSM
  audit/             # Append-only audit logs, redaction, admin access
  notification/      # In-app notifications and notification rules
  {X}/               # Each context follows this structure:
    domain/          # Core business logic: entities, value objects, domain events
    application/     # Use-case orchestration: coordinate domain and external ports
    infrastructure/  # External adapters: database, cache, external APIs
    interfaces/      # Facades for use by app-level routers
shared_kernel/       # Shared primitives across all contexts
  auth/              # Authentication and authorization (JWT, RBAC matrix, rate limiter, IP ban cache)
  db/                # SQLAlchemy engine, session factory, table registry
  errors/            # Error handling (RFC 7807 Problem Details, custom error base)
  i18n/              # Internationalization helpers
  infra/             # Shared external service clients (Vault, Redis buckets)
  logging/           # Structured logging via loguru with JSON + redaction
  markdown/          # Markdown rendering and sanitization helpers
  observability/     # Prometheus metrics and OpenTelemetry instrumentation
  realtime/          # WebSocket connection management and pub/sub helpers
  security/          # Envelope encryption (DEK + Vault Transit)
  storage/           # MinIO client wrapper
tests/               # Test suites (unit, integration, end-to-end)
```

## Architecture Rules

The codebase enforces separation of concerns through the following rules (validated by import-linter in `pyproject.toml`):

1. **Layered architecture**: Dependencies flow inward (`domain` ← `application` ← `infrastructure` ← `interfaces`). Inner layers never depend on outer layers.

2. **Context isolation**: Bounded contexts do not directly import each other (enforced by import-linter). Shared, cross-cutting concerns live in `shared_kernel`; coordination across contexts happens at the application layer through each context's interface facade.

3. **Thin request routing**: HTTP routers in `app.api.*` only import from `contexts.*.interfaces`. Domain and infrastructure logic remain isolated from the API layer.

4. **Framework-agnostic domain**: Domain logic contains no framework dependencies (`fastapi`, `sqlalchemy`, `httpx`, `redis`, etc.). This keeps business logic testable and portable.

To verify these rules are maintained, run `make lint` to execute import-linter checks.

## Common Commands

Run these commands from the project root. All backend tasks are invoked through the top-level Makefile:

```bash
make install-backend           # Install dependencies (includes development extras)
make fmt-backend               # Format code with ruff and apply automatic fixes
make lint-backend              # Run ruff linter and import-linter for architecture checks
make typecheck-backend         # Type-check with mypy (strict mode for domain + shared_kernel)
make test-backend              # Run pytest test suite
make dev-backend               # Start development server (uvicorn on port 8000)
```

## Getting Started

1. Install dependencies: `make install-backend`
2. Run tests to verify the setup: `make test-backend`
3. Start the development server: `make dev-backend`
4. Before committing changes, run: `make fmt-backend && make lint-backend && make typecheck-backend`

## Contributing

When adding new features, keep the following principles in mind:

- **Business logic belongs in `domain`**: Write use-case logic in `application`, external integrations in `infrastructure`.
- **Maintain isolation**: Avoid cross-context dependencies. Use `shared_kernel` for shared, cross-cutting concerns and coordinate across contexts through their interface facades.
- **Keep routers thin**: API routers should delegate to context interfaces, not contain business logic.
- **Test thoroughly**: Write tests at the appropriate level (unit for domain, integration for application/infrastructure).

For detailed architectural guidance, refer to `REQUIREMENTS.md` and existing context implementations.
