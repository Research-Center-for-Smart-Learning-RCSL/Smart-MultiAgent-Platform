# Phase E — Agents, RAG, Graph RAG, MCP

**Goal.** Ship the core "intelligence" surface: Agent definitions (composition, prompt strategy, context management, A2A), the RAG subsystem on Qdrant (ingest + retrieve + rerank), the Graph RAG subsystem on Neo4j + Qdrant under 2-phase commit, and the MCP integration including the sandbox and built-in tools (file, web search BYO, code_exec).

**Size.** XL
**Depends on.** C (auth, projects, audit), D (LLM / embedding / rerank / search keys).
**Unblocks.** F (chat invokes agents), G (orchestration), H (workflow).
**Refs.** `REQUIREMENTS.md` §9, §10, §11, §11.2a, §12, §21.1, §22.6–§22.9a.

## E.0 Scope summary

By phase close:

- An Agent can be defined per §9.1 with `name`, `model_hint`, `key_group_id`, `system_prompt`, `prompt_strategy (full|lazy)`, `rag_config_id`, `graphrag_config_id`, `mcp_servers`, `a2a_enabled`, `context_mode (general|compact)`, `context_token_cap`, `wakeup_config`, `workflow_capabilities`. Agents are **not versioned** and not templated (R9.02).
- A RAG config can be attached and exercised end-to-end; embeddings and reranks are paid for by user-supplied keys picked per-config.
- Graph RAG builds commit atomically across Neo4j + Qdrant with compensation and a reconciliation worker; 1:1 bound to an Agent (R11.05).
- MCP servers (built-in, URL, package) run inside gVisor ephemeral containers behind a FastAPI-based Egress Proxy; `file` tool uses a per-agent named Docker volume.
- Web search ships with the Tavily adapter first and the four-provider protocol behind it.

## E.1 Agents schema & CRUD — **CODE** — L

**Deliverables.**

- Alembic revision `0005_agents`:

  ```
  agents (
    id uuid pk, project_id fk projects,
    name text, model_hint enum('claude','openai','gemini'),
    key_group_id fk key_groups,
    system_prompt text,
    prompt_strategy enum('full','lazy'),
    rag_config_id fk rag_configs null,
    graphrag_config_id fk graphrag_configs null,
    context_mode enum('general','compact'),
    context_token_cap int null,
    a2a_enabled bool,
    wakeup_config jsonb,
    workflow_capabilities jsonb,
    version int not null default 1,
    created_at, deleted_at,
    UNIQUE (project_id, name) WHERE deleted_at IS NULL
  );
  agent_mcp_servers (
    id, agent_id fk agents,
    source enum('builtin','url','package'),
    reference text, allowed_tools text[], config jsonb
  );
  ```

- Endpoints (§22.6): `GET /api/projects/{pid}/agents`, `POST /api/projects/{pid}/agents`, `GET /api/agents/{id}`, `PATCH /api/agents/{id}` (requires `If-Match: <version>`), `DELETE /api/agents/{id}` (soft-delete, 60-day recovery per R9.03 / R8.11).
- **No `agent_versions` table.** Editing overwrites in place (R9.02). Optimistic-lock via `version int`.
- **No templates, no export/import** (R9.02).
- Hard cap: **1 000 agents per project** (R9.01) enforced at create.

**Key IDs.** `[R9.01]`–`[R9.03]`, §22.6, §21.1 agents.

**Exit criteria.** Create → edit (If-Match) → soft-delete flow green; second create above cap returns `https://smap.local/problems/agent-cap-exceeded`.

## E.2 Prompt Read Strategy — **CODE** — M

**Deliverables.**

- `smap/contexts/agents/prompt_loader.py`:
  - **`full`** (R9.05): entire `system_prompt` markdown sent verbatim.
  - **`lazy`** (R9.06): parse markdown into sections, each beginning with YAML front-matter `{id, title, description}`. Build an **index prompt** listing all sections' titles + descriptions. Register a built-in tool `load_prompt_section(id)` so the model can fetch a body on demand. Section bodies fetched within one turn are cached for that turn only (R9.07); next turn re-runs retrieval.
  - **Fallback** (R9.08): if the active provider does not support tool use, silently degrade to `full` and emit a UI warning.
- Invocation pipeline assembles `[system_prompt_or_index] + [compact_summary?] + [retrieved_rag?] + [retrieved_graphrag?] + chat_history`.

**Key IDs.** `[R9.04]`–`[R9.08]`.

**Exit criteria.** Unit tests cover full rendering, lazy index + section load, provider-no-tools fallback.

## E.3 Context management — **CODE** — L

**Deliverables.**

- `smap/contexts/agents/context.py`:
  - **`general`** (R9.09): send full history; provider's context-limit error surfaced to UI.
  - **`compact`** (R9.10): when next request tokens > `context_token_cap` (default = 75% of provider's context limit), run `/compact`:
    1. Select oldest un-compacted range.
    2. Call the agent's own Key Group with a Claude-Code-style system prompt ("Summarize preserving decisions, file paths, open questions. ≤ 2000 tokens").
    3. Replace the range with a single system-role message tagged `{"type":"compact_summary"}` in `messages.metadata`.
    4. User-visible transcript unchanged; only model-facing history changes.
  - `/compact` failure keeps the original context and logs an audit (`agent.compact_failed`) (R9.11).
- No `sliding` or `replace` strategies — they do not exist in the SRS.

**Key IDs.** `[R9.09]`–`[R9.11]`.

**Exit criteria.** Long-conversation test trips compaction at the cap; failure path leaves original intact.

## E.4 A2A boolean + runtime scope — **CODE** — S

**Deliverables.**

- Agents carry a plain `a2a_enabled bool`; there is **no `a2a_scope` enum**.
- Scope checker in `smap/contexts/agents/a2a_scope.py` implementing R9.17 exactly:
  - Same Project.
  - Both agents have `a2a_enabled = true`.
  - Caller's invocation context (chatroom or workflow_run) is one the callee is also attached to, **OR** callee has `wakeup_config.triggers.call_only.enabled = true`.
  - Cross-project: denied.
  - Target in soft-deleted project: denied.
- Denial returns `https://smap.local/problems/a2a-forbidden` and writes audit event `a2a.forbidden`.

**Key IDs.** `[R9.17]`.

**Exit criteria.** Scope matrix test green (same-project × enabled × shared-context × call_only combinations).

## E.5 RAG ingestion — **CODE** — L

**Deliverables.**

- Alembic revision `0006_rag`:

  ```
  rag_configs (
    id uuid pk, project_id fk projects,
    name text,
    chunk_strategy enum('fixed','semantic'),
    chunk_params jsonb,                 -- {chunk_size_tokens, chunk_overlap_tokens} or {max_tokens_per_chunk, similarity_threshold}
    embed_key_id fk api_keys null,      -- single key, not group; must have embedding capability
    embed_provider text, embed_model text,
    rerank_enabled bool,
    rerank_key_id fk api_keys null,     -- single key, rerank capability
    rerank_provider text null, rerank_model text null,
    top_k int,                          -- default 8 per R10.07
    created_at, deleted_at,
    UNIQUE (project_id, name) WHERE deleted_at IS NULL
  );
  rag_documents (
    id, rag_config_id fk, filename, mime, size_bytes,
    minio_path,
    status enum('ingesting','ready','failed','quarantined'),
    scan_status enum('pending','clean','quarantined','skipped') default 'pending',
    scan_at timestamptz null,
    uploaded_by fk users, uploaded_at
  );
  rag_chunks (id bigserial, document_id fk, chunk_idx int, text text, qdrant_point_id uuid);
  ```

- Parsers (R10.01/R10.03): `pdf` (`pypdf` + optional `tesseract` OCR), `docx` (`python-docx`), `md`, `txt`. **No HTML.**
- Chunkers (R10.04): `fixed` (default `chunk_size_tokens=512`, `chunk_overlap_tokens=64`) or `semantic` (`semantic-text-splitter`; `max_tokens_per_chunk=512`, `similarity_threshold=0.6`).
- Embedding model whitelist (R10.05): `openai:text-embedding-3-small`, `openai:text-embedding-3-large`, `gemini:text-embedding-004`, `voyage-3`. Key chosen via `embed_key_id`, must have `embedding` capability.
- Qdrant collection `rag_{project_id}` with payload `{doc_id, chunk_idx, agent_ids[]}` (§21.4).
- Upload endpoints (§22.7): `POST /api/rag-configs/{id}/documents` (multipart ≤32MB) and tus at `/api/tus` for larger (§22.15, purpose=`rag_source`).
- Permissions: upload requires **Project Owner** (R10.10); documents scoped per project, never cross-project (R10.11).

**Key IDs.** `[R10.01]`–`[R10.06]`, `[R10.10]`–`[R10.11]`, §22.7.

**Exit criteria.** Mixed-format ingest end-to-end; re-ingest of same SHA-256 skipped; capability-mismatch attach rejected.

## E.6 RAG retrieval & rerank — **CODE** — M

**Deliverables.**

- `smap/contexts/knowledge/rag/retrieve.py::query(config_id, text, *, top_k=None, rerank=None)`.
- Default `top_k=8` (R10.07); rerank per config (R10.08): Cohere `rerank-3` (uses `rerank_key_id`) **or** local `bge-reranker-v2-m3` service.
- Retrieved chunks injected immediately before the user turn as system-role message tagged `{"type":"rag"}` in `messages.metadata` (R10.09).
- Permission filter: chunks limited to documents of configs in the caller's accessible projects.

**Key IDs.** `[R10.07]`–`[R10.11]`.

**Exit criteria.** Relevance bench on labeled set + scoped leakage test.

## E.7 Graph RAG build pipeline — **CODE** — XL

**Deliverables.**

- Alembic revision `0007_graphrag`:

  ```
  graphrag_configs (
    id uuid pk, project_id fk projects,
    agent_id fk agents UNIQUE,                -- 1:1 (R11.05)
    builder_key_group_id fk key_groups,       -- distinct from consumer agent's group (R11.01)
    trigger_config jsonb,                     -- mirrors wakeup_config but counts ALL messages (R11.02)
    last_build_at timestamptz null,
    last_build_state enum('idle','running','neo4j_committed','qdrant_committed','failed_compensating','failed'),
    last_build_error text null,
    created_at, deleted_at
  );
  ```

- Triggers (R11.02): `every_n_messages` (user+agent), `silence_minutes`, `manual` (`POST /api/graphrag/{id}/build`).
- Builder worker (R11.03): consume delta since last build → extract `(subject, relation, object, confidence, evidence_msg_ids)` via the builder Key Group → upsert `(:Entity)-[:REL]->(:Entity)` nodes/edges tagged with `build_id` → embed entity descriptions to Qdrant `graphrag_{project_id}` with `build_id` payload.
- **2PC with compensation** (R11.04 / §11.2a):
  - `idle → running → neo4j_committed → qdrant_committed → idle`.
  - Phase-1 failure → `failed`, nothing committed.
  - Phase-2 failure → `failed_compensating`; a reconciliation worker (period 60s, not 10min) retries Qdrant up to 5× exp-backoff, else rolls back Neo4j via pre-build snapshot cached in Redis `graphrag:build:{config_id}:{build_id}`; final state `failed` with previous active build preserved.
- Build lock: Redis key `graphrag:lock:{config_id}` with **10-min TTL**, released on completion (R11a.01).
- Admin override (R11a.02): `POST /api/admin/graphrag/{id}/reset` force-sets `last_build_state = 'idle'`; always audited as `admin.graphrag_reset`.

**Key IDs.** §11, §11.2a, `[R11.01]`–`[R11.06]`, `[R11a.01]`–`[R11a.02]`.

**Exit criteria.** Chaos test (kill worker between phases) heals via reconciliation; admin reset documented.

## E.8 Graph RAG query — **CODE** — M

**Deliverables.**

- Hybrid retrieval (R11.06): vector search in Qdrant → top entities → 1–2 hop traversal in Neo4j → bundle entities + relations + evidence excerpts (≤ 2 KB total) as a system message `{"type":"graphrag"}` in `messages.metadata`.
- `GET /api/graphrag/{id}/status` returns last build state + size.
- `DELETE /api/graphrag/{id}` cascades Neo4j subgraph (labels `:Entity {graphrag_config_id=...}` and `:REL` with same tag).

**Key IDs.** `[R11.06]`, §22.8.

**Exit criteria.** p95 < 500ms on a 10k-entity graph.

## E.9 MCP binding & egress allowlist — **CODE** — M

**Deliverables.**

- Alembic revision `0008_mcp`:

  ```
  -- agent_mcp_servers already in E.1.
  mcp_egress_allowlist (
    id, project_id fk, hostname text,
    added_by_user_id fk users, added_at, note text null,
    UNIQUE (project_id, hostname)
  );
  ```

- Endpoints (§22.9): `GET/POST /api/agents/{id}/mcp`, `PATCH/DELETE /api/agents/{id}/mcp/{mcp_id}` (PATCH updates `allowed_tools` and `config`; preserves binding id and audit chain), `POST /api/agents/{id}/mcp/{mcp_id}/test`, `GET/PUT /api/projects/{pid}/mcp/egress-allowlist`.
- `test` runs MCP `initialize` + `tools/list` in an ephemeral sandbox with a 30s wall cap; on exceed returns `https://smap.local/problems/mcp-timeout`. Success returns `{ok, tool_names[], duration_ms, error?}`.
- URL-based MCPs: auth material (bearer / token) stored envelope-encrypted on the binding `config` jsonb (reuses D.1 envelope).

**Key IDs.** §12.1–§12.2, `[R12.01]`–`[R12.02]`, §22.9.

**Exit criteria.** URL MCP handshake probe returns tool names; package MCP image cache rebuild after 24h.

## E.10 MCP sandbox & Egress Proxy — **CODE + OPS** — L

**Deliverables.**

- Container runtime: Docker with **gVisor (`runsc`)**, Kata Containers fallback documented (§26 open item 3).
- Per-call ephemeral container (R12.03):
  - Image pinned by digest; user-provided package rebuilt at cold start, **cached per `(agent_id, version)` for 24h** (where `version` is the optimistic-lock int from E.1 — Agents are not versioned as separate rows per R9.02), purged on agent update.
  - UID ≥ 10 000, no `root`, no `sudo`, `no-new-privileges`.
  - Root filesystem read-only; 100 MB tmpfs at `/workspace`; no host volume mounts.
  - Resources: `--memory=512m --cpus=0.5 --pids-limit=128 --ulimit nofile=512`.
  - Network: only `smap_egress_net` → Egress Proxy; DNS restricted to allowlist.
  - Lifetime: `--rm` at agent turn completion.
- **Built-in `file` tool** runs in a separate container with a per-agent named Docker volume `smap-agent-fs-{agent_id}` mounted R/W at `/workspace`; quota 100 MB (`size` tmpfs option or ext4 loopback). User MCP containers **never** receive this mount. Soft-delete of an Agent retains the volume for 60 days; nightly cleanup removes expired.
- **`code_exec`** built-in (R12.05): curated image `python:3.12-slim + common scientific libs` under the same gVisor policy.
- **Egress Proxy** (R12.04): FastAPI-based HTTPS forward proxy.
  - Enforces project-scoped `mcp_egress_allowlist` hostnames.
  - Blocks RFC 1918, link-local, loopback, and cloud-metadata addresses.
  - Strips inbound `Authorization` header from the sandbox so MCPs cannot impersonate platform keys.
  - Logs every request with truncated bodies for audit.
- URL MCPs bypass the sandbox but still traverse the Egress Proxy (R12.06).

**Key IDs.** `[R12.03]`–`[R12.06]`.

**Exit criteria.** Non-allowlist egress denied and audited as `mcp.egress_blocked`; container auto-destroyed; metadata-IP SSRF test blocked.

## E.11 Built-in tools — **CODE** — M

**Deliverables.**

- **`file`** (sandboxed to the `smap-agent-fs-{agent_id}` volume; operations `list/read/write`; never leaves volume).
- **`web_search`** BYO (§12.4):
  - Table `search_keys` added in D.4 (one active per project via partial UNIQUE per §21.1).
  - Adapter Protocol (R12.11):
    ```python
    class SearchAdapter(Protocol):
        async def search(self, query: str, *, top_k: int, locale: str,
                         freshness: Literal["any","day","week","month","year"]
                         ) -> list[SearchResult]: ...
    SearchResult = {"title":str, "url":str, "snippet":str,
                    "published_at": Optional[datetime], "score": float}
    ```
  - `top_k` default 5, hard max 20; results capped at **4 KB** serialised (R12.12).
  - Cache: Redis `search:{hash(provider,query_norm,top_k,locale,freshness)}` TTL 10 min, project-scoped (R12.13).
  - Rate limit: 60 searches / min / project, admin-tunable (R12.14).
  - Missing active search key (R12.10): agents that list `web_search` in `allowed_tools` get structured `tool_unavailable: search_key_not_configured`.
  - Audit each call as `mcp.tool_invoked` with `tool="web_search"` and truncated query ≤256 chars (R12.15). Built-in tools (`file`, `web_search`, `code_exec`) share the `mcp.tool_invoked` audit event name because they flow through the same MCP tool surface per §12; disambiguate by the `tool` field.
  - Outbound via Egress Proxy with seeded hosts `api.search.brave.com`, `google.serper.dev`, `api.tavily.com`, `www.googleapis.com` (R12.16).
- **Initial adapter rollout** (R12.17): v1 ships **Tavily** end-to-end plus the plug-in framework; Brave / Serper / Google CSE ship behind the same Protocol after parity tests.
- **`code_exec`** see E.10; 30s wall cap (exceed returns `https://smap.local/problems/mcp-timeout`); stdout/stderr/exit captured.

**Key IDs.** `[R12.07]`–`[R12.17]`.

**Exit criteria.** Per-provider integration test; cache hit test; quota/denial tests.

## E.12 Frontend knowledge affordances inside `agents` slice — **CODE** — L

**Objective.** Per §24.2 `slices/agents/` owns agents + RAG + GraphRAG + MCP UI (knowledge folds into the agents slice, per SRS §24.2).

**Deliverables (within `slices/agents/`):**

- Views: `AgentListView`, `AgentEditorView` (composition panel, system prompt with lazy-section syntax awareness, RAG/GraphRAG/MCP bindings, test chat).
- RAG sub-views: `RagConfigListView`, `RagConfigDetailView`, document uploader using tus (purpose=`rag_source`, with `project_id`+`rag_config_id` metadata).
- GraphRAG sub-views: `GraphRagConfigView` (trigger editor, build status via WS channel `/ws/graphrag-configs/{id}` emitting `build.started | build.neo4j_committed | build.qdrant_committed | build.failed | build.compensating` events that mirror `last_build_state` from E.7; RAG uses the parallel `/ws/rag-configs/{id}` channel), manual-build button.
- MCP sub-views: `McpBindingView`, `EgressAllowlistView`, per-binding test button.

**Key IDs.** §24.2, §22.6–§22.9.

**Exit criteria.** Playwright: create Agent → attach RAG config → ingest doc (tus) → Q&A shows grounded answer; trigger GraphRAG build → WS progress updates → hybrid answer.

## E.∞ Phase gate

- [ ] Agents table matches §21.1 exactly; no `agent_versions`, no templates.
- [ ] 1 000-agent project cap enforced.
- [ ] `general` + `compact` contexts pass tests; `/compact` failure path preserves original.
- [ ] `prompt_strategy` lazy: section index + `load_prompt_section` tool working; fallback to `full` when tools unsupported.
- [ ] A2A scope matrix matches R9.17.
- [ ] RAG uses single `embed_key_id`/`rerank_key_id`; capability mismatch blocks attach.
- [ ] GraphRAG 1:1 with agent; reconciliation worker at 60s; admin reset endpoint live.
- [ ] MCP sandbox uses gVisor; `file` tool volume isolated; Egress Proxy strips Authorization.
- [ ] MCP binding endpoints live: `POST/PATCH/DELETE` on `/api/agents/{id}/mcp[/{mcp_id}]`, `/test` probe returns tool names, and `/api/projects/{pid}/mcp/egress-allowlist` CRUD enforces project scope.
- [ ] Tavily adapter end-to-end; cache + 4KB cap + 60/min rate limit verified.
- [ ] `00-overview.md` §0.8: E = done.

## Cross-cutting checklist

1. **AuthZ tap.** Agents/RAG/GraphRAG/MCP endpoints under capabilities 15–16 of §5.2; uploads require Project Owner (R10.10).
2. **Audit tap.** `agent.created/edited/deleted`, `rag.document_uploaded/indexed`, `graphrag.build_started/_finished`, `mcp.tool_invoked`, `mcp.egress_blocked`, `admin.graphrag_reset`.
3. **Rate limit bucket.** `rag-upload`, `rag-query`, `graphrag-build`, `mcp-call`, `web-search` (60/min/project).
4. **Observability.** `rag_query_duration_ms`, `graphrag_build_state_total{state}`, `mcp_sandbox_denials_total`, `web_search_cache_hit_ratio`.
5. **RFC 7807.** `https://smap.local/problems/{a2a-forbidden, capability-mismatch, graphrag-build-busy, mcp-egress-denied, mcp-timeout, search-key-not-configured, search-quota-exceeded, agent-cap-exceeded}`.
6. **Migration policy.** `0005_agents`, `0006_rag`, `0007_graphrag`, `0008_mcp`, each N-1 compatible.
7. **Secrets.** MCP auth in `agent_mcp_servers.config` uses envelope encryption; search keys in `search_keys` (added in D.4).

## Risks

- **GraphRAG compensation edge cases.** Chaos-injection nightly test; build_id isolation prevents partial pollution.
- **gVisor kernel compatibility.** Bootstrap probe at startup; Kata fallback path documented (§26 item 3).
- **Tavily rate variability.** Per-provider 60/min/project + Redis cache + 4KB cap absorb bursts.
- **Prompt section drift under lazy strategy.** Turn-level cache only (R9.07) so edits take effect next turn.
