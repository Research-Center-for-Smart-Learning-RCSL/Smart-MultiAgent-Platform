# SMAP Backend

The backend is built with Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, Arq, and loguru, and integrates PostgreSQL, Redis, Qdrant, Neo4j, MinIO, and HashiCorp Vault with Prometheus and OpenTelemetry observability. It is organized as a Domain-Driven Design (DDD) system with distinct bounded contexts, each responsible for a specific business capability.

## Project Layout

The codebase follows DDD principles (see `REQUIREMENTS.md` §23) with clear separation of concerns across fourteen bounded contexts:

```
app/                 # Application entry point: FastAPI app, worker processes, request routing
  api/               # HTTP routers (minimal logic; delegate to context interfaces)
  bootstrap/         # App startup initializers (seed data, startup hooks)
  config/            # Configuration via pydantic-settings
  plugins/           # Pluggable extensions (e.g. activity validators)
  wiring/            # Cross-context wiring glue (e.g. knowledge ingestion)
  workers/           # Background job entrypoints (Arq)
  main.py            # FastAPI ASGI application
contexts/            # Fourteen bounded contexts (independent business domains)
  identity/          # User accounts, admin roles, sessions, JWT, password management
  tenancy/           # Organizations, projects, membership, invites, OC transfers
  keys/              # Bring-your-own-key support for LLM/embedding/search services
  agents/            # Agent definitions, MCP tools, built-in tools (file; web_search via Tavily/Brave/Serper/Google CSE; code_exec on a session-scoped Code-Interpreter kernel)
  agent_groups/      # Grouping of agents within a project (e.g. GraphRAG owner-centric concept maps), membership management, per-project trust boundary
  knowledge/         # RAG (with citations + per-agent scoping) and GraphRAG (Neo4j + Qdrant)
  conversation/      # Workspaces, chatrooms, messages, WebSockets, tus uploads, guests, exports
  orchestration/     # A2A streams, wakeup configs, instructions, sub-agent inheritance
  workflow/          # Workflow definitions, SEL v1, 11 executors, 6 triggers, runs FSM (has an extra sel/ subpackage alongside the standard four layers)
  activities/        # Structured, scored interactive activities embedded in a room: activity types, sessions, submissions; validators can be in-process, MCP, or webhook
  prompt_studio/     # Prompt playground: platform/org/user-scoped assistant configs, reference-file ingestion, reusable prompt templates, bounded ephemeral test-chat sessions
  skills/            # Reusable Skill bundles (name, description, markdown body, files) at agent/project/org/platform scope, explicitly bound to agents
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
  text_extraction/   # PDF/DOCX/text parsing for knowledge and prompt_studio ingestion
tests/               # Test suites: unit, integration, e2e, and wiring (cross-context tripwires against real Postgres/Redis/MailHog, run by the backend-wiring CI job)
```

## Architecture Rules

The codebase enforces separation of concerns through the following rules:

1. **Layered architecture** (enforced by import-linter): Dependencies flow inward (`domain` ← `application` ← `infrastructure` ← `interfaces`). Inner layers never depend on outer layers. This contract currently covers 11 of the 14 contexts — `identity`, `tenancy`, `keys`, `agents`, `knowledge`, `conversation`, `workflow`, `orchestration`, `audit`, `notification`, `skills`. `activities`, `agent_groups`, and `prompt_studio` are not yet wired into the contract.

2. **Framework-agnostic domain** (enforced by import-linter, same 11 contexts as above): Domain logic contains no framework dependencies (`fastapi`, `sqlalchemy`, `httpx`, `redis`, `hvac`, `arq`, `minio`, `neo4j`, `qdrant_client`, `prometheus_client`). This keeps business logic testable and portable.

3. **Context isolation** (target state, not currently machine-enforced): Bounded contexts should not directly import each other — shared, cross-cutting concerns belong in `shared_kernel`, and coordination across contexts should happen at the application layer through each context's interface facade. In practice this is violated in a few places today (e.g. `identity`/`tenancy`/`keys` reach into `notification.application` directly instead of `notification.interfaces.facade`, and `knowledge`/`agents`/`orchestration` interleave for shared search/agent state). The import-linter contracts that previously enforced this were deliberately deferred pending a multi-context refactor — see `docs/audit-2026-05-07.md` for the tracked follow-up.

4. **Thin request routing** (target state, not currently machine-enforced): HTTP routers in `app.api.*` are meant to only import from `contexts.*.interfaces`, keeping domain and infrastructure logic out of the API layer. Several routers currently call `.application` services that transitively import `.infrastructure` repositories, because a port abstraction is still missing on top of the SQLAlchemy repos.

To verify the currently-enforced rules, run `make lint-backend` to execute import-linter checks.

## Common Commands

Run these commands from the project root. All backend tasks are invoked through the top-level Makefile:

```bash
make install-backend           # Install dependencies (includes development extras)
make fmt-backend                # Format code with ruff and apply automatic fixes
make lint-backend               # Run ruff linter and import-linter for architecture checks
make typecheck-backend          # Type-check with mypy (strict mode for domain + shared_kernel)
make test-backend               # Run pytest test suite
make dev-backend                # Start development server (uvicorn on port 8000)
```

## Getting Started

1. Install dependencies: `make install-backend`
2. Run tests to verify the setup: `make test-backend`
3. Start the development server: `make dev-backend`
4. Before committing changes, run: `make fmt-backend && make lint-backend && make typecheck-backend`

## Contributing

When adding new features, keep the following principles in mind:

- **Business logic belongs in `domain`**: Write use-case logic in `application`, external integrations in `infrastructure`.
- **Maintain isolation**: Avoid cross-context dependencies. Use `shared_kernel` for shared, cross-cutting concerns and coordinate across contexts through their interface facades. This is the target state described in Architecture Rules above, not something the linter fully enforces yet — new code should not add to the existing gap.
- **Keep routers thin**: API routers should delegate to context interfaces, not contain business logic.
- **Test thoroughly**: Write tests at the appropriate level (unit for domain, integration for application/infrastructure, wiring for real cross-service tripwires).

For detailed architectural guidance, refer to `REQUIREMENTS.md` and existing context implementations.
