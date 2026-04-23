# Bounded contexts

Each sub-directory is an **independent** bounded context per `REQUIREMENTS.md` §23. Contexts never import each other; they communicate via:

- `shared_kernel.events` — domain-event bus for async fan-out (e.g. `user.registered` → notification context reacts).
- Direct calls **only** from `app.api` routers into `contexts.{X}.interfaces` facades.

## Layer rules (enforced by import-linter)

| Layer | Allowed imports | Forbidden |
|---|---|---|
| `domain/` | `shared_kernel` (types/errors/events only) | `fastapi`, `sqlalchemy`, `httpx`, `redis`, `hvac`, `arq`, any other context |
| `application/` | own `domain`, `shared_kernel`, ports defined in `infrastructure` | HTTP/SQL drivers; other contexts |
| `infrastructure/` | own `domain`, own `application`, `shared_kernel`, 3rd-party drivers | HTTP framework; other contexts |
| `interfaces/` | own `application`, `shared_kernel`, DTOs | `domain` internals beyond re-exported types; other contexts |

## Context responsibilities (build order — populated in phases C–I)

| Context | Phase | Owns |
|---|---|---|
| `identity` | C | users, admins, sessions, JWT (Transit), password hashing, email-verify |
| `tenancy` | C | orgs, projects, invites, Original Creator transfer, permission matrix |
| `keys` | D | BYO api_keys, key_projects carry, Key Groups (ordered priority), search_keys, envelope encryption |
| `agents` | E, G | agents (versionless), MCP tools, wake-up, A2A, approvals (agent-only), instruct, sub-agents |
| `knowledge` | E | RAG (Qdrant), GraphRAG (Neo4j+Qdrant 2PC 1:1 with agent) |
| `conversation` | F | workspaces, chatrooms, messages, WS, tus uploads, guest links (permanent) |
| `workflow` | H | versionless workflows, SEL v1, 11 executors, workflow_runs/steps |
| `audit` | C, I | append-only audit_logs, redaction, admin queries |
| `notification` | I | in-app notifications (R18.01/R18.02 five kinds only) |
