---
type: feature
status: approved
created: 2026-07-07
requirements: [R09.17, R10.06, R10.10, R10.11, R11.01, R11.02, R11.03, R11.05, R11.06, R11.07, R11.08, R11.09, R11.10, R11.11, R11.12, R11.13, R11.14, R11.15, R11.16, R11.17, R11.18, R11.19, R11.20, R11.21, R11a.01]
---

# GraphRAG Two-Axis Redesign — Decouple the Knowledge Graph from a Single Agent

## 1. Summary

Today "GraphRAG" is a single construct bound 1:1 to one Agent and built from that
agent's conversation history, yet it is presented in the UI beside file-RAG as if it were
designer-authored knowledge. This blueprint separates the concept into **two independent
subsystems along two axes**, and decouples the conversation graph from single-agent
ownership so it can serve a chatroom, an agent group, or a whole workspace:

```
Axis 1 — RAG (Designer -> AI Agent)        build-time, authored knowledge, read-only at runtime
  - File            (exists: file-RAG)
  - Knowledge Map   (NEW: GraphRAG over uploaded files)

Axis 2 — Context (record: User <-> AI Agent) runtime, conversation memory, grows over time
  - General         (exists: flat transcript + compact-summary)
  - Concept Map     (today's GraphRAG, re-homed here and given a discriminated typed-FK owner)
```

This is a blueprint dossier: it fixes the target architecture and the data model, then
proposes a phasing that will be split into separate `/build` tasks. The audience is the
engineers who will implement each phase and the reviewers who approve them.

**Verification provenance:** the current-state claims and the feasibility of every load-
bearing change were re-verified against the code on 2026-07-07 by four adversarial
verification passes (decouple surface, Knowledge Map reuse, layered retrieval, ownership +
migration). Their material corrections are folded into §4-§13 and recorded in §4a.

## 2. Goals and Non-goals

**Goals**

- Establish the two-axis model as the canonical framing for retrieval/memory in SMAP:
  Axis 1 = designer-authored knowledge (File + Knowledge Map); Axis 2 = conversation
  memory (General + Concept Map).
- Decouple the conversation graph (today's GraphRAG) from the `graphrag_configs.agent_id`
  UNIQUE 1:1 binding, replacing it with **typed nullable owner FK columns**
  (`owner_chatroom_id` / `owner_agent_group_id` / `owner_workspace_id`) + a CHECK (exactly
  one non-null) + per-kind partial unique indexes — the SMAP-idiomatic discriminated-owner
  pattern (Q-10, §4b-2 debt round; exemplar `0042_prompt_studio.py`).
- Introduce `agent_group` as a first-class entity that can own a Concept Map.
- Support **layered retrieval**: an agent's turn draws on every Concept Map whose scope
  covers it (its room + its groups + its workspace), merged under one retrieval budget
  with narrow-scope precedence.
- Introduce the **Knowledge Map** (Axis 1) as GraphRAG built from uploaded files, a distinct
  product subsystem (own config/domain/UI) that reuses the shared graph **engine** through
  Protocol seams (Q-11).
- **Share the graph engine, fork the product+domain** (Q-11): the engine plumbing (Neo4j
  driver, Qdrant store, 2PC runner, lock/snapshot, embed resolution, build-state, bundle/
  cap/merge, WS transport, frontend socket/visualizer) is shared via Protocols/factories;
  each axis owns only its four differing seams (source loader, extractor prompt, scope/authz
  validation, collection prefix + trigger kind) plus its domain and retrieval strategy.
- Make the Concept Map a **temporal knowledge graph** (Q-12): edges/entities carry
  timestamps, the exact User<->Agent conversation timeline is preserved so an agent
  understands ordering/causality, and the (per-axis) retrieval strategy reserves a
  recency-weighting hook. Heavier temporal features (time-travel / bitemporal snapshots) are
  architecturally reserved here and specced separately.
- **Leave no orphaned graph data** (Q-13, §4b-2 debt round): every config/owner deletion
  purges its Neo4j subgraph + Qdrant points; a DB cascade is never the sole teardown. Fix the
  pre-existing agent-delete leak.
- Enforce **default-strict privacy**: only the chatroom layer is on by default; the
  agent_group and workspace layers must be explicitly enabled on their owner entity.

**Non-goals**

- Not a rewrite of the file-RAG chunk/vector pipeline (§10) — Knowledge Map reuses the
  shared document parser as a source, it does not replace RAG.
- Not changing the 2PC build/compensation mechanics (§11.2a) — the state machine is
  reused unchanged; only the owner scoping, the delta-feed query, and the evidence typing
  change.
- Not merging Neo4j/Qdrant into a single store, and not introducing a new graph database.
- Not building cross-project memory. Every owner remains inside a single project's tenant
  boundary; no layer ever spans projects.
- Not overloading `graphrag_configs` for Knowledge Maps — Knowledge Map is its own config
  table (Concept Map keeps `graphrag_configs`, minus `agent_id`).
- Not a polymorphic `(owner_kind, owner_id)` column — rejected as a relational anti-pattern
  with no FK/CASCADE and app-only integrity (Q-10).
- Not fully specifying temporal time-travel here — only the cheap, architecture-fixing
  temporal parts are in scope; bitemporal/versioned-snapshot time-travel is a separate spec.
- Not delivering all four quadrants in one shipment — phasing (§ Phasing) splits this into
  independently buildable tasks.
- Not changing sub-agent inheritance semantics (today sub-agents inherit neither
  `rag_config_id` nor `graphrag_config_id`,
  `orchestration/application/subagent_service.py:257-280`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Pure decouple/rename (refactor) or also new Knowledge Map (feature)? | Full four-quadrant blueprint first, then phase it. | The two axes are one coherent architecture. |
| Q-2 | New scope unit for the conversation graph after decoupling? | Agent group / workflow scope. | Superseded/generalized by Q-4/Q-7 into the polymorphic-owner model. |
| Q-3 | One engine with two kinds, or two subsystems? | Two separate subsystems. | Knowledge Map and Concept Map differ in lifecycle/source/UI. |
| Q-4 | Which real entity owns the Concept Map (no `agent_group` in code)? | Create `agent_group` first-class. | User wants explicit user-composed memory units. |
| Q-5 | How independent must the two subsystems be at infra? | Domain/config/UI separate; share low-level adapters. | Avoids duplicating Neo4j/Qdrant/2PC. |
| Q-6 | Which messages feed a group/workspace Concept Map? | Decouple owner so chatroom AND workspace can each own one. | Led to the polymorphic multi-scope model. |
| Q-7 | Which Concept Map layers does this cover? | All three: chatroom, agent_group, workspace. | Full layered memory hierarchy. |
| Q-8 | Cross-scope privacy for wide layers? | Default strict; wide layers require explicit enablement. | Honors the multi-tenant AuthZ hard rule. |
| Q-9 | First buildable phase? | Arrange after the blueprint is finalized. | Phasing recommended here (revised post-verification, § Phasing). |
| Q-10 | Owner data model: polymorphic column vs typed FK + CHECK? | **Typed FK columns + CHECK + partial unique indexes.** | Debt round: polymorphic is a non-idiomatic anti-pattern (no FK/CASCADE, orphan debt); typed-FK is SMAP's convention (`0042_prompt_studio.py`, `projects` XOR). |
| Q-11 | Knowledge/Concept code-sharing boundary? | **Share the engine plumbing via Protocols/factories; product+domain independent per axis.** | Debt round: literal fork = ~2,500 lines twin + 7 drift pairs; sharing the plumbing keeps product separation without the debt and lets Concept Map's domain evolve freely. |
| Q-12 | Temporal Concept Map — what and in scope? | Timestamps on edges/entities + preserve exact conversation timeline (in scope); recency-weighting hook (reserved on the retrieval strategy); time-travel (separate spec). | User wants the agent to understand ordering/causality; temporal lives on the independent Concept Map domain/strategy side, validating Q-11's cut. |
| Q-13 | Drop `agents.graphrag_config_id` in Phase 1 or leave dormant? | **Drop in Phase 1** (no shim needed); collapse bind==membership. | Debt round: dormant is misleading dead code (null DTO contract, false `isBound` badge) — exactly the debt to avoid. |

## 4. Current State

### 4.1 One context, two subsystems already

Both RAG and GraphRAG live in `contexts/knowledge/`, physically separate from `agents`
and `conversation`. The `agents` context only validates attachment; retrieval is consumed
read-only at runtime.

- File-RAG: `contexts/knowledge/domain/models.py:113-148`; ingestion
  `application/ingest_service.py:115-423`; Qdrant `rag_{project_id}`; MinIO `rag-sources`.
- GraphRAG (conversation graph): `contexts/knowledge/domain/graphrag.py:30-153`; builder
  `application/graphrag_builder.py:124-457`; Neo4j `infrastructure/neo4j_driver.py`;
  Qdrant `graphrag_{project_id}` (`infrastructure/graphrag_vector_store.py:31-33`).

### 4.2 The mislabel: today's GraphRAG is Axis-2 memory wearing Axis-1 clothing

GraphRAG's data source is **conversation history**, not files. The delta loader reads
messages from every chatroom the agent is a member of —
`app/workers/tasks/graphrag.py:88-91`:
`messages m JOIN chatrooms cr JOIN chatroom_agents ca WHERE ca.agent_id = :agent_id`.
The domain docstring confirms it: "a persistent graph built from conversation history"
(`domain/graphrag.py:1-8`). Yet the frontend renders it in the agent editor's **Knowledge**
tab beside file-RAG (`AgentDetailView.vue:935-1000`). This blueprint corrects that framing.

### 4.3 The agent coupling — two distinct linkages, four schema anchors

There are **two independent agent linkages** serving different sides; both must be
understood before decoupling:

- **Build/delta-feed side** — `graphrag_configs.agent_id`, `UNIQUE NOT NULL`, FK
  `ondelete=CASCADE` (`graphrag_tables.py:21-27`; migration `0013_graphrag.py:52-54`).
  Drives which rooms are ingested (`app/workers/tasks/graphrag.py:88-91`, agent id from
  `:183`).
- **Consume/retrieval side** — `agents.graphrag_config_id`, nullable, deferred FK
  `ondelete=SET NULL` (`agents/infrastructure/tables.py:50`; FK installed at
  `0013_graphrag.py:80-87` — a fourth schema anchor). At query time the agent resolves its
  single config through this pointer (`turn_engine.py:1669`).

Under a polymorphic owner these two legitimately decouple: build scope becomes the owner;
retrieval becomes owner-coverage resolution (§5.4). `graphrag_configs.project_id` is stored
directly (`graphrag_tables.py:18-20`), so chatroom/workspace owners resolve the Qdrant
collection `graphrag_{project_id}` with no owner-walk.

File-RAG is already the looser template: project-scoped `RagConfig`, many-agent allowlist
`RagDocument.agent_ids` (`domain/models.py:147`).

### 4.4 Retrieval assembly — one point, read-only, fail-soft (turn-level only)

`TurnEngine._run_locked` (`contexts/agents/application/runtime/turn_engine.py:794-1138`,
worker-only) folds all context into `system_parts`. Both retrievers use one
`knowledge_queries` built at `turn_engine.py:902`:

- File RAG block: `_rag_context` (`:903-905, 1658-1663`).
- GraphRAG block: `_graphrag_context` (`:906-908, 1666-1671`) forwarding
  `agent.graphrag_config_id` + `query_texts` to
  `GraphRagContextProvider.query(*, graphrag_config_id, query_text, query_texts)`
  (`knowledge/application/graphrag_context_provider.py:60-66`) — **single config today**.

Fail-soft is **turn-level only**: the entire `query()` body is one try/except
(`graphrag_context_provider.py:75-91`) returning `None` — so one config error discards the
whole result (relevant to per-layer isolation, §5.4). Evidence excerpts are fetched from
conversation via an injected `EvidenceFetcher` (`:203-224`) keyed on message UUIDs (§4a-G3).

### 4.5 Ownership hierarchy and the missing "group"

`orgs -> projects -> workspaces -> chatrooms -> messages`. Agents join chatrooms via
`chatroom_agents(chatroom_id, agent_id, role)` — composite PK, both legs FK
`ondelete=CASCADE`, plus a `role` enum (`conversation/infrastructure/tables.py:50-70`).

- **No `agent_group` entity exists** (grep-confirmed, definitive). The only grouping
  constructs are `workflows` and `key_groups` (the latter in the `keys` context).
- `chatrooms`: PK `id`, FK `workspace_id -> workspaces.id CASCADE`
  (`conversation/infrastructure/tables.py:25-31`). `workspaces`: PK `id`, FK
  `project_id -> projects.id CASCADE` (`:13-16`).
- `Workflow` is workspace-scoped and references agents/chatrooms only as UUID strings in
  its `definition` JSONB (`linter.py:111-120,361`) — no relational membership.
- `workflow_runs` (`orchestration/infrastructure/tables.py:17-39`) is ephemeral (~90d) — a
  poor config owner.

### 4.6 Frontend & existing tests

- All RAG/GraphRAG UI lives in the `agents` slice (SRS §24.2,
  `docs/implement/E-agents-knowledge.md:288`): routes `slices/agents/routes.ts:19-37`;
  Knowledge tab `AgentDetailView.vue:935-1000`; `RagConfigDetailView.vue`;
  `GraphragConfigListView.vue`; visualizer `GraphragGraphView.vue` (Vue-Flow, built).
- "Conversation memory" today = only `context_mode: compact` (§9.3, R9.09-R9.11),
  configured on the agent editor **General** tab (`AgentDetailView.vue:820-855`).
- Audit to read first: `docs/audits/graphrag-neo4j-audit.md` (2026-06-28) — flags
  2PC-persistence, lock-steal, reconciler non-determinism.

### 4a. Verification pass (2026-07-07) — material corrections

Recorded so the plan reflects code, not the initial exploration:

- **G1 — Decouple surface is ~15 source files + ~6 test files, not "3 points".** The three
  schema anchors are accurate but only remove constraints; the runtime read/write sites are
  broader (full list in §6). Phase 1 rescoped accordingly.
- **G2 — Builder-vs-consumer key-group rule is owner-conditional and its source of truth is
  the config service.** The R11.01 rule (builder group != consumer agent's group) lives in
  `graphrag_config_service.py:59-79` (create) and `:173-199` (update), not
  `agent_service.py:227-259` (that is the agents-side mirror). Chatroom/workspace owners
  have **no `key_group_id`**, and a Concept Map can be consumed by many agents, so the
  distinctness rule has no single operand and must be **dropped for Concept Maps** — only
  the project-scope check on `builder_key_group_id` survives. This is new behavior (a
  validation that is skipped), not a relaxed constraint.
- **G3 — Evidence identity is UUID-hard and would silently drop file evidence.**
  `evidence_msg_ids: tuple[uuid.UUID,...]` on `Triple`/`RelationEdge` (`domain/graphrag.py`),
  the `uuid.UUID(...)` coercion in `triple_extractor.py:158-165` and `graphrag_retrieve.py:114-122`
  (both `except ValueError: continue` → silent drop), and `EvidenceFetcher =
  Callable[[list[uuid.UUID]], ...]` all hard-require UUIDs. Neo4j stores it as an **opaque
  string list** (`neo4j_driver.py:119`), so **no datastore migration** — but the Python
  domain/parse layers need generalizing to `tuple[str,...]` (Phase 3).
- **G4 — The reuse seam is the shared parser, not `IngestService`.**
  `IngestService._index_document` chunks-and-discards the parsed text
  (`ingest_service.py:278-423`); the reusable seam is
  `shared_kernel/text_extraction/parsers.py::MIME_TO_PARSER` (`str = MIME_TO_PARSER[mime](data)`).
  The `TripleExtractor` **Protocol** is reusable; the concrete `LlmTripleExtractor` prompt +
  `_render_messages` are conversation-shaped — Knowledge Map needs a second concrete
  extractor.
- **G5 — `_merge_bundles` already exists and is the correct home for cross-map merge**
  (`graphrag_context_provider.py:227-253`): dedups entities by name, relations by
  `(s,r,o)` keeping max confidence, evidence-capped at 10; merged pre-cap, one final 2KB
  cap (`_cap_to_2kb`, `domain/graphrag.py:135-153`, applied by provider at `:84`). It orders
  by confidence only — **scope precedence must be added as render order**.
- **G6 — Per-query client churn + no per-layer fail-soft.** The provider builds/tears down
  fresh Neo4j+Qdrant clients per query inside the query-text loop (`:137-156, :77-80`), no
  pool. RAG's provider reuses one client per `query()` (`rag_context_provider.py:115-149`) —
  the pattern to follow. N layers × M texts = N×M round-trips, and embeddings cannot be
  shared (each config has its own embed model, `:158-184`).
- **G7 — `workspace_id` is not in scope at the assembly point;** it needs an added
  `ConversationFacade(db).get_chatroom(chatroom_id)` fetch (`chatroom_id` and `agent` are in
  scope; `workspace_id` is not).
- **G8 — Multi-member feed needs `DISTINCT m.id`.** `WHERE ca.agent_id = ANY(:ids)` emits
  duplicate message rows for co-present members — a latent double-ingest defect.
- **G9 — Migration must mutate config rows in place (stable `id`)** or 100% of Neo4j/Qdrant
  data (keyed by `graphrag_config_id`) orphans. Reversibility holds only against
  freshly-migrated data (singleton groups); once a group has ≠1 members or a
  chatroom/workspace owner appears, there is no lossless inverse to `UNIQUE agent_id`.

### 4b. Verification pass 2 (2026-07-07) — second-angle findings

A second adversarial round attacked four angles the first missed: build concurrency/2PC/
scale, AuthZ/tenant/WebSocket, the consume-pointer ripple + multi-block turns, and frontend
slice boundaries. Findings, with severity and the phase they bind to:

**Build correctness & scale (Phase 2 prerequisites — these bite only once multi-member
groups or workspace owners exist; a singleton agent_group in Phase 1 has today's bounded
scope and does not regress):**

- **V-1 — CRITICAL — no windowed extraction exists.** `_DbDeltaLoader.load` paginates the DB
  (`_BATCH_SIZE=2000`) but accumulates the **entire** delta into one list
  (`app/workers/tasks/graphrag.py:60,101-107`); the builder passes all of it to ONE
  `extractor.extract` call (`graphrag_builder.py:227-236`) rendered into one prompt
  (`triple_extractor.py:84-122`), and embeds all entities in one batch
  (`graphrag_builder.py:435-440`). No chunk/window logic exists anywhere. A workspace initial
  build (`since=None`) over ~10k messages is ~800k tokens in one prompt — beyond every model.
  Failure is **silent**: non-200 -> extractor returns `[]` -> build "succeeds" with zero
  triples; or an oversized embed batch -> Phase-2 fails -> `FAILED_COMPENSATING` retrying the
  same oversized batch forever. Windowed/batched extraction is a hard prerequisite for
  workspace and multi-member-group ownership.
- **V-2 — HIGH — one project-shared Qdrant collection, one fixed vector dimension.**
  `graphrag_{project_id}` is created with the first build's `vector_size`
  (`graphrag_vector_store.py:56-62`). Per-owner configs have their own `builder_key_group_id`
  -> possibly different embed models -> different dims (1536 vs 1024); the second config's
  upsert is rejected by Qdrant -> permanent Phase-2 failure. This is a **pre-existing latent
  bug** (two 1:1 configs in one project with different embed models already hit it) that
  fan-out makes routine. Fix: pin one embedding model/dimension per project graph collection,
  or shard the collection per config.
- **V-3 — HIGH — `job_timeout (600s) == LOCK_TTL (600s)`** (`app/workers/main.py:271`,
  `graphrag_builder.py:59`). A long fan-out build is killed by arq exactly as its lock
  expires -> durable `RUNNING`/`NEO4J_COMMITTED` + a re-trigger can start a second build.
  Raise `job_timeout` above the largest realistic (windowed) build, or make builds resumable.
- **V-4 — MEDIUM — the covering-config trigger resolver must return DISTINCT configs.**
  `evaluate_graphrag_message_triggers` increments a counter per returned row
  (`graphrag_triggers.py:52,56-60`); if the `(agent, chatroom)->covering configs` query
  reaches a group/workspace config through multiple member agents, its counter multiplies.
  G8's DISTINCT applies to the trigger set too, not only the feed.
- **V-5 — MEDIUM — enqueue has no dedup.** `enqueue` passes no `_job_id`
  (`shared_kernel/queue.py:31`); duplicate triggers enqueue duplicate builds -> the second
  hits the lock -> `GraphRagBuildBusy` -> the worker sets the Prometheus one-hot to
  `failed=1` and arq retries (`graphrag.py:234-237`). Fan-out makes false "failed" signals +
  retry storms routine. The "per-message build dedup" named in §7/§10 must be designed
  (coalesce via `_job_id` keyed on config + message watermark).
- **V-6 — LOW — DISTINCT implementation trap + `RUNNING`-state counter pause.** DISTINCT must
  be full-row with `m.id,m.created_at` in the select (not `DISTINCT ON`, which fights the
  keyset ORDER BY), and dedup happens before `LIMIT` so batch cost scales with member×room
  fan-out. The trigger counter is skipped while state is `RUNNING`
  (`graphrag_triggers.py:58`), so messages during a long build are lost for trigger
  accounting. Confirmed-safe: per-config locks, Neo4j config-scoping, `apply_triples`
  idempotency (MERGE + evidence union), the atomic Redis counter, the keyset design.

**AuthZ / tenant isolation (Phase 1-2; building blocks all exist, but none apply to the
graphrag paths today — they must be wired in, not assumed):**

- **V-7 — HIGH — polymorphic owner->project invariant does not exist.** Today only
  `graphrag_config_service.py:74` guards `agent_row.project_id != project_id`. Nothing will
  stop a config with `project_id=P1` from owning a P2 chatroom/workspace/group, violating
  R11.10. Needs an app-layer `_assert_owner_in_project` (owner resolves project differently
  per kind) enforced at **create and update** (mirror `:74` and `:195`). Reuse the
  `_assert_*_in_project` family (`agent_service.py:206-258`).
- **V-8 — HIGH — map channels/reads use bare project membership, bypassing room ACL.**
  `ws/graphrag.py:46-52` and `GET /graphrag/{id}/graph` (`app/api/v1/graphrag.py:258-329`)
  authorize on `roles_for(project_id) != {}`. For a **chatroom-owned** map, any project
  member — including one barred from an owners-only/project-only room by the §21.1 flags —
  could read graph content distilled from that room. Authorization must branch on
  `owner_kind`: chatroom -> `resolve_room_access` + `ensure_can_read`
  (`conversation/application/access.py:52,125`, as `ws/chatroom.py:59-64`); workspace/
  agent_group -> project membership + the owner opt-in. The `/graph` content endpoint is the
  higher-severity vector; the build-status channel leaks only state.
- **V-9 — MEDIUM — retrieval coverage resolution is authorization-bearing.**
  `graphrag_context_provider` does zero re-check today (safe because the pointer was
  validated at attach). The new coverage resolver must derive coverage strictly from the
  agent's actual room binding + its group memberships + the current room's workspace (never a
  broader union), filter each wide layer by `concept_map_enabled` **at query time** (a
  later-disabled layer must stop being read), and confirm project match. The enabled-flag
  check is load-bearing, not cosmetic (R11.10).
- **V-10 — MEDIUM — two conflicting "Project Owner" checks.** `RESOURCE_CREATE_EDIT`
  (`permissions.py:211-214`) admits org-owner-inherited project owners; `is_project_owner`
  (`tenancy/interfaces/facade.py:48`) is strict. R10.10's upload gate uses the strict one.
  `concept_map_enabled` (R11.10 "only by Project Owner") must reuse the **strict**
  `is_project_owner` consistently across enable + membership endpoints. Audit reuse
  (`shared_kernel/audit.py:42`) is a clean drop-in.

**Consume-pointer ripple & multi-block turns (Phase 2/4):**

- **V-11 — confirmed no orchestration breakage.** `agent.graphrag_config_id` is read only at
  `turn_engine.py:1669` (retrieval), the agents create/patch validation, ORM/DTO, and two
  frontend surfaces. Orchestration/A2A/subagent/workflow read it nowhere except the two
  inheritance maps that already exclude it (`subagent_service.py:271`,
  `domain/models.py:364`). History/compaction is also safe — RAG/graphrag blocks are
  ephemeral, never written as message rows (`transcript.py` keys only on `compact_summary`).
- **V-12 — MEDIUM — DTO/openapi ripple + two live frontend surfaces.** The pointer is a field
  on `AgentOut`/`AgentCreateIn`/`AgentPatchIn` (`app/api/v1/agents.py`, generated into
  `shared/api-client/models/*`), and drives the `isBound` badge
  (`GraphragConfigListView.vue:130-131`, semantically wrong after decouple) and the
  AgentDetailView graphrag form field. Decoupling ripples through `gen:api` +
  `check:openapi-drift` and these two surfaces.
- **V-13 — MEDIUM — no cross-block token budget/order policy.** `_run_locked` joins all
  context blocks into one system string (`turn_engine.py:983`); today 1 RAG + 1 graphrag
  block. Under the plan a turn may carry 1 File-RAG + 1 Knowledge-Map + up to 3 Concept-Map
  layers, each self-capping (graphrag at 2 KB) with **no combined budget** — five blocks can
  blow the system-prompt budget, and `should_compact` measures history rows, not the system
  prefix. A combined budget with narrow-scope precedence is a net-new policy.
- **V-14 — MEDIUM — graph citation persistence does not exist.** `grep graphrag_sources` = 0;
  `_graphrag_context` returns a bare `str`, discarding the bundle. RAG persists a single
  `metadata.rag_sources` list (`turn_engine.py:1065-1067`). "N graph blocks with citations"
  is net-new and must be multi-source-shaped from day one — OR graph blocks stay
  citation-less as today (Open Q-E). Not required by any goal; scope decision.
- **V-15 — RAG/graphrag pointer asymmetry.** `rag_config_id` also drives the hosted File
  Search tool + a tool-singleton gate + doc-scope SQL (`agent_service.py:334,628-635`,
  `builtin_tools.py`); graphrag mirrors only the retrieval line. The symmetric create/patch
  clear-flag machinery will deliberately diverge — handle explicitly, update the paired tests.

**Frontend re-home (Phase 4):**

- **V-16 — workspace UI lives in the `conversation` slice, not `tenancy`.** `tenancy` owns
  only orgs/projects/members (`tenancy/routes.ts`); workspaces are `conversation.workspaces`
  (`conversation/routes.ts:4-9`). This is fortunate: `conversation` may import `agents`
  (`eslint.config.js:21`) whereas `tenancy` may not (`:20`), so the workspace Concept Map
  panel is boundary-legal.
- **V-17 — the "reusable visualizer" is a route-bound page, not a component.**
  `GraphragGraphView.vue` reads route params, renders breadcrumbs, calls `agentsApi`, and
  hardcodes `agents.*` i18n; it is not re-exported from `agents/index.ts`. Reuse requires
  extracting a props-driven `KnowledgeGraphCanvas` into `shared/ui/` (labels as props; there
  is no `shared/locales/`) and moving the pure `useGraphLayout` to `shared/`, demoting the
  current view to a thin wrapper. A real shared-layer refactor, not a re-export.
- **V-18 — owner-scoped data access is net-new on the frontend too.** `agentsApi.getGraphragGraph`
  and `useGraphragSocket` are `graphrag_config`-scoped (agent-1:1); an owner-centric panel
  needs the new owner-scoped endpoints + new API methods. Good news: knowledge state is
  TanStack-Query-keyed (no Pinia store to split).

## 5. Design

### 5.1 The four quadrants, mapped to code

| Quadrant | Axis | Source | Owner / scope | Status today |
|---|---|---|---|---|
| File | 1 | uploaded docs | project + agent allowlist | Exists (§10) |
| Knowledge Map | 1 | uploaded docs | project + agent allowlist (own config table) | **NEW** |
| General | 2 | chat history | per agent (transcript) | Exists (§9.3) |
| Concept Map | 2 | chat history (temporal) | **typed FK owner: chatroom / agent_group / workspace** | Exists as 1:1-agent; re-homed + decoupled |

### 5.2 Concept Map — discriminated owner via typed FK columns (Q-10)

Replace `graphrag_configs.agent_id UNIQUE` **in place** (§4a-G9, preserving
`graphrag_configs.id` so Neo4j/Qdrant data stays scoped) with the SMAP-idiomatic
discriminated-owner shape (exemplar `0042_prompt_studio.py:27-88`; `projects` owner-XOR
`0002_tenancy.py:63-88`):

```
owner_kind        ENUM('chatroom','agent_group','workspace')   -- discriminator, create_type=False
owner_chatroom_id     UUID NULL  FK -> chatrooms.id
owner_agent_group_id  UUID NULL  FK -> agent_groups.id
owner_workspace_id    UUID NULL  FK -> workspaces.id
CHECK: exactly one owner_* non-null, matching owner_kind   -- style of _SCOPE_CHECK
-- three partial unique indexes, one per kind (WHERE owner_kind = '...') -- replaces UNIQUE(agent_id)
project_id  UUID NOT NULL FK -> projects CASCADE            -- kept: belt-and-suspenders sweep
```

Why typed FKs, not a polymorphic `(owner_kind, owner_id)` column (Q-10, debt round): real
FKs give DB-enforced referential integrity, per-kind `ON DELETE` behavior, and direct joins;
a bare polymorphic UUID has none of these and orphans Neo4j/Qdrant data on every owner
deletion (§5.9). Typed FK is SMAP's established convention; the polymorphic column would be
its first live polymorphic FK. `owner_kind` is still stored as a fast discriminator so
per-kind branching (delta feed, coverage) reads it without probing three columns.

Each config is its own row -> isolated Neo4j subgraph + Qdrant points (scoped by
`graphrag_config_id`, `neo4j_driver.py:49-228`, `graphrag_vector_store.py:68-115`).
`GraphRagConfig` drops `agent_id`, gains `owner: OwnerRef` (kind + the one id).

**Delta-feed strategy keyed on `owner_kind`** — variants of `app/workers/tasks/graphrag.py:88-91`,
all full-row `SELECT DISTINCT` with `m.id,m.created_at` in the projection (§4a-G8, §4b-V-6):

- `chatroom`: `WHERE m.chatroom_id = :owner_chatroom_id`
- `agent_group`: `JOIN chatroom_agents ca ... WHERE ca.agent_id = ANY(:member_ids)` (DISTINCT)
- `workspace`: `JOIN chatrooms cr ... WHERE cr.workspace_id = :owner_workspace_id`

**Key-group validation becomes owner-conditional** (§4a-G2): the create path skips the
agent-load + builder-vs-consumer distinctness check for non-agent owners (else it
dereferences a null agent, §4b-V-5-adjacent); keep only the in-project check on
`builder_key_group_id`. Authority moves out of the agents-side mirror into the config
service.

### 5.3 `agent_group` — new first-class entity

- `agent_groups(id PK, project_id FK -> projects CASCADE, name, timestamps)`, UNIQUE
  `(project_id, name)` — mirrors `rag_configs` project-scoping.
- `agent_group_members(group_id FK, agent_id FK, PRIMARY KEY(group_id, agent_id))`, both
  legs `ondelete=CASCADE` — mirrors `chatroom_agents:50-70` (no `role` column; groups are
  not rooms).
- `concept_map_enabled BOOLEAN` on the group (and on `workspaces`) gates the wide-layer
  opt-in (§5.6).
- **Home: a sub-module of the `agents` context** (project-scoped agent-composition concept),
  not a new top-level context.

### 5.4 Layered retrieval

At turn time resolve **the Concept Maps covering this agent in this room**:

1. the room's map (`owner_kind=chatroom`, `owner_id=chatroom_id`), if it exists;
2. every enabled `agent_group` map whose membership includes this agent;
3. the workspace map (`owner_kind=workspace`, `owner_id=room.workspace_id`), if enabled.

Concrete changes (§4a-G5/G6/G7):

- `GraphRagContextProvider.query`: `graphrag_config_id` -> a prioritized **list**; build one
  Neo4j + one Qdrant client per `query()` and reuse across all configs/texts (RAG pattern);
  move the try/except **inside** the per-config loop for **per-layer fail-soft**.
- Reuse `_merge_bundles` (`:227-253`) to merge all layers' bundles pre-cap, then one
  `_cap_to_2kb`. Add **scope-precedence render order** (chatroom > agent_group > workspace)
  so the 2KB tail-trim drops the widest scope first; dedup entities by name.
- Add `ConversationFacade.get_chatroom(chatroom_id)` at the assembly point to obtain
  `workspace_id` (§4a-G7).
- New facade method to resolve covering configs for `(agent_id, chatroom_id)`.

The consume-side reverse pointer `agents.graphrag_config_id` is superseded by this coverage
resolution and is **dropped in Phase 1** (Q-13), not left dormant. Phase 1 builds the
membership-based covering-config resolver for triggers anyway; retrieval reuses it (with
`agent.id` in scope), so no shim is needed. The "built-but-unbound" edge collapses to
bind==membership (the target design; also removes wasted builder-key spend).

### 5.5 Knowledge Map — GraphRAG over files (Axis 1)

A distinct Axis-1 **product** subsystem (its **own** config table, domain, and UI) that
reuses the shared graph **engine** through Protocol seams (§5.7), not a forked stack:

- `knowledge_map_configs`: project-scoped, agent allowlist (mirror `RagConfig` /
  `RagDocument.agent_ids`). No `agent_id`-as-source semantics.
- Source: uploaded documents parsed via the **shared** `MIME_TO_PARSER`
  (`shared_kernel/text_extraction/parsers.py`, the true seam — not `IngestService`, §4a-G4);
  a **second concrete `TripleExtractor`** with a document prompt feeds the shared 2PC builder
  via its own source loader (the extractor Protocol + parse half are shared, only prompt +
  renderer differ, §4b-C-C).
- Evidence: uses the **neutral opaque `evidence_refs`** (§5.7), encoding file evidence as
  `doc:{doc_id}:{chunk_idx}` with a `build_doc_evidence_fetcher` -> `rag_chunks` text. No
  Neo4j/Qdrant schema change (the stores already hold opaque strings).
- Storage: its own Qdrant collection `knowmap_{project_id}` via the **parameterized**
  collection prefix (§5.7); its own Neo4j subgraph via the shared driver scoped by config id.
- Retrieval: a Knowledge Map block in `turn_engine` beside File RAG; read-only; fail-soft.
- Billing: designer/project key (authored knowledge; not the Axis-2 builder-key split).
- Non-temporal: Knowledge Map does not carry the Concept Map's temporal semantics (§5.8).

### 5.6 Privacy model (default strict)

- `chatroom` layer: enabled by default (a room's memory of its own conversation).
- `agent_group` and `workspace` layers: **disabled by default**; require `concept_map_enabled`
  on the owner, settable only by Project Owner, enablement audit-logged.
- Retrieval never folds a wide layer an agent's room is not authorized for. See §8.

### 5.7 Engine-sharing architecture — share the plumbing, fork the product+domain (Q-11)

The debt round (§4b-C) showed the two subsystems differ in only **four seams** and share
the whole engine; a literal fork = ~2,500 lines of twin + 7 drift-prone pairs (embed-model
map — already drifted, build-state machine, WS payload, error taxonomy, Qdrant prefix,
metric labels, cascade cleanup). So the boundary runs along **"will it diverge by product?"**,
not "which subsystem":

**Shared engine (Protocol/factory seams; neither axis redefines it):**
- `Neo4jAsyncDriver`, `GraphVectorStore` (collection prefix **parameterized** in the
  constructor, not a module free function), 2PC runner, lock/snapshot ports.
- One `embed_resolution` helper owning the provider->model map + `_resolve_embed_key` —
  **retires the existing 2-way FU-1 drift** and, per Phase-0 verification, **fixes a SEC-H3
  bug**: the worker copy uses `list_ordered` (no active-carry check) so the builder can embed
  with a revoked-carry key; the helper standardizes on the secure `list_ordered_carried`.
- `BuildState`, `_cap_to_2kb`, `_merge_bundles`, `_render_bundle_text`,
  `build_entity_descriptions`.
- De-concreted `GraphRagBuilder` / `RetrieveService` / `ReconciliationLoop` take a **repo
  Protocol** + a `ConfigLike` Protocol (`id/project_id/key_group_id`) instead of the concrete
  `GraphRagConfigRepository`/`GraphRagConfig`.
- WS transport factory `(channel_fn, config_lookup)`; frontend `useGraphBuildSocket(prefix,
  keys)`, one shared `BuildState` module, one parameterized `GraphVisualizer` /
  `GraphConfigList` shell (also serves §4b-V-17).
- Shared error-registration helper + base error set parameterized by slug prefix.

**Neutral shared graph-domain** with evidence as opaque `evidence_refs: tuple[str,...]`.
Phase 0 does the **Python-layer** neutralization only; the **physical** Neo4j property-key
rename `evidence_msg_ids -> evidence_refs` is **deferred to the document-evidence phase**
(Phase-0 dossier Q-2: the driver maps the field to the existing key, so there is zero
data migration and byte-identical behavior until non-UUID evidence actually exists). This
severs the doc<->conversation two-masters coupling that generalizing the shared `Triple` in
place would create (§4a-G3 refined).

**Per-axis (independent — where Concept Map evolves freely, incl. temporal §5.8):** config
table + repo + service, source loader, extractor prompt + renderer, scope/authz validation,
trigger kind, retrieval strategy, and the domain interpretation of `evidence_refs`.

### 5.8 Temporal Concept Map (Q-12)

The Concept Map is a temporal knowledge graph; temporal logic lives entirely on the
independent Concept Map side (§5.7), so it never touches the shared engine or Knowledge Map.

**In scope (cheap, architecture-fixing — lock in now so decoupling doesn't preclude it):**
- Concept Map edges/entities carry `first_seen` / `last_seen` timestamps, derived from the
  source messages' `created_at` (already reachable — edges carry `evidence_refs` that resolve
  to messages). The shared Neo4j driver stores arbitrary edge properties, so this is a
  Concept-Map-domain addition, not an engine change — validating the §5.7 cut.
- The exact User<->Agent conversation timeline is preserved via `evidence_refs` (message ids
  are already time-ordered), so an agent can reason about ordering/causality.
- The Concept Map retrieval strategy reserves a **recency-weighting hook** (rank recent
  concepts/relations higher); the merge/precedence in `_merge_bundles` stays shared, the
  weighting is a Concept-Map strategy parameter.

**Reserved (separate spec, not built here):** time-travel / "what did we know as of date X"
needs bitemporal (valid-time + transaction-time) modeling and versioned snapshots; the 2PC
`build_id` + snapshot mechanism is a natural anchor, but the full feature is out of scope for
this blueprint. The design above must not preclude it — timestamps + per-build provenance
are the foundation it will build on.

### 5.9 Deletion & cleanup contract (Q-13, debt round §4b-2-B)

Graph data must never orphan. Today the explicit `DELETE /graphrag/{id}` is clean
(`cascade_external_stores` -> Neo4j `delete_all` + Qdrant `delete_by_config`,
`graphrag_config_service.py:277-346`), but **agent deletion leaks** (soft-delete ignores the
graph; the 60-day retention hard-delete fires the FK cascade, which cannot reach Neo4j/Qdrant,
`retention.py:141-159`). The plan multiplies this across new owner-delete paths.

Contract (exemplar: RAG's inline teardown `rag.py:335-395` — RAG never relies on a DB cascade
for external teardown):
- **One teardown primitive** per subsystem: `cascade_external_stores(config_id, project_id)`.
- **Every owner-delete path** (agent, chatroom, workspace, agent_group) enumerates owned
  configs (`list_for_owner_*`) and runs teardown in the deleting request — soft-delete +
  audit -> commit -> best-effort external purge -> `*.infra_purged` audit row — **before** any
  DB cascade removes the rows. A DB cascade is never the sole teardown of an external store.
- **Fix the pre-existing agent-delete leak** (Phase 0): teach the agent delete / retention
  path to purge external stores per config first (or drop the `agent_id` cascade in favour of
  explicit teardown). This is a live bug today, independent of the new model.
- **Reconciler backstop:** extend the existing GraphRAG reconciler to sweep Neo4j/Qdrant for
  `graphrag_config_id`s no longer present in Postgres, catching best-effort failures at scale.

### Options considered

**Option A — Single engine, two kinds.** One config table with a `kind` discriminator.
Least duplication but couples two divergent products. **Rejected per Q-3.**

**Option B — Two product subsystems sharing the engine via Protocols (chosen).** Separate
config/domain/UI per axis, but the engine plumbing shared through Protocol/factory seams
(§5.7); each axis owns only its four differing seams + domain/strategy. Clean product
boundaries AND DRY; costs the up-front Protocol de-concreting + the neutral graph-domain
extraction. **Chosen (Q-3, Q-5-refined, Q-11).**

**Option C — Two fully independent stacks (literal fork).** ~2,500 lines twin + 7 drift
pairs, incl. the already-drifted embed-model map. **Rejected on debt grounds (Q-11).**

Owner model: **typed FK + CHECK** chosen over a **polymorphic `(owner_kind, owner_id)`
column** (Q-10) — the latter loses DB integrity/CASCADE and orphans graph data.
Concept Map owner scope: **workspace-only** rejected per Q-4; `workflow.id` rejected because
workflows don't own the chatrooms where agents chat.

### Decision

Adopt the two-axis model. Concept Map gets an in-place **typed-FK discriminated owner**
(§5.2, Q-10) with `agent_group` first-class (§5.3) and layered, narrow-precedence,
per-layer-fail-soft retrieval (§5.4). Knowledge Map is a separate Axis-1 **product**
subsystem that reuses the shared graph **engine via Protocols** (§5.5, §5.7, Q-11), with a
**neutral graph domain** carrying opaque `evidence_refs`. The Concept Map is **temporal**
(§5.8, Q-12). Deletion **purges external stores** on every owner path, and the pre-existing
agent-delete leak is fixed (§5.9, Q-13). Privacy is default-strict (§5.6). Given up: a
unified config (Option A) and a polymorphic owner column (marginally narrower) in exchange
for clean product boundaries, DB-enforced integrity, and no orphan/twin debt.

## 6. Detailed Changes

SoC: the graph **engine** (Neo4j/Qdrant adapters, 2PC runner, Protocol seams) stays in
`knowledge`, reused by both subsystems. `conversation` stays the message source via facade
(never imported by `knowledge`).

**Engine de-concreting (§5.7, foundational — precedes both subsystems):** inject a repo
Protocol + `ConfigLike` into `GraphRagBuilder`/`RetrieveService`/`ReconciliationLoop`;
parameterize the Qdrant collection prefix in the store constructor; extract one
`embed_resolution` helper (retires FU-1 drift); extract the neutral graph domain
(`GraphTriple`/`GraphEdge`/`GraphBundle`, opaque `evidence_refs`, Neo4j property rename
`evidence_msg_ids -> evidence_refs`); WS transport + frontend socket/visualizer factories.

Full decouple surface (§4a-G1) — Concept Map, Phases 1-2:

- **`knowledge` domain**: `GraphRagConfig.agent_id`/draft (`domain/graphrag.py:34,46`) ->
  `owner: OwnerRef`. Evidence -> neutral `evidence_refs: tuple[str,...]` on the shared graph
  domain (§5.7).
- **`knowledge` repo** (`graphrag_repositories.py`): `_row_to_config:29`; `create:43-68`;
  409 `GraphRagConfigAlreadyExists(agent_id):64-67` -> per-owner; `list_for_agents:100-119`
  -> `list_for_owner_{chatroom,agent_group,workspace}` (typed-FK joins, no owner_kind probe);
  immutability note `:167-171`. Delete stale 1:1 comments/docstrings (Q-13/§4b-2-D).
- **`knowledge` config service** (`graphrag_config_service.py`): `create:59-79` +
  `update:173-199` key-group validation -> owner-conditional; `:97-102` owner passthrough;
  audit `:113`.
- **`knowledge` facade** (`interfaces/facade.py`): `evaluate_graphrag_message_triggers(agent_ids):75-85`
  -> owner set; add covering-config resolver.
- **`knowledge` triggers** (`graphrag_triggers.py:42-52`): `list_for_agents` -> owner
  resolution (per-config counter `:60` already owner-agnostic).
- **worker** (`app/workers/tasks/graphrag.py:44-107,183`): `_DbDeltaLoader` -> per-owner
  strategy with `DISTINCT`.
- **provider** (`graphrag_context_provider.py`): single id -> list; client reuse; per-layer
  try/except; scope-precedence in `_merge_bundles`.
- **runtime** (`turn_engine.py`): `_graphrag_context:1666-1671` -> layered coverage;
  trigger fan-out `:1272-1281` -> all covering configs; add `get_chatroom` fetch.
- **`agents` context (reverse pointer — DROP in Phase 1, Q-13)**: remove
  `models.py:141,229,241`; `repositories.py:53,119,141`; the `agent_service.py:227-261,301-321,411-458`
  attach/validate/clear branches; DTO fields `api/v1/agents.py:75,106,127,150,219,314,324`
  (ripples `gen:api` + `check:openapi-drift`). Not left dormant.
- **GraphRAG API** (`app/api/v1/graphrag.py:71,86,132-137,185-189`): `agent_id` ->
  owner fields; add **owner->project invariant** `_assert_owner_in_project` at create+update
  (§4b-V-7); **owner-kind-branched authz** (chatroom -> room ACL, §4b-V-8).
- **New** `agent_groups` + `agent_group_members` tables/facade/CRUD; `concept_map_enabled`
  on group + workspace (strict `is_project_owner` gate, §4b-V-10).
- **Migration**: drop `graphrag_configs.agent_id` UNIQUE + deferred reverse FK
  (`0013_graphrag.py:52-54,80-87`); add `owner_kind` enum + three typed nullable owner FK
  columns + CHECK (exactly-one) + three partial unique indexes (§5.2, pattern
  `0042_prompt_studio.py:27-88`); **in place** (stable `id`); backfill each config to a
  singleton `agent_group` (member = former `agent_id`). New PG enum via `CREATE TYPE` in
  `upgrade` / `DROP TYPE` in `downgrade`, `pg.ENUM(..., create_type=False)` (exemplar
  `graphrag_build_state` `0013_graphrag.py:38-42,65,98`; never `sa.Text`).
- **Tests** (~6): `test_graphrag_triggers.py`, `test_agent_service.py:306-342,489-583`,
  `test_agent_config_project_guard.py`, `test_graphrag_builder.py`, `test_graphrag_retrieve.py`,
  `test_graphrag_reset.py` — all build `GraphRagConfig(agent_id=...)`; also update the stale
  bound/unbound tests `GraphragConfigListView.test.ts:102-119` (§4b-2-D).
- **Cleanup (§5.9)**: `cascade_external_stores` invoked on every owner-delete path;
  Phase-0 fix for the agent-delete leak (`agent_service.py:502-525` / `retention.py:141-159`);
  reconciler orphan-sweep.

Concept Map temporal (§5.8): `first_seen`/`last_seen` on Concept Map edges/entities in the
per-axis builder; recency-weighting hook on the Concept Map retrieval strategy; timeline via
`evidence_refs`. Time-travel reserved (separate spec).

Knowledge Map (Phase 3): `knowledge_map_configs` table + service + facade over the shared
engine (§5.7); second concrete extractor (prompt + renderer only); `build_doc_evidence_fetcher`
resolving `doc:{id}:{idx}` -> `rag_chunks`; `turn_engine` Axis-1 block. The evidence-typing
change is done once in the neutral graph domain (§5.7), not per subsystem.

- **API contract** — `gen:api` rerun required: yes. New **owner-scoped** Concept Map
  endpoints (create/read/graph/build-status by owner, not `graphrag_config_id`) — the current
  API is config-scoped/agent-1:1 (§4b-V-18). Decoupling the pointer ripples the agent DTOs
  through `gen:api` + `check:openapi-drift` (§4b-V-12).
- **Frontend** (Phase 4, §4b-V-16..V-18) — split Knowledge tab (Axis-1 stays on agent;
  Concept Map -> owner panels). Panel homes: chatroom memory + workspace settings live in the
  **`conversation`** slice (workspaces are `conversation.workspaces`, not `tenancy`);
  agent_group editor in `agents`. Extract a props-driven **`KnowledgeGraphCanvas` into
  `shared/ui/`** (move pure `useGraphLayout` too) and demote `GraphragGraphView.vue` to a
  wrapper — the current view is route-bound and not re-exported, so cross-slice reuse needs
  the shared component, not a re-export. New owner-scoped API methods + a socket that is
  owner- not config-keyed. Fix the `isBound` badge semantics (§4b-V-12). All strings via
  `$t()`; canvas labels passed as props (no `shared/locales/`).
- **Deploy/config** — add `knowmap_{project_id}` to `smap/bootstrap/qdrant_init.py`; no new
  stores. Confirm one embedding dimension per project graph collection (§4b-V-2).

## 7. NFR Checklist

- [ ] i18n — all new owner-panel/agent_group strings via `$t()`.
- [ ] Audit log — enabling a wide layer, agent_group membership changes, admin reset.
- [ ] Tenant isolation — every new endpoint verifies project membership; owner_id resolves
  in-project; no owner spans projects.
- [ ] Error handling UX — extend `useGraphragSocket` build-status states to Knowledge Map +
  per-owner Concept Maps; RFC 7807 via `knowledge/interfaces/error_mapping.py`.
- [ ] Performance / retrieval — layered retrieval is N×M embed+search+traverse round-trips
  with no shared embeddings (§4a-G6); single-client reuse is a prerequisite, not optional.
  Index the new delta-feed predicates. The 2KB cap is a blind binary-search truncation with
  no scope notion (`domain/graphrag.py:135-153`) — precedence must be render-order, not cap
  logic. Multi-block turns need a **combined** token budget across File-RAG + Knowledge-Map +
  N Concept-Map layers (§4b-V-13); no combined budget exists today.
- [ ] Performance / build (Phase 2 prerequisites, §4b-V-1..V-6) — **windowed/batched
  extraction** is mandatory before workspace/multi-member ownership (V-1: unbounded initial
  build, silent zero-triple failure). **Pin one embedding model/dimension per project graph
  collection** or shard per config (V-2: dimension-mismatch permanent failure). **Raise
  `job_timeout` above the largest windowed build** (V-3: killed at lock-TTL). **DISTINCT the
  covering-config trigger set** (V-4) and **dedup enqueues via `_job_id`** (V-5: BuildBusy
  retry storms + false `failed` metric).

## 8. Security Considerations

Touches tenant boundaries, provider keys, user-input processing — required.

- **Owner->project invariant (§4b-V-7, HIGH).** Nothing today stops a config with
  `project_id=P1` from owning a P2 chatroom/workspace/group. Add an app-layer
  `_assert_owner_in_project` (owner resolves project per kind) at **create and update**,
  mirroring `graphrag_config_service.py:74,195` and the `_assert_*_in_project` family. This
  is the code R11.10 ("no Concept Map spans projects") depends on and it does not yet exist.
- **Room-ACL-aware map authorization (§4b-V-8, HIGH).** Graphrag channels/reads authorize on
  bare project membership (`ws/graphrag.py:46-52`, `GET /graphrag/{id}/graph`
  `app/api/v1/graphrag.py:258-329`). A chatroom-owned map must inherit the room ACL
  (`conversation/application/access.py:52,125` `resolve_room_access`/`ensure_can_read`, as
  `ws/chatroom.py:59-64`) so a project member barred from the room by the §21.1 flags cannot
  read graph content distilled from it. workspace/agent_group maps -> project membership +
  owner opt-in. The `/graph` content endpoint outranks the build-status channel.
- **Retrieval coverage is authorization-bearing (§4b-V-9).** The coverage resolver must
  derive from the agent's real room binding + group memberships + current-room workspace
  (never a broader union), filter wide layers by `concept_map_enabled` **at query time**, and
  confirm project match. The enabled-flag is load-bearing, not cosmetic.
- **Cross-scope memory leakage (primary risk).** Wide layers aggregate many rooms — surfacing
  room B's concepts in room A. Mitigated by V-7/V-8/V-9 plus wide-layer default-off,
  Project-Owner-gated, audited (§5.6).
- **Project-Owner gate uses the strict check (§4b-V-10).** `concept_map_enabled` and
  membership endpoints use `is_project_owner` (`tenancy/interfaces/facade.py:48`), matching
  R10.10's upload gate — not the org-inheriting `RESOURCE_CREATE_EDIT`.
- **Builder key group.** Concept Map builder key is a config attribute; the R11.01
  distinctness rule is dropped for Concept Maps (§4a-G2). The create path must **skip the
  agent-load** for non-agent owners (§4b-V-6-adjacent) or it dereferences a null agent; keep
  the in-project check on `builder_key_group_id`.
- **Knowledge Map ingestion** reuses file-RAG's validated upload surface (MIME/size gate,
  SHA-256, virus scan, `ingest_service.py:115-423`) — do not open a second unvalidated path.
- No provider keys logged; evidence excerpts avoid raw keys.

## 9. Quality Notes

- **Existing debt (record; do not silently fix):**
  - Embed-model map + `_resolve_embed_key` duplicated between
    `graphrag_context_provider.py:29-33,158-184` and `app/workers/tasks/graphrag.py:30-34,110-133`
    — FU-1.
  - Per-query Neo4j+Qdrant client churn, no pool (`graphrag_context_provider.py:137-156`) —
    §7 makes fixing it blocking for layered retrieval.
  - 2PC builder complexity flagged by `docs/audits/graphrag-neo4j-audit.md` — do not extend
    blindly.
  - UUID-hard evidence coercion silently drops non-UUID tokens
    (`triple_extractor.py:163`, `graphrag_retrieve.py:119`) — §4a-G3.
  - Pre-existing embed-dimension collision on the shared `graphrag_{project_id}` collection
    (§4b-V-2) — already latent for two 1:1 configs with different embed models; fan-out makes
    it routine. FU-2.
  - No windowed extraction; whole delta in one prompt/embed batch (§4b-V-1) — FU-3.
  - Reconciler Phase-2 retry mints fresh point ids and skips the superseded sweep, leaking
    duplicate Qdrant points (`graphrag_reconciler.py:130,186-191`) — FU-4.
  - Enqueue has no dedup / `job_timeout == LOCK_TTL` (§4b-V-3,V-5) — FU-5.
- **Patterns to follow:** discriminated owner via typed FK + CHECK + partial unique indexes
  (`0042_prompt_studio.py:27-88`, `projects` owner-XOR `0002_tenancy.py:63-88`); inline
  external-store teardown on delete (`rag.py:335-395`, never rely on DB cascade); file-RAG
  many-agent scoping (`domain/models.py:113-148`); membership junction `chatroom_agents:50-70`;
  room ACL (`conversation/application/access.py:52,125`); cross-context read via
  `EvidenceFetcher` injection (`:203-224`); single-client-per-query reuse
  (`rag_context_provider.py:115-149`); PG-enum discipline (`graphrag_build_state` in
  `0013_graphrag.py`); facade-only cross-context.
- **Reuse inventory:** `Neo4jAsyncDriver`, `GraphRagVectorStore`, `LlmTripleExtractor`
  (Protocol only for docs), 2PC runner (`graphrag_builder.py`), `GraphRagRetrieveService`,
  `_merge_bundles`, `RedisLock`, `MIME_TO_PARSER` (`shared_kernel/text_extraction/parsers.py`),
  `GraphragGraphView.vue`, `useGraphragSocket`/`useRagConfigSocket`, RAG upload/virus-scan
  path, tus finalizer.

## 10. Risks and Rollback

- **Migration.** Must mutate `graphrag_configs` rows **in place** (stable `id`) — creating
  new rows orphans 100% of Neo4j/Qdrant data keyed by `graphrag_config_id` (§4a-G9). Forward:
  add `owner_kind` enum + three typed owner FK columns + CHECK + partial unique indexes,
  backfill each config to a singleton `agent_group` (member = its `agent_id`,
  `owner_agent_group_id` set) — delta-feed set-identical (`ANY(ARRAY[:id])` == `= :id`),
  subgraphs untouched. Down: rebuild `UNIQUE agent_id` from singleton members.
  **Reversibility holds only against freshly-migrated data**; once a group has ≠1 members or a
  chatroom/workspace owner exists, there is no lossless inverse (acceptable — the reverse
  pointer is redundant with membership post-migration).
- **Orphaned graph data (§5.9).** Typed FK CASCADE cleans the PG row but not Neo4j/Qdrant;
  the pre-existing agent-delete leak is live today. Mitigation: Phase-0 cleanup contract —
  every owner-delete path purges external stores inline (RAG pattern) + reconciler sweep; a DB
  cascade is never the sole teardown.
- **Layered retrieval regressions** — merge/budget bugs could crowd out the room layer.
  Mitigation: scope-precedence render order + per-layer fail-soft + merge-policy tests.
- **Build fan-out load** — one message feeding N layers. Mitigation: per-message build dedup
  (`_job_id`, §4b-V-5), per-config Redis lock (R11a.01), trigger thresholds, a dedicated
  worker lane (§4b-V-6/B3).
- **Unbounded initial build (§4b-V-1, CRITICAL).** A workspace/multi-member-group first build
  feeds the whole delta into one prompt/embed batch — silent zero-triple or stuck
  compensation. Mitigation: windowed extraction is a **hard Phase-2 prerequisite** (AC-14);
  singleton-group Phase 1 does not regress (bounded like today).
- **Embed-dimension collision (§4b-V-2, HIGH).** Divergent embed models on the shared project
  collection permanently fail Phase-2. Mitigation: pin one dimension per collection or shard
  (AC-15) — also fixes a pre-existing latent bug.
- **`job_timeout == LOCK_TTL` (§4b-V-3).** Long builds killed at the lock boundary, risking a
  double build. Mitigation: raise the timeout above the largest windowed build (AC-16).
- **Evidence generalization** touches shared `Triple`/`RelationEdge` used by Concept Map —
  regression-test conversation evidence after the typing change (Phase 3).
- **Staging** (`smap.rcsl.online`) has live per-agent configs — migrate in place.
- Per-phase rollback: each ships behind fail-soft retrieval, so a broken layer degrades to
  no-context, not a failed turn.

## 11. Acceptance Criteria

- [ ] AC-1: `graphrag_configs` loses `agent_id`; gains `owner_kind` + three typed nullable
  owner FK columns + a CHECK (exactly one non-null, matching `owner_kind`) + three per-kind
  partial unique indexes; migration mutates rows in place (id stable); deferred reverse FK
  handled. (migration test) [P1]
- [ ] AC-2: Delta-feed strategy per `owner_kind` uses `SELECT DISTINCT m.id`; agent_group
  variant is set-identical to the legacy query for a singleton member. (loader tests) [P1/P2]
- [ ] AC-3: `agent_groups` + `agent_group_members` (composite PK, CASCADE both legs) exist; a
  group owns a Concept Map; membership CRUD verifies project membership. [P1]
- [ ] AC-4: Existing configs migrate to singleton agent_groups; Neo4j/Qdrant data unchanged
  (same config id); build scope identical to pre-migration. (behavior-preservation test) [P1]
- [ ] AC-5: Key-group validation is owner-conditional — the builder-vs-consumer distinctness
  check is skipped for Concept Map owners; the in-project `builder_key_group_id` check
  remains. [P1]
- [ ] AC-6: Trigger resolution is reshaped from `agent_ids` to an owner set; one message fans
  out a build to every covering config. [P1/P2]
- [ ] AC-7: At turn time, retrieval merges every covering Concept Map (room + enabled groups +
  enabled workspace) via `_merge_bundles` pre-cap, scope-precedence render order (chatroom >
  agent_group > workspace), entity dedup, single final 2KB cap. [P2]
- [ ] AC-8: Per-layer fail-soft — one map erroring drops only that layer; a single
  Neo4j+Qdrant client is reused across all layers in a turn. [P2]
- [ ] AC-9: agent_group and workspace layers are absent from retrieval unless
  `concept_map_enabled` (checked at query time); chatroom layer default on; a user in room A
  never receives concepts sourced solely from room B via an unauthorized wide layer;
  enablement + membership + admin reset are audit-logged. [P2]
- [ ] AC-9a: An `_assert_owner_in_project` invariant rejects any config whose `owner_id`
  entity is outside the config's project, at create and update. (§4b-V-7) [P1/P2]
- [ ] AC-9b: A chatroom-owned Concept Map's `/graph` read and build-status channel enforce
  the room ACL (`resolve_room_access`/`ensure_can_read`); a project member barred from the
  room cannot read its graph. workspace/agent_group maps use project membership + owner
  opt-in; `concept_map_enabled` uses the strict `is_project_owner`. (§4b-V-8, V-10) [P2]
- [ ] AC-10: A Knowledge Map config (its own table) builds from uploaded files via shared
  `MIME_TO_PARSER` + a document `TripleExtractor`, and is queried at turn time as an Axis-1
  block independent of any Concept Map. [P3]
- [ ] AC-11: Evidence identity is an opaque string (message UUID string for conversation,
  `doc:{doc_id}:{chunk_idx}` for files); conversation evidence still resolves; file evidence
  is not silently dropped. (evidence round-trip test) [P3]
- [ ] AC-12: Knowledge Map uses `knowmap_{project_id}` via a parameterized collection prefix
  and shares the Neo4j driver + 2PC runner with no duplicated adapter code. (structure
  review) [P3]
- [ ] AC-13: `ruff/mypy/pytest` and `pnpm typecheck/lint/build` pass; `gen:api` regenerated;
  no hardcoded user-facing strings. [all]
- [ ] AC-14: Concept Map builds extract triples over a bounded message window; an initial
  build over a large scope (workspace/multi-member group) completes in bounded batches and
  never sends an over-limit prompt/embed batch nor silently yields zero triples. (§4b-V-1)
  [P2 prerequisite]
- [ ] AC-15: All Concept Maps sharing a project's `graphrag_{project_id}` collection use one
  embedding dimension; a config selecting a divergent embedding is rejected (or collections
  are sharded per config). (§4b-V-2) [P2 prerequisite]
- [ ] AC-16: Duplicate build enqueues are coalesced (`_job_id`) and `job_timeout` exceeds the
  largest windowed build; a busy config does not emit a false `failed` metric or a retry
  storm. (§4b-V-3, V-5) [P2 prerequisite]
- [ ] AC-17: The covering-config trigger set is DISTINCT — one message increments each
  covering config's counter exactly once. (§4b-V-4) [P1/P2]
- [ ] AC-18: A turn carrying multiple knowledge blocks (File-RAG + Knowledge-Map + N
  Concept-Map layers) stays within a combined system-prompt budget with narrow-scope
  precedence. (§4b-V-13) [P2]
- [ ] AC-19: `agents.graphrag_config_id` is **removed** in Phase 1 (column + DTO fields +
  form control + `isBound` badge + stale 1:1 comments/tests); orchestration/A2A/subagent/
  workflow remain untouched (already excluded); `gen:api` regenerated. (§4b-V-11, V-12,
  §4b-2-D, Q-13) [P1]
- [ ] AC-20: The graph engine is de-concreted — `GraphRagBuilder`/`RetrieveService`/
  `ReconciliationLoop` depend on repo + `ConfigLike` Protocols, the Qdrant collection prefix
  is a constructor param, and one `embed_resolution` helper is the single source (FU-1 drift
  retired). Knowledge Map adds no duplicated engine code. (structure review, Q-11) [P0/P3]
- [ ] AC-21: Graph evidence is a neutral opaque `evidence_refs` (Python) on the shared graph
  domain; the **physical Neo4j property key stays `evidence_msg_ids` until the document-evidence
  phase** (driver maps `evidence_refs <-> evidence_msg_ids`, zero data migration — verified,
  see Phase-0 dossier Q-2); Concept Map and Knowledge Map each decode refs via their own
  fetcher without a shared two-master type. (§5.7) [P0 Python / P3 physical rename]
- [ ] AC-22: Deleting a config OR any owner (agent, chatroom, workspace, agent_group) purges
  its Neo4j subgraph + Qdrant points inline (best-effort + audit), never relying on a DB
  cascade alone; the reconciler sweeps configs absent from Postgres. The pre-existing
  agent-delete leak is fixed. (§5.9, Q-13) [P0]
- [ ] AC-23: Concept Map edges/entities carry `first_seen`/`last_seen` timestamps derived from
  message `created_at`; the conversation timeline is recoverable via `evidence_refs`; the
  Concept Map retrieval strategy exposes a recency-weighting hook (Knowledge Map unaffected).
  (§5.8, Q-12) [P2]

## 12. Test Plan

- Migration up/down + in-place id-stability + singleton behavior-preservation (AC-1, AC-4).
- Per-`owner_kind` delta-loader strategies incl. multi-member DISTINCT (AC-2), near existing
  `_DbDeltaLoader` tests.
- agent_group CRUD + membership AuthZ (AC-3); owner-conditional key-group validation (AC-5);
  trigger fan-out reshape (AC-6).
- Layered merge: unit tests on precedence/dedup/budget + per-layer fail-soft; `TurnEngine`
  integration (AC-7, AC-8).
- Privacy: cross-room leakage test, wide layer disabled vs enabled (AC-9).
- Knowledge Map: file -> parser -> doc extractor -> Neo4j/Qdrant -> turn block (AC-10);
  evidence round-trip for both token kinds (AC-11); structure/lint for shared-adapter reuse
  (AC-12). CI gates (AC-13).

## 13. SRS Delta

Applied to `REQUIREMENTS.md` on approval (done). Recorded here as the authoritative text.

Amend:

- **[R11.05]** A Concept Map (conversation-derived Graph RAG) is owned by exactly one owner —
  a chatroom, an agent_group, or a workspace — modelled as typed nullable FK columns with a
  discriminator and a CHECK enforcing exactly one, plus a per-kind partial unique index (one
  Concept Map per owner). The 1:1 Agent binding is removed. (No polymorphic `owner_id`
  column: real FKs preserve referential integrity and enable cleanup on owner deletion.)

§11.4 Concept Map ownership and layering:

- **[R11.07]** `agent_group` is a first-class, project-scoped entity with a member set of
  Agents (`agent_group_members`). It may own a Concept Map.
- **[R11.08]** The Concept Map delta feed is scoped by owner and deduplicated by message id:
  `chatroom` -> that room's messages; `agent_group` -> the union of member agents' room
  messages (`SELECT DISTINCT`); `workspace` -> that workspace's room messages.
- **[R11.09]** At agent invocation, retrieval draws on every Concept Map covering the agent
  in the current room (its chatroom map, each enabled agent_group map it belongs to, and the
  enabled workspace map), merged under the 2 KB cap with narrow-scope precedence
  (chatroom > agent_group > workspace) and entity dedup; retrieval is per-layer fail-soft.
- **[R11.10]** Privacy default-strict: the chatroom layer is enabled by default; agent_group
  and workspace layers require an explicit `concept_map_enabled` flag on the owner, settable
  only by Project Owner and audit-logged. No Concept Map spans projects.
- **[R11.11]** The Concept Map builder key group is declared on the owning config and
  validated only for project membership. Because a Concept Map has no single consumer, the
  [R11.01] builder-vs-consumer distinctness rule does not apply to Concept Maps.

§11.5 Knowledge Map — Graph RAG over files (Axis 1):

- **[R11.12]** A Knowledge Map is a designer-authored Graph RAG built from uploaded documents
  (the §10 file-RAG sources), not conversation. It is a distinct config, project-scoped with
  a per-Agent allowlist, mirroring [R10.11].
- **[R11.13]** Knowledge Map ingestion reuses the shared document parser to obtain document
  text, extracts triples via a document-oriented extractor, and persists to its own Neo4j
  subgraph + Qdrant collection (`knowmap_{project_id}`), scoped by its config id.
- **[R11.14]** At agent invocation, an attached Knowledge Map is queried as an Axis-1 system
  block beside file-RAG, independent of any Concept Map.
- **[R11.15]** Knowledge Map and Concept Map are distinct **product** subsystems (separate
  config, domain, and UI) that reuse a single shared graph **engine** through Protocol seams
  (Neo4j driver, Qdrant store with a parameterized collection prefix, 2PC runner, one
  embedding-resolution helper, build-state machine, and the `TripleExtractor` Protocol) plus a
  neutral shared graph-domain type. Graph evidence is a neutral opaque reference token
  (`evidence_refs`) decoded per subsystem — a message id for conversation, a document chunk
  ref for files. Engine plumbing is never forked per subsystem.

§11.6 Ownership authorization and build robustness (added post-verification):

- **[R11.16]** Every graph config satisfies an owner->project invariant: the entity named by
  `owner_id` (chatroom, agent_group, or workspace) lives in the config's project. Enforced at
  create and update; a violating request is rejected.
- **[R11.17]** A chatroom-owned Concept Map inherits the room's access ACL (§21.1): only
  principals permitted to read the room may read its graph or subscribe to its build status.
  agent_group and workspace maps are gated by project membership plus their
  `concept_map_enabled` opt-in; `concept_map_enabled` is set only by a strict Project Owner.
- **[R11.18]** Concept Map builds extract triples over a bounded message window; an owner
  delta larger than the window is processed in bounded batches, so an initial build over a
  large scope cannot exceed model/embedding limits or silently produce zero triples.
- **[R11.19]** All Concept Maps sharing a project's graph vector collection use a single
  embedding model/dimension; a config whose builder key group would select a different
  embedding dimension is rejected (or the collection is sharded per config). When several
  knowledge blocks (File RAG, Knowledge Map, Concept Map layers) are injected in one turn,
  their combined size is bounded with narrow-scope precedence.

§11.7 Graph data lifecycle and temporal Concept Map (added post-debt-review):

- **[R11.20]** Deleting a graph config, or any owner of one (agent, chatroom, workspace,
  agent_group), purges the config's Neo4j subgraph and Qdrant points as part of the deleting
  operation (best-effort, audit-logged); a database cascade is never the sole teardown of an
  external store. A reconciler sweeps external stores for graph ids absent from Postgres.
- **[R11.21]** A Concept Map is a temporal knowledge graph: its entities and relations carry
  first-seen/last-seen timestamps derived from the source messages' timestamps, and the exact
  User<->Agent conversation timeline is recoverable from the evidence so an agent can reason
  about ordering and causality. Concept Map retrieval may weight results by recency. Knowledge
  Maps are non-temporal. (Time-travel / bitemporal queries are a separate future capability.)

## 14. Open Questions

- Resolved Q-A (Q-13): drop `agents.graphrag_config_id` in Phase 1, collapse bind==membership.
- Resolved Q-10/Q-11/Q-12/Q-13 in §3.
- Open Q-B: Neo4j/Qdrant client pooling scope for N-fan-out — per-turn single client
  (minimum) vs a longer-lived pool.
- Open Q-C: `agent_group` home confirmed as a sub-module of `agents` — confirm at build.
- Open Q-D: Second `TripleExtractor` — separate concrete class vs source-parameterized prompt
  on the existing one.
- Open Q-E: Do graph blocks gain citation persistence (a multi-source-shaped `graph_sources`
  metadata field + a renderer), or stay citation-less as today? (§4b-V-14) — not required by
  any goal; scope decision.
- Open Q-F: Embed-dimension policy (§4b-V-2/R11.19) — pin one model per project collection vs
  shard the collection per config. Affects Qdrant bootstrap + config validation.

## Phasing (revised post-verification + debt review)

0. **Phase 0 — Engine hardening & cleanup (foundational refactor, behavior-preserving).**
   De-concrete the engine via Protocols + `ConfigLike`; parameterize the Qdrant prefix;
   extract the single `embed_resolution` helper (retire FU-1 drift); extract the neutral
   graph domain with opaque `evidence_refs` (rename Neo4j property). Fix the pre-existing
   agent-delete graph leak and establish the `cascade_external_stores`-on-every-delete
   contract + reconciler orphan-sweep. AC-20, AC-21, AC-22. *(Pure refactor + live-bug fix;
   ships value on its own and de-risks every later phase.)* **Build plan:**
   `docs/tasks/2026-07-07-graphrag-phase0-engine-cleanup/spec.md`.
1. **Phase 1 — Decouple to typed-FK owner + agent_group, behavior-preserving (refactor +
   the agent_group entity).** In-place owner migration (typed FK + CHECK + partial unique,
   §5.2); `agent_groups`/`agent_group_members`; backfill singleton groups (reproduces today's
   scope, AC-4); agent_group delta strategy with DISTINCT; owner-conditional key-group
   validation; trigger + repo reshape; **drop the reverse pointer** (AC-19); ~15 source + ~6
   test files. AC-1..AC-6, AC-17, AC-19. *(agent_group moved into Phase 1 because only a
   singleton group is behavior-equivalent to today; a chatroom owner is not.)*
2. **Phase 2a — Builder hardening (prerequisite for wide ownership).** Windowed/batched
   extraction (AC-14); one embedding dimension per project collection or per-config sharding
   (AC-15); enqueue dedup + `job_timeout` fix (AC-16); DISTINCT covering-config trigger set
   (AC-17); owner->project invariant at create/update (AC-9a). These close the CRITICAL/HIGH
   build + isolation gaps (§4b-V-1..V-7) before any workspace/multi-member owner can build
   safely. Some (V-2 embed-dimension) also fix pre-existing latent bugs.
3. **Phase 2b — chatroom + workspace layers + layered retrieval + privacy + temporal
   (feature).** chatroom/workspace delta strategies; coverage resolution
   (authorization-bearing); provider fan-out (list, client reuse, per-layer fail-soft,
   scope-precedence); combined multi-block budget; `get_chatroom` fetch; `concept_map_enabled`
   gating + audit; room-ACL map authorization; Concept Map edge/entity timestamps + recency
   hook (§5.8). AC-7, AC-8, AC-9, AC-9b, AC-18, AC-23.
4. **Phase 3 — Knowledge Map (feature).** `knowledge_map_configs` over the shared engine;
   document extractor (prompt+renderer only); `build_doc_evidence_fetcher`; Axis-1 turn block.
   The neutral-domain / evidence-refs work is already done in Phase 0. AC-10, AC-11, AC-12.
5. **Phase 4 — Frontend re-home (feature).** Split Knowledge tab; owner-centric Concept Map
   panels in the correct slices (chatroom/workspace -> `conversation`, agent_group ->
   `agents`); extract `KnowledgeGraphCanvas` + `useGraphLayout` to `shared/`; owner-scoped
   API methods + owner-keyed socket. AC-13 UI parts. (The `isBound` badge is removed in
   Phase 1 with the pointer, AC-19.)

Each phase is a separate `/spec`-refined `/build` task linking back to this blueprint.
Phase 0 de-risks all later phases; Phase 2a is a hard gate — workspace and multi-member-group
ownership must not ship before it. Time-travel (temporal bitemporal queries) is a separate
future spec, not a phase here.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: De-duplicate the embed-model map / `_resolve_embed_key` — **pulled into Phase 0**
  (AC-20) as the single `embed_resolution` helper; also reconcile the already-drifted
  `list_ordered` vs `list_ordered_carried` call while extracting.
- FU-2: Pre-existing embed-dimension collision on the shared `graphrag_{project_id}`
  collection (§4b-V-2) — pulled into Phase 2a (AC-15) because fan-out makes it routine; if
  Phase 2a slips, this remains a latent production bug for any project with two graph configs
  on different embed models.
- FU-3: No windowed extraction (§4b-V-1) — pulled into Phase 2a (AC-14).
- FU-4: Reconciler Phase-2 retry mints fresh Qdrant point ids and skips the superseded sweep,
  leaking duplicate points (`graphrag_reconciler.py:130,186-191`); amplified by fan-out.
- FU-5: Enqueue lacks dedup and `job_timeout == LOCK_TTL` (§4b-V-3,V-5) — pulled into
  Phase 2a (AC-16).
- FU-6: `ensure_graphrag_collection` is check-then-create, not idempotent
  (`graphrag_vector_store.py:56-62`) — concurrent first builds race to a 409 (§4b-V-2/B2).
- FU-7: Trigger counter is skipped while a build is `RUNNING`
  (`graphrag_triggers.py:58`); messages during a long build are lost for trigger accounting
  (§4b-V-6/C3).
- FU-8: Temporal time-travel / bitemporal Concept Map queries ("what did we know as of date
  X") — a dedicated future spec (§5.8, R11.21), building on the timestamps + per-build
  provenance laid down here.
