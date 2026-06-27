# Agent Tools Restructure — Overview

A focused, post-v1 enhancement that replaces the current "everything is an MCP
binding" model with an explicit **Agent Tools** taxonomy. It is sequenced as six
phases (A–F), each in its own file with numbered sub-steps, deliverables, exact
file targets, and exit criteria — directly followable by an engineer.

This plan layers on top of the completed A–M build (`docs/implement/`). It does
**not** change `REQUIREMENTS.md` behavior except where explicitly noted; it
reorganizes the tool surface and adds three capabilities (File Search tool,
Code-Interpreter designer uploads, Function tool).

## 0.1 Problem statement

Today every agent tool is persisted in a single table `agent_mcp_servers` with a
`source ∈ {builtin, url, package}` discriminator. The three built-in tools
(`web_search`, `code_exec`, `file`) are stored as `source='builtin'` rows and
gated through a *separate* `PUT /builtin-tools` endpoint plus a legacy
"no-binding ⇒ all-on" back-compat rule (`builtin_tools.py::_enabled_builtins`).
The domain entity is literally named `McpBinding`. The result: built-in tools and
real MCP servers are the same concept in storage and UI, which is the source of
the "everything is under MCP" confusion.

## 0.2 Target taxonomy

Two groups, seven tool types (the OpenAI Agents-SDK split, reinterpreted for a
server platform — see §0.4):

| Group | Tool type (`agent_tool_type`) | Status before | Phase |
|---|---|---|---|
| Hosted | `hosted_mcp` | exists (url/package) | A |
| Hosted | `hosted_web_search` | exists (builtin) | A |
| Hosted | `hosted_code_interpreter` | exists (builtin `code_exec`) | A |
| Hosted | `hosted_file_workspace` | exists (builtin `file`) | A |
| Hosted | `hosted_file_search` | RAG exists as context-injection, **not a tool** | C |
| Local | `local_function` | **not implemented** | E |
| Local | `local_shell` | **not implemented** — FE stub only | F |

## 0.3 Locked decisions (from the design Q&A)

1. **Data model.** Full unification: rename the concept to `agent_tools` with a
   `tool_type` enum + explicit `enabled` flag. Removes the `source='builtin'`
   overload and the legacy gate. (§A)
2. **"Local" semantics.** SMAP is a self-hosted **server** platform; there is no
   persistent client. "Local" tools execute **server-side**: `local_function` is
   an outbound HTTP/webhook call made by SMAP through the Egress Proxy;
   `local_shell` would be bash in the gVisor sandbox (deferred).
3. **Scope this round.** Reorg (A, B) + File Search (C) + Code-Interpreter
   uploads (D) + Function end-to-end (E). **Local Shell is frontend-only** this
   round: a visible card that shows a "coming soon" state on click; the backend
   does not build or accept it. (§F)
4. **`file` built-in** keeps a home as its own Hosted card **File workspace**
   (`hosted_file_workspace`) — behavior unchanged.
5. **File Search** reuses the entire existing RAG ingestion + retrieval pipeline
   and the per-document `rag_documents.agent_ids` allowlist (migration 0035). It
   adds an agent-callable `file_search` tool and surfaces per-agent uploads in
   the agent's own Tools UI. It is **additive and default-off**; the existing
   always-inject RAG path (Knowledge tab) is untouched.
6. **Code-Interpreter uploads** use **MinIO as the source of truth** and hydrate
   the per-agent `/workspace` volume on use, reusing the existing
   `stage_kernel_inputs` archive path.

## 0.4 "Hosted" vs "Local" for a server platform

OpenAI's Agents SDK runs "local" tools in the developer's own process. SMAP has
no such client: agents run inside SMAP's backend/worker. We keep the two labels
as a **mental grouping** the user already expects, with this concrete meaning:

- **Hosted** — SMAP executes the tool inside its own managed infrastructure
  (sandbox, RAG pipeline, search adapters, MCP runtime).
- **Local** — the tool's effect is a single, well-scoped action SMAP performs on
  the user's behalf: `local_function` = one outbound HTTP call (allowlisted, IP-
  screened, through the Egress Proxy); `local_shell` = one bash run in the
  gVisor sandbox. The grouping signals "user-defined imperative action" vs.
  "managed capability".

## 0.5 The `agent_tools` data model (authoritative)

```
agent_tools (
  id           uuid pk default gen_random_uuid(),
  agent_id     uuid not null references agents(id) on delete cascade,
  tool_type    agent_tool_type not null,
  enabled      boolean not null default true,
  display_name text null,                 -- mcp / function rows only
  config       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);
-- one row per singleton hosted tool per agent
CREATE UNIQUE INDEX uq_agent_tools_singleton ON agent_tools (agent_id, tool_type)
  WHERE tool_type IN ('hosted_web_search','hosted_code_interpreter',
                      'hosted_file_workspace','hosted_file_search');
CREATE INDEX ix_agent_tools_agent ON agent_tools (agent_id);

agent_tool_type = enum(
  'hosted_mcp','hosted_web_search','hosted_code_interpreter',
  'hosted_file_workspace','hosted_file_search','local_function','local_shell'
);
```

**Two row shapes:**

- **Singleton hosted** (`hosted_web_search`, `hosted_code_interpreter`,
  `hosted_file_workspace`, `hosted_file_search`): exactly **one row per agent**,
  auto-provisioned at agent creation, mutated only via `enabled` (+ minimal
  `config`). Enforced by `uq_agent_tools_singleton`.
- **Multi** (`hosted_mcp`, `local_function`): **0..N rows**, created/deleted by
  the user. `local_shell` is never persisted in this round.

**`config` per type:**

| `tool_type` | `config` shape |
|---|---|
| `hosted_mcp` | `{ "source": "url"\|"package", "reference": str, "allowed_tools": [str], "auth": <sealed?> }` |
| `hosted_web_search` | `{}` |
| `hosted_code_interpreter` | `{}` |
| `hosted_file_workspace` | `{}` |
| `hosted_file_search` | `{ "top_k": int? }` (knowledge source = the agent's `rag_config_id`) |
| `local_function` | `{ "name": str, "description": str, "parameters": <json-schema>, "http": {"method": str, "url": str, "headers": {str:str}}, "auth": <sealed?> }` |
| `local_shell` | n/a (not created) |

Sealed auth reuses the envelope marker from `mcp_service.py::_seal_auth`
(`{"__sealed__": true, ...}`), with the AAD namespace generalized to
`agent_tool_auth:<tool_id>` (§A.6).

## 0.6 Phase map

| Phase | Title | Backend | Frontend | Migrations |
|---|---|---|---|---|
| **A** | Backend `agent_tools` unification | domain `AgentTool`, table, repo, service, facade, `build_agent_tools`, `/api/agents/{id}/tools` API | — | `0036_agent_tools`, `0037_drop_agent_mcp_servers` |
| **B** | Frontend Tools reorg | — | `AgentToolsView` (Hosted/Local), tab rename, i18n `agents.tools.*`, api-client regen | — |
| **C** | File Search tool | `file_search` tool builder + facade retrieval; per-agent upload reuse | File Search card + per-agent document panel | — |
| **D** | Code-Interpreter uploads | `agent_workspace_files` table, MinIO bucket, upload API, `stage_agent_workspace_files` hydration | Upload/list/delete under Code Interpreter card | `0038_agent_workspace_files` | done |
| **E** | Function tool | `FunctionTool` via Egress Proxy, sealed auth, **signed upstream-auth channel (egress client + proxy)**, create/test API | Function form (schema + http + auth) | — | done |
| **F** | Local Shell UI stub | (reject create) | Local Shell card → "coming soon" | — | done |

## 0.7 Dependency graph

```
A ──► B ──► C
      │
      ├──► D
      └──► E ──► F (stub sits in the Local group E introduces)
```

- **B depends on A** (consumes `/api/agents/{id}/tools`).
- **C, D, E depend on A** (all add new `tool_type` rows / builders) and each
  needs B's view to expose UI, but their backends can land before B.
- **F depends on E** (the Local group/card scaffolding ships in E's FE work).

## 0.8 Conventions (inherited from `docs/implement/00-overview.md`)

- **Layer boundaries.** `app/api/v1/` → `contexts/agents/interfaces/facade.py`
  (or service via the existing router pattern) → `application/` → `infrastructure/`.
  Frontend: `app/ → slices/ → shared/`; cross-slice via `index.ts` only.
- **AuthZ tap.** Every new endpoint runs `RESOURCE_CREATE_EDIT` at the agent's
  project scope (mutations) or project membership (reads) — identical to the
  current `agents.py` handlers.
- **Audit tap.** State changes emit audit events (renamed from `mcp.binding_*` to
  `agent.tool_*`, see §A.5). Tool invocations keep `mcp.tool_invoked` with a
  `tool` discriminator so existing dashboards keep working.
- **RFC 7807.** New problems use `https://smap.local/problems/…`. Function egress
  denial reuses the existing `mcp-egress-denied`.
- **Migration policy.** N-1 compatible: `0036` only **adds** (`agent_tools` +
  enum + backfill); old code keeps reading `agent_mcp_servers` until the code
  cutover in the same phase merges; `0037` drops the old table **after** cutover.
- **Secrets.** Tool auth is envelope-encrypted (Vault Transit) in `config.auth`;
  never logged. No new env secret beyond the existing
  `EGRESS_PROXY_SHARED_SECRET`.
- **No emojis. i18n via `$t()`. Comments only where WHY is non-obvious.**

## 0.9 Acceptance levels

Same as the parent plan: **CODE** (impl + unit tests), **CONTRACT** (OpenAPI
frozen), **E2E** (Playwright/integration green). Each phase gate requires its
sub-steps at least at CODE; the cutover sub-steps (A.7, B.5) require E2E before
the old endpoints are removed.

## 0.10 Risk register

| Risk | Mitigation |
|---|---|
| Rename breaks N-1 compat | Split into add-then-drop (`0036` add + backfill, `0037` drop) with code cutover between. |
| Migration loses MCP auth / bindings | Backfill copies `config` verbatim (sealed auth travels unchanged); add a row-count assertion test (A.1). |
| Legacy "all-on" agents silently change behavior | Backfill materializes explicit `enabled` rows by replaying `_enabled_builtins` logic per agent (A.1). |
| File Search double-injects context | `file_search` is additive + default-off; the always-inject RAG path is untouched (C). |
| Code-Interpreter hydration latency | Manifest marker in the volume; only copy changed files; gated on `code_interpreter` enabled (D.4). |
| Function tool becomes an SSRF vector | Reuses the Egress Proxy allowlist + IP pinning; host must be on the project allowlist (E.3). |
| Function bearer auth weakens the `Authorization`-strip defense | E.6 adds an HMAC-signed upstream-auth channel computable only by the in-process caller (holds the secret), not by sandboxes (hold only a pre-signed project HMAC, verified `docker_runsc.py:272`) — SEC-H5 preserved. |

## 0.11 How to use this plan

1. Land **A** behind a feature branch; keep `agent_mcp_servers` until A.7 cutover
   tests are green, then merge `0037`.
2. Land **B** so the UI consumes `/tools`; remove the deprecated `/mcp` +
   `/builtin-tools` endpoints only after B is E2E-green.
3. **C / D / E** are independent and may proceed in parallel once A + B are in.
4. **F** rides on E's Local-group FE scaffolding.
5. Tick each phase's `∞` gate before opening the next.
