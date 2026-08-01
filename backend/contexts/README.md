# Bounded contexts

Each sub-directory is an **independent** bounded context per `REQUIREMENTS.md` §23. The target design is that contexts never import each other and communicate only via:

- Domain events published via `shared_kernel.realtime.pubsub` for async fan-out.
- Direct calls **only** from `app.api` routers into `contexts.{X}.interfaces` facades.

In practice this isolation is not fully machine-enforced today — see the note under "Layer rules" below and `backend/README.md`'s Architecture Rules section for the tracked gap (`docs/audit-2026-05-07.md`).

## Layer rules

| Layer | Allowed imports | Forbidden |
|---|---|---|
| `domain/` | `shared_kernel` (types/errors/events only) | `fastapi`, `sqlalchemy`, `httpx`, `redis`, `hvac`, `arq`, any other context |
| `application/` | own `domain`, `shared_kernel`, ports defined in `infrastructure` | HTTP/SQL drivers; other contexts |
| `infrastructure/` | own `domain`, own `application`, `shared_kernel`, 3rd-party drivers | HTTP framework; other contexts |
| `interfaces/` | own `application`, `shared_kernel`, DTOs | `domain` internals beyond re-exported types; other contexts |

Only the `domain/` row is actually enforced by import-linter (`pyproject.toml` `[tool.importlinter]`), and only for 11 of the 14 contexts below (`identity`, `tenancy`, `keys`, `agents`, `knowledge`, `conversation`, `workflow`, `orchestration`, `audit`, `notification`, `skills` — `activities`, `agent_groups`, and `prompt_studio` aren't wired into the contract yet). The "any other context" / "other contexts" forbidding cross-context imports at the `application/`/`infrastructure/`/`interfaces/` layers is **not enforced**: those contracts were deliberately deferred (e.g. `identity`/`tenancy`/`keys` reach into `notification.application` directly today).

## Context responsibilities

| Context | Phase | Owns |
|---|---|---|
| `identity` | C | users, admins, sessions, JWT (Transit), password hashing, email-verify |
| `tenancy` | C | orgs, projects, invites, Original Creator transfer, permission matrix |
| `keys` | D | BYO api_keys, key_projects carry, Key Groups (ordered priority), search_keys, envelope encryption |
| `agents` | E, G | agents (versionless), MCP tools, wake-up, A2A, approvals (agent-only), instruct, sub-agents |
| `agent_groups` | post-J | agent grouping within a project, membership management, per-project trust boundary (owner-centric GraphRAG concept maps) |
| `knowledge` | E | RAG (Qdrant), GraphRAG (Neo4j+Qdrant 2PC 1:1 with agent) |
| `conversation` | F | workspaces, chatrooms, messages, WS, tus uploads, guest links (permanent) |
| `activities` | post-J | structured, scored interactive activities embedded in a room (§30): activity types, sessions, submissions; in-process/MCP/webhook validators |
| `orchestration` | G | per-agent A2A streams (Redis), wakeup/self-modify/refresh, instruct chain depth=5, sub-agent inheritance |
| `workflow` | H | versionless workflows, SEL v1, 11 executors, workflow_runs/steps |
| `prompt_studio` | post-J | prompt playground (§29): platform/org/user-scoped assistant configs, reference-file ingestion, reusable prompt templates, ephemeral test-chat sessions |
| `audit` | C, I | append-only audit_logs, redaction, admin queries |
| `notification` | I | in-app notifications (R18.01/R18.02 five kinds only) |
| `skills` | post-J | reusable Skill bundles (§31) at agent/project/org/platform scope, bound explicitly to agents; soft-delete/restore/copy, index builder |

"Phase" reflects the original lettered build plan (`docs/implement/00-overview.md` §0.1, phases A–J). `activities`, `agent_groups`, `prompt_studio`, and `skills` were added afterward through individual task specs under `docs/tasks/` rather than a single lettered phase, hence "post-J".
