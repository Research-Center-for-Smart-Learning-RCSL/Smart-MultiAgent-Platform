---
type: feature
status: approved
created: 2026-07-07
requirements: [R10.06, R10.11, R11.01, R11.02, R11.03, R11.05, R11.06, R11.07, R11.08, R11.09, R11.10, R11.11, R11.12, R11.13, R11.14, R11.15, R11a.01]
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
  - Concept Map     (today's GraphRAG, re-homed here and given a polymorphic owner)
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
  UNIQUE 1:1 binding, replacing it with a **polymorphic owner** (`owner_kind` + `owner_id`)
  over `chatroom`, `agent_group`, and `workspace`.
- Introduce `agent_group` as a first-class entity that can own a Concept Map.
- Support **layered retrieval**: an agent's turn draws on every Concept Map whose scope
  covers it (its room + its groups + its workspace), merged under one retrieval budget
  with narrow-scope precedence.
- Introduce the **Knowledge Map** (Axis 1) as GraphRAG built from uploaded files, as a
  distinct config, sharing the low-level graph adapters and the shared document parser.
- Keep the two subsystems (Knowledge Map, Concept Map) separate at the domain/config/UI
  level while sharing the low-level graph adapters (Neo4j driver, Qdrant store, 2PC
  runner) and the `TripleExtractor` Protocol.
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

## 5. Design

### 5.1 The four quadrants, mapped to code

| Quadrant | Axis | Source | Owner / scope | Status today |
|---|---|---|---|---|
| File | 1 | uploaded docs | project + agent allowlist | Exists (§10) |
| Knowledge Map | 1 | uploaded docs | project + agent allowlist (own config table) | **NEW** |
| General | 2 | chat history | per agent (transcript) | Exists (§9.3) |
| Concept Map | 2 | chat history | **polymorphic: chatroom / agent_group / workspace** | Exists as 1:1-agent; re-homed + decoupled |

### 5.2 Concept Map — polymorphic owner

Replace `graphrag_configs.agent_id UNIQUE` with `owner_kind` + `owner_id`, **in place**
(§4a-G9), preserving `graphrag_configs.id` so all Neo4j/Qdrant data stays correctly scoped:

```
owner_kind ENUM('chatroom','agent_group','workspace')   -- new PG enum, create_type=False
owner_id   UUID
UNIQUE (owner_kind, owner_id)                            -- was: UNIQUE(agent_id)
```

Each owner scope is its own `graphrag_configs` row -> its own isolated Neo4j subgraph and
Qdrant points (already scoped by `graphrag_config_id`, `neo4j_driver.py:49-228`,
`graphrag_vector_store.py:68-115`). `GraphRagConfig` drops `agent_id`, gains `owner`.

**Delta-feed strategy keyed on `owner_kind`** — variants of `app/workers/tasks/graphrag.py:88-91`,
all with `SELECT DISTINCT m.id` (§4a-G8):

- `chatroom`: `WHERE m.chatroom_id = :owner_id`
- `agent_group`: `JOIN chatroom_agents ca ... WHERE ca.agent_id = ANY(:member_ids)` (DISTINCT)
- `workspace`: `JOIN chatrooms cr ... WHERE cr.workspace_id = :owner_id`

**Key-group validation becomes owner-conditional** (§4a-G2): drop the builder-vs-consumer
distinctness check for Concept Maps; keep only the in-project check on
`builder_key_group_id`. Move authority out of the agents-side mirror.

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
resolution; whether to drop the column in Phase 1 or leave it dormant until Phase 2 is
Open Q-A.

### 5.5 Knowledge Map — GraphRAG over files (Axis 1)

A distinct Axis-1 subsystem (its **own** config table, not `graphrag_configs`), sharing the
low-level adapters (§4a-G3/G4):

- `knowledge_map_configs`: project-scoped, agent allowlist (mirror `RagConfig` /
  `RagDocument.agent_ids`). No `agent_id`-as-source semantics.
- Source: uploaded documents parsed via the **shared** `MIME_TO_PARSER`
  (`shared_kernel/text_extraction/parsers.py`); a **second concrete `TripleExtractor`** with
  a document-oriented prompt feeds the shared 2PC builder via its own `DeltaLoader`/source
  shape.
- Evidence: requires the **generalized opaque evidence identity** (`tuple[str,...]`, §4a-G3)
  encoding file evidence as `doc:{doc_id}:{chunk_idx}`, plus a `build_doc_evidence_fetcher`
  mapping those to `rag_chunks` text. No Neo4j/Qdrant schema change.
- Storage: its own Qdrant collection `knowmap_{project_id}` — requires **parameterizing the
  collection-prefix** (today a module-level free function `graphrag_collection_name()`); its
  own Neo4j subgraph via the shared driver scoped by its config id.
- Retrieval: a Knowledge Map block in `turn_engine` beside File RAG; read-only; fail-soft.
- Billing: designer/project key (authored knowledge; not the Axis-2 builder-key split).

### 5.6 Privacy model (default strict)

- `chatroom` layer: enabled by default (a room's memory of its own conversation).
- `agent_group` and `workspace` layers: **disabled by default**; require `concept_map_enabled`
  on the owner, settable only by Project Owner, enablement audit-logged.
- Retrieval never folds a wide layer an agent's room is not authorized for. See §8.

### Options considered

**Option A — Single engine, two kinds.** One config table with a `kind` discriminator.
Least duplication but couples two divergent products. **Rejected per Q-3.**

**Option B — Two subsystems, shared low-level adapters (chosen).** Separate domain,
config tables, services, UI; shared Neo4j/Qdrant/2PC + `TripleExtractor` Protocol +
`MIME_TO_PARSER`. Clean boundaries, independent evolution; costs one more config table and
the evidence-typing generalization (§4a-G3). **Chosen (Q-3, Q-5).**

**Option C — Two fully independent stacks.** Duplicates the 2PC/compensation/reconciler —
the most error-prone code. **Rejected.**

Concept Map owner: **workspace-only** rejected per Q-4; `workflow.id` rejected because
workflows don't own the chatrooms where agents chat.

### Decision

Adopt the two-axis model. Concept Map gets a polymorphic in-place owner (§5.2) with
`agent_group` first-class (§5.3) and layered, narrow-precedence, per-layer-fail-soft
retrieval (§5.4). Knowledge Map is a separate Axis-1 subsystem with its own config table,
sharing adapters + parser and requiring the evidence-identity generalization (§5.5).
Privacy is default-strict (§5.6). Given up: a unified config (Option A) for clean product
boundaries, and one-shot delivery for phased shipments.

## 6. Detailed Changes

SoC: the graph **engine** (Neo4j/Qdrant adapters, 2PC runner, `TripleExtractor` Protocol)
stays in `knowledge`, reused by both subsystems. `conversation` stays the message source
via facade (never imported by `knowledge`).

Full decouple surface (§4a-G1) — Concept Map, Phases 1-2:

- **`knowledge` domain**: `GraphRagConfig.agent_id`/draft (`domain/graphrag.py:34,46`) ->
  `owner`. Evidence typing `Triple`/`RelationEdge` -> `tuple[str,...]` (Phase 3).
- **`knowledge` repo** (`graphrag_repositories.py`): `_row_to_config:29`; `create:43-68`;
  409 `GraphRagConfigAlreadyExists(agent_id):64-67` -> per-owner; `list_for_agents:100-119`
  -> `list_for_owners`; immutability note `:167-171`.
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
- **`agents` context (reverse pointer)**: `models.py:141,229`; `repositories.py:53,119,141`;
  `agent_service.py:227-261,301-321,411-458`; `api/v1/agents.py:75,106,127,150,219,314,324`.
- **GraphRAG API** (`app/api/v1/graphrag.py:71,86,132-137,185-189`): `agent_id` ->
  owner fields.
- **New** `agent_groups` + `agent_group_members` tables/facade/CRUD; `concept_map_enabled`
  on group + workspace.
- **Migration**: drop `graphrag_configs.agent_id` UNIQUE + deferred reverse FK
  (`0013_graphrag.py:52-54,80-87`); add owner columns + enum (in-place); backfill singleton
  groups. New PG enum `owner_kind` via `CREATE TYPE ... ENUM` in `upgrade`, `DROP TYPE` in
  `downgrade`, column `pg.ENUM(..., create_type=False)` (exemplar: `graphrag_build_state`
  in `0013_graphrag.py:38-42,65,98`; never `sa.Text`).
- **Tests** (~6): `test_graphrag_triggers.py`, `test_agent_service.py:306-342,489-583`,
  `test_agent_config_project_guard.py`, `test_graphrag_builder.py`, `test_graphrag_retrieve.py`,
  `test_graphrag_reset.py` — all build `GraphRagConfig(agent_id=...)`.

Knowledge Map (Phase 3): `knowledge_map_configs` table + service + facade; second concrete
extractor; parameterized Qdrant prefix; evidence generalization across
`domain/graphrag.py`, `triple_extractor.py:158-165`, `graphrag_retrieve.py:114-122`,
`EvidenceFetcher` signature + `build_doc_evidence_fetcher`; `turn_engine` Axis-1 block.

- **API contract** — `gen:api` rerun required: yes.
- **Frontend** (Phase 4) — split Knowledge tab (Axis-1 stays on agent; Concept Map -> owner
  panels: chatroom memory, agent_group editor, workspace settings); `agent_groups` UI;
  reuse `GraphragGraphView.vue`. All strings via `$t()`.
- **Deploy/config** — add `knowmap_{project_id}` to `smap/bootstrap/qdrant_init.py`; no new
  stores.

## 7. NFR Checklist

- [ ] i18n — all new owner-panel/agent_group strings via `$t()`.
- [ ] Audit log — enabling a wide layer, agent_group membership changes, admin reset.
- [ ] Tenant isolation — every new endpoint verifies project membership; owner_id resolves
  in-project; no owner spans projects.
- [ ] Error handling UX — extend `useGraphragSocket` build-status states to Knowledge Map +
  per-owner Concept Maps; RFC 7807 via `knowledge/interfaces/error_mapping.py`.
- [ ] Performance — layered retrieval is N×M embed+search+traverse round-trips with no
  shared embeddings (§4a-G6); single-client reuse is a prerequisite, not optional. Build
  fan-out multiplies worker load — dedup builds per message. Index the new delta-feed
  predicates. The 2KB cap is a blind binary-search truncation with no scope notion
  (`domain/graphrag.py:135-153`) — precedence must be render-order, not cap logic.

## 8. Security Considerations

Touches tenant boundaries, provider keys, user-input processing — required.

- **Cross-scope memory leakage (primary risk).** Wide layers aggregate many rooms;
  retrieval could surface room B's concepts in room A. Mitigations: (1) wide layers
  default-disabled, Project-Owner-gated, audited (§5.6); (2) coverage resolved strictly from
  membership/scope — an agent gets a group layer only if a member and the group is enabled;
  (3) no layer crosses projects (owner_id validated in-project on every read/build).
- **Builder key group.** Concept Map builder key is a config attribute; the R11.01
  builder-vs-consumer distinctness rule is dropped for Concept Maps (§4a-G2) — ensure the
  remaining in-project check on `builder_key_group_id` is enforced.
- **Knowledge Map ingestion** reuses file-RAG's validated upload surface (MIME/size gate,
  SHA-256, virus scan, `ingest_service.py:115-423`) — do not open a second unvalidated path.
- **AuthZ on new endpoints** — agent_group CRUD, membership, owner-scoped Concept Map config
  each verify org/project membership before returning data.
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
- **Patterns to follow:** file-RAG many-agent scoping (`domain/models.py:113-148`);
  membership junction `chatroom_agents:50-70`; cross-context read via `EvidenceFetcher`
  injection (`:203-224`); single-client-per-query reuse (`rag_context_provider.py:115-149`);
  PG-enum discipline (`graphrag_build_state` in `0013_graphrag.py`); facade-only cross-context.
- **Reuse inventory:** `Neo4jAsyncDriver`, `GraphRagVectorStore`, `LlmTripleExtractor`
  (Protocol only for docs), 2PC runner (`graphrag_builder.py`), `GraphRagRetrieveService`,
  `_merge_bundles`, `RedisLock`, `MIME_TO_PARSER` (`shared_kernel/text_extraction/parsers.py`),
  `GraphragGraphView.vue`, `useGraphragSocket`/`useRagConfigSocket`, RAG upload/virus-scan
  path, tus finalizer.

## 10. Risks and Rollback

- **Migration.** Must mutate `graphrag_configs` rows **in place** (stable `id`) — creating
  new rows orphans 100% of Neo4j/Qdrant data keyed by `graphrag_config_id` (§4a-G9). Forward:
  add owner columns + `owner_kind` enum, backfill each existing config to a singleton
  `agent_group` (member = its `agent_id`) — delta-feed is set-identical to today
  (`ANY(ARRAY[:id])` == `= :id`), subgraphs untouched. Down: rebuild `UNIQUE agent_id` from
  singleton members. **Reversibility holds only against freshly-migrated data**; once a group
  has ≠1 members or a chatroom/workspace owner exists, there is no lossless inverse.
- **Layered retrieval regressions** — merge/budget bugs could crowd out the room layer.
  Mitigation: scope-precedence render order + per-layer fail-soft + merge-policy tests.
- **Build fan-out load** — one message feeding N layers. Mitigation: per-message build dedup,
  per-config Redis lock (R11a.01), trigger thresholds.
- **Evidence generalization** touches shared `Triple`/`RelationEdge` used by Concept Map —
  regression-test conversation evidence after the typing change (Phase 3).
- **Staging** (`smap.rcsl.online`) has live per-agent configs — migrate in place.
- Per-phase rollback: each ships behind fail-soft retrieval, so a broken layer degrades to
  no-context, not a failed turn.

## 11. Acceptance Criteria

- [ ] AC-1: `graphrag_configs` loses `agent_id`; gains `owner_kind`+`owner_id`, UNIQUE
  `(owner_kind, owner_id)`; migration mutates rows in place (id stable); deferred reverse FK
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
  `concept_map_enabled`; chatroom layer default on; a user in room A never receives concepts
  sourced solely from room B via an unauthorized wide layer; enablement + membership + admin
  reset are audit-logged. [P2]
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

- **[R11.05]** A Concept Map (conversation-derived Graph RAG) is owned by exactly one
  **owner** `(owner_kind, owner_id)`, `owner_kind ∈ {chatroom, agent_group, workspace}`. The
  1:1 Agent binding is removed. `UNIQUE(owner_kind, owner_id)`.

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
- **[R11.15]** Knowledge Map and Concept Map are distinct subsystems (separate config,
  services, UI) that share the low-level graph adapters (Neo4j driver, Qdrant store, 2PC
  runner) and the `TripleExtractor` Protocol. Graph evidence is identified by an opaque
  reference token (a message id for conversation, a document chunk ref for files).

## 14. Open Questions

- Open Q-A: Drop `agents.graphrag_config_id` in Phase 1 or leave dormant until Phase 2's
  coverage resolution replaces it? (retrieval-path decision)
- Open Q-B: Neo4j/Qdrant client pooling scope for N-fan-out — per-turn single client
  (minimum) vs a longer-lived pool.
- Open Q-C: `agent_group` home confirmed as a sub-module of `agents` — confirm at build.
- Open Q-D: Second `TripleExtractor` — separate concrete class vs source-parameterized prompt
  on the existing one.

## Phasing (revised post-verification)

1. **Phase 1 — Decouple to polymorphic owner + agent_group, behavior-preserving (refactor +
   the agent_group entity).** In-place owner migration; `agent_groups`/`agent_group_members`;
   backfill singleton groups (reproduces today's scope, AC-4); agent_group delta strategy
   with DISTINCT; owner-conditional key-group validation; trigger + repo + reverse-pointer
   reshape; ~15 source + ~6 test files. AC-1..AC-6. *(agent_group moved into Phase 1 because
   only a singleton group is behavior-equivalent to today; a chatroom owner is not.)*
2. **Phase 2 — chatroom + workspace layers + layered retrieval + privacy (feature).**
   chatroom/workspace delta strategies; coverage resolution; provider fan-out (list, client
   reuse, per-layer fail-soft, scope-precedence); `get_chatroom` fetch; `concept_map_enabled`
   gating + audit. AC-7, AC-8, AC-9.
3. **Phase 3 — Knowledge Map (feature).** `knowledge_map_configs`; shared parser + document
   extractor; evidence-identity generalization (shared type change — re-test Concept Map);
   parameterized Qdrant prefix; Axis-1 turn block. AC-10, AC-11, AC-12.
4. **Phase 4 — Frontend re-home (feature).** Split Knowledge tab; owner-centric Concept Map
   panels; agent_group UI; reuse visualizer. AC-13 UI parts.

Each phase is a separate `/spec`-refined `/build` task linking back to this blueprint.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: De-duplicate the embed-model map / `_resolve_embed_key` shared between
  `graphrag_context_provider.py` and `app/workers/tasks/graphrag.py` (extract to a shared
  `knowledge` helper without violating the no-`app`-import rule).
