# SMAP — Smart Multi-Agent Platform

SMAP is a self-hosted web application for composing and conversing with groups of LLM-powered agents. Users supply their own API keys from third-party model providers (Anthropic Claude, OpenAI, Google Gemini). SMAP does not charge usage fees; model costs are billed directly by the providers to the key owner.

Deployment target: single-host Docker Compose (16-core / 32 GB). There is no cloud-managed option or SaaS tier.

---

## What SMAP does

**Key and credential management.** Users upload provider API keys stored with envelope encryption (Vault Transit backend). Keys are organized into ordered Key Groups with automatic rotation, per-key sliding-window quota tracking, and failover. A background worker emits notifications when 80% of a key's quota is consumed.

**Agent configuration.** An Agent is a named LLM persona with a system prompt, a Key Group, an optional RAG configuration, an optional Graph RAG configuration, and an optional set of MCP tool servers. Agents can exchange messages via the Agent-to-Agent (A2A) protocol and be composed into multi-agent workflows.

**Retrieval-augmented generation.** Each agent can be bound to a RAG configuration backed by Qdrant (dense vector search) and a Graph RAG configuration backed by Neo4j (graph-enhanced retrieval). Both are populated by background ingestion workers.

**MCP tool servers.** Agents can call built-in tool servers (file access, web search via Tavily, code execution) and user-provided external MCP servers. External servers run inside a gVisor-isolated Docker-in-Docker sandbox with an egress proxy.

**Chat rooms and workspaces.** Projects contain Workspaces; Workspaces contain Chat Rooms. Chat Rooms support real-time messaging over WebSocket, file attachments (resumable upload via TUS), full-text search (PostgreSQL GIN index), and export to CSV in MinIO. Permanent guest links allow external users to join a room without a platform account.

**Workflow engine.** Workflows are directed graphs of agent steps supporting 11 executor types, 5 trigger kinds, and the SMAP Expression Language (SEL v1) for dynamic routing. Execution is tracked in a `workflow_runs` finite-state machine (running / waiting / succeeded / failed / cancelled) with a 90-day archive policy.

**Multi-tenant access control.** Accounts belong to Organizations; Organizations contain Projects. Each scope has a role hierarchy (Original Creator, Org Owner, Org Member, Project Owner, Project Member, Guest). Authorization is enforced through a 24-capability permission matrix evaluated per request.

**Admin and observability.** Admins can manage users (ban, unban, soft-delete, hard-delete), promote or demote admins with a last-admin guard, manage IP ban lists (CIDR), adjust per-bucket rate-limit policies, force-transfer Original Creator status, impersonate users in read-only mode, and query or export the append-only audit log. Prometheus metrics, OpenTelemetry tracing, and structured JSON logging are included.

**Retention.** A background worker runs 16 retention policies on a configurable schedule covering messages, file attachments, exports, audit logs, workflow run archives, key usage events, soft-deleted tenancy entities, expired tokens, sessions, and more.

---

## Architecture

```
Browser (Vue 3 SPA)
    |
    | HTTPS / WebSocket
    v
Nginx  (TLS termination, gzip, static assets, WS proxy)
    |
    v
FastAPI  (stateless API gateway)
    |  AuthN/AuthZ middleware, rate limiter, request-ID injection
    |
    +---> ARQ worker pool  (background jobs: retention, RAG ingestion, export, threshold)
    |
    +---> PostgreSQL   (primary relational store, FTS GIN, append-only audit)
    +---> Redis        (session denylist, rate-limit counters, A2A streams, pub/sub)
    +---> Qdrant       (dense vector index for RAG)
    +---> Neo4j        (knowledge graph for Graph RAG)
    +---> MinIO        (object storage: chat-uploads, rag-sources, exports)
    +---> Vault        (Transit encryption, JWT signing keys, KV secrets)
    +---> MCP sandbox  (Docker-in-Docker + gVisor + egress proxy)
```

Five WebSocket endpoints provide real-time push: per-user notifications, per-chatroom messages, workflow run streaming, RAG config updates, and admin log tailing. All WebSocket connections authenticate via the `bearer.<token>` subprotocol.

---

## Technology stack

| Layer | Technology |
|---|---|
| API server | Python 3.12, FastAPI 0.115, Uvicorn 0.32 |
| Database ORM | SQLAlchemy 2.0 (async core), asyncpg 0.30 |
| Migrations | Alembic 1.13 |
| Cache / queue | Redis 5.2, ARQ 0.26 |
| Vector store | Qdrant 1.12 |
| Graph store | Neo4j 5.24 |
| Object storage | MinIO 7.2 (S3-compatible) |
| Secrets | HashiCorp Vault 2.3 (HVAC) |
| Auth | Argon2-cffi (passwords), Authlib 1.3 (JWT), Vault Transit (key rotation) |
| HTTP client | HTTPX 0.27 (async) |
| Serialization | ORJSON 3.10, Pydantic 2.9 |
| Logging | Loguru 0.7 (structured JSON) |
| Metrics | Prometheus-client 0.21 |
| Tracing | OpenTelemetry SDK 1.27 |
| Frontend | Vue 3.5, TypeScript 5.6, Vite 5.4 |
| State | Pinia 2.2, TanStack Vue Query 5.59 |
| UI | Element Plus 2.8 |
| Forms | vee-validate 4.14, Zod 3.23 |
| Linting | Ruff 0.7, MyPy 1.13, ESLint 9.16 |
| Testing | Pytest 8.3, pytest-asyncio 0.24, Vitest 2.1, Playwright (Phase J) |

---

## Repository layout

```
backend/
  app/
    api/v1/          REST route handlers (one file per resource)
    api/ws/          WebSocket endpoints
    api/middleware/  Request-ID, trusted-proxy, IP-ban, auth, rate-limit, security-headers
    config/          Pydantic settings (all SMAP_ prefixed env vars)
    workers/         ARQ task definitions
  contexts/          Ten DDD bounded contexts
    identity/        Users, sessions, email verification, password reset, admins, IP bans
    tenancy/         Organizations, projects, membership, invites
    keys/            API keys, key groups, key usage events
    agents/          Agent configs, MCP tools
    knowledge/       RAG configs, Graph RAG configs
    conversation/    Workspaces, chat rooms, messages, attachments, guests, exports
    workflow/        Workflows, workflow runs, approvals
    orchestration/   A2A streams, wakeup snapshots
    audit/           Append-only audit log
    notification/    In-app user notifications
  shared_kernel/
    auth/            JWT, RBAC matrix, rate limiter, IP ban cache, FastAPI deps
    db/              SQLAlchemy engine, session factory, table registry
    security/        Envelope encryption (DEK + Vault Transit)
    storage/         MinIO client wrapper
    audit/           Audit event emitter (shared across contexts)

frontend/
  src/
    app/             Root component, router, entry point
    shared/          API client (generated from OpenAPI), composables, transport, i18n, UI
    slices/          Feature modules: admin, auth, agents, keys, tenancy, conversation, workflow

deploy/
  compose/           Docker Compose files
  vault/             Vault policies (HCL) and bootstrap SOP

docs/
  implement/         Per-phase construction plans (A through J)
  operations.md      Operator manual
  workflow.schema.json + workflow.schema.md   Normative workflow schema and SEL v1

alembic/             Database migrations (versions 0000 through 0025+)
REQUIREMENTS.md      Software Requirements Specification (authoritative)
```

Each bounded context follows a four-layer structure enforced by import-linter: `domain` (pure Python, no framework imports), `infrastructure` (SQLAlchemy tables and external adapters), `application` (service classes), and `interfaces` (public facade and error mapping). Routers may only import from `interfaces`.

---

## Authoritative documents

| Document | Purpose |
|---|---|
| `REQUIREMENTS.md` | Software Requirements Specification. Every requirement is tagged with an ID of the form `[Rxx.yy]`. If the implementation disagrees with this document, the SRS wins. |
| `docs/implement/00-overview.md` | Ten-phase construction plan (A through J) with dependency graph and phase gate status. |
| `docs/workflow.schema.json` + `docs/workflow.schema.md` | Normative workflow JSON Schema and SMAP Expression Language (SEL v1) specification. |
| `docs/operations.md` | Operator manual: structured logging fields, health check behavior, resource limits, Alembic migration policy, bootstrap CLI, RFC 7807 error catalog, runbooks. |
| `deploy/vault/README.md` | Vault bootstrap procedure, key rotation SOP, disaster recovery scenarios. |

All other documents in this repository are derived from these files. Do not treat them as independent sources of truth.

---

## Quickstart (developer)

Prerequisites: Docker, Docker Compose v2, Python 3.12, Node.js 20+, pnpm.

```bash
cp .env.example .env          # edit for local overrides (keeps secrets out of git)
make install                  # install backend + frontend dependencies
make docker-up                # start Postgres, Redis, Qdrant, Neo4j, MinIO, Vault, Nginx, egress proxy, MCP sandbox supervisor
make bootstrap                # initialize Vault, run Alembic migrations, create MinIO buckets, set Neo4j constraints
make dev-backend              # start uvicorn with hot reload on :8000
make dev-frontend             # start Vite dev server on :5173
```

After the stack is running, `http://localhost/healthz` (via Nginx to the backend) confirms liveness. `http://localhost/readyz` confirms that all downstream dependencies (Postgres, Redis, Qdrant, Neo4j, MinIO, Vault) are reachable.

The OpenAPI documentation is available at `http://localhost/api/docs` when `SMAP_APP_DOCS_ENABLED=true` (default in dev).

---

## Configuration

All settings are controlled through environment variables with the `SMAP_` prefix. The resolution order is: Vault KV (production), environment variable, `.env` file, hardcoded default.

Section prefixes:

| Prefix | Covers |
|---|---|
| `SMAP_APP_` | Application environment, version, docs toggle |
| `SMAP_DB_` | PostgreSQL DSN, pool size, statement timeout |
| `SMAP_REDIS_` | Redis DSN |
| `SMAP_QDRANT_` | Qdrant URL and API key |
| `SMAP_NEO4J_` | Neo4j URL, user, password, database |
| `SMAP_MINIO_` | MinIO endpoint, credentials, bucket names |
| `SMAP_VAULT_` | Vault address, AppRole credentials, Transit key names |
| `SMAP_JWT_` | Access and refresh token TTLs, issuer, audience |
| `SMAP_SEC_` | Trusted proxy CIDRs, CORS origins, CSP mode |
| `SMAP_LOG_` | Log level, service name, JSON toggle |
| `SMAP_LIMITS_` | Per-bucket rate-limit counts (R19.02) |

See `.env.example` for the full list with defaults.

---

## Conventions

**Endpoint paths.** All REST paths carry the `/api/` prefix and match `REQUIREMENTS.md` section 22 exactly. WebSocket paths are at `/ws/`.

**Error format.** All error responses follow RFC 7807 Problem Details. The `type` field uses the prefix `https://smap.local/problems/`. The error catalog is maintained in `docs/operations.md` section 6.

**Storage names.** MinIO buckets: `chat-uploads`, `rag-sources`, `exports`. Redis streams: `a2a:agent:{agent_id}`. Vault Transit keys: `smap-provider-secret`, `smap-guest-link`, `smap-jwt-sign`. Use these names verbatim; do not introduce aliases.

**Requirement traceability.** Commits, pull requests, and test docstrings cite at least one `[Rxx.yy]` requirement ID. When a new requirement must be added, it goes into `REQUIREMENTS.md` before any code is written.

**Specifications are English.** All files under `docs/`, `deploy/`, and the root SRS are English-only. Pull request descriptions may use zh-TW.

---

## Build status (phases)

| Phase | Title | Status | Closed |
|---|---|---|---|
| A | Foundations and Project Bootstrap | CODE complete | 2026-04-21 |
| B | Infrastructure Bootstrap and Operations | CODE complete | 2026-04-21 |
| C | Identity, Tenancy, Access, and Web Security | CODE complete | 2026-04-21 |
| D | API Key Management | CODE complete | 2026-04-21 |
| E | Agents, RAG, Graph RAG, MCP | CODE complete | 2026-04-22 |
| F | Chat and Real-time | CODE complete | 2026-04-23 |
| G | Multi-Agent Orchestration | CODE complete | 2026-04-23 |
| H | Workflow Engine | CODE complete | 2026-04-23 |
| I | Admin, Audit, Notifications, Retention | CODE complete | 2026-04-23 |
| J | Frontend Integration, E2E, and Release | not started | — |

Full gate notes for each closed phase are in `docs/implement/00-overview.md`.

---

## License

TBD.
