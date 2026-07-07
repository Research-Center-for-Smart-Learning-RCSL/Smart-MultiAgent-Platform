---
type: feature
status: draft
created: 2026-07-07
requirements: [R10.06, R10.11, R11.01, R11.02, R11.03, R11.05, R11.06, R11a.01]
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
- Introduce the **Knowledge Map** (Axis 1) as GraphRAG built from uploaded files, sharing
  the file-RAG ingestion path and the same low-level graph adapters.
- Keep the two subsystems (Knowledge Map, Concept Map) separate at the domain/config/UI
  level while sharing the low-level graph adapters (Neo4j driver, Qdrant store, triple
  extractor, retrieval provider).
- Enforce **default-strict privacy**: only the chatroom layer is on by default; the
  agent_group and workspace layers must be explicitly enabled on their owner entity.

**Non-goals**

- Not a rewrite of the file-RAG chunk/vector pipeline (§10) — Knowledge Map reuses it as
  a source, it does not replace it.
- Not changing the 2PC build/compensation mechanics (§11.2a) — the state machine is
  reused unchanged; only the owner scoping and the delta-feed query change.
- Not merging Neo4j/Qdrant into a single store, and not introducing a new graph database.
- Not building cross-project memory. Every owner remains inside a single project's tenant
  boundary; no layer ever spans projects.
- Not delivering all four quadrants in one shipment — phasing (§ Phasing) splits this into
  independently buildable tasks. The phase order is a recommendation pending user sign-off.
- Not changing sub-agent inheritance semantics beyond what the polymorphic owner requires
  (today sub-agents inherit neither `rag_config_id` nor `graphrag_config_id`,
  `orchestration/application/subagent_service.py:257-280`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is this a pure decouple/rename (refactor) or also new Knowledge Map (feature)? | Produce the full four-quadrant blueprint first, then phase it. | The two axes are one coherent architecture; splitting the analysis would re-derive the same model twice. |
| Q-2 | New scope unit for the conversation graph after decoupling? | Agent group / workflow scope. | Superseded and generalized by Q-4/Q-7 into the polymorphic-owner model. |
| Q-3 | Organize the graph engine as one engine with two kinds, or two subsystems? | Two separate subsystems. | User's call. Knowledge Map and Concept Map differ enough in lifecycle/source/UI to model separately. |
| Q-4 | Which real entity owns the Concept Map, given no `agent_group` exists in code? | Create `agent_group` as a first-class entity. | User wants explicit user-composed memory units, not a workspace-implicit boundary. |
| Q-5 | How independent must the "two subsystems" be at the infra layer? | Domain/config/UI separate; share the low-level adapters. | Avoids duplicating Neo4j/Qdrant/2PC/extractor while keeping product concepts clean. |
| Q-6 | Which messages feed a group/workspace Concept Map? | Decouple owner so a chatroom can have its own AND a workspace can have its own. | Led to the polymorphic multi-scope model in §5. |
| Q-7 | Which Concept Map layers does this blueprint cover? | All three: chatroom, agent_group, workspace. | Full layered memory hierarchy. |
| Q-8 | How is cross-scope privacy (wide layers aggregate many rooms) guarded? | Default strict; wide layers (group/workspace) require explicit enablement. | Honors the multi-tenant AuthZ hard rule; narrow layer is the safe default. |
| Q-9 | What is the first buildable phase after the blueprint? | Arrange phase order after the blueprint is finalized. | Phasing is a recommendation in this dossier, confirmed at approval. |

## 4. Current State

### 4.1 One context, two subsystems already

Both RAG and GraphRAG live in `contexts/knowledge/` — physically separate from the
`agents` and `conversation` contexts. The `agents` context only validates attachment;
retrieval is consumed read-only at runtime.

- File-RAG: `contexts/knowledge/domain/models.py:113-148` (`RagConfig`, `RagDocument`);
  ingestion `application/ingest_service.py:115-423`; vectors in Qdrant `rag_{project_id}`
  (`infrastructure/qdrant_store.py`), sources in MinIO `rag-sources`
  (`ingest_service.py:77-81`).
- GraphRAG (the conversation graph): `contexts/knowledge/domain/graphrag.py:30-153`;
  builder `application/graphrag_builder.py:124-393`; Neo4j graph
  `infrastructure/neo4j_driver.py`; entity vectors Qdrant `graphrag_{project_id}`
  (`infrastructure/graphrag_vector_store.py:31-33`).

### 4.2 The mislabel: today's GraphRAG is Axis-2 memory wearing Axis-1 clothing

GraphRAG's data source is **conversation history**, not files. The delta loader reads
messages from every chatroom the agent is a member of —
`app/workers/tasks/graphrag.py:83-97`:
`messages m JOIN chatrooms cr JOIN chatroom_agents ca WHERE ca.agent_id = :agent_id`.
The domain docstring states it plainly: "a persistent graph built from conversation
history" (`contexts/knowledge/domain/graphrag.py:1-8`). Yet the frontend renders it in the
agent editor's **Knowledge** tab beside file-RAG (`AgentDetailView.vue:935-1000`), framing
it as designer-authored knowledge. This blueprint corrects that framing.

### 4.3 The 1:1 agent coupling — the three points to break

1. `graphrag_configs.agent_id` is `UNIQUE NOT NULL`
   (`contexts/knowledge/infrastructure/graphrag_tables.py:21-27`; migration
   `alembic/versions/0013_graphrag.py:52-54`, "R11.05 — 1:1 with an agent").
2. Reverse pointer `agents.graphrag_config_id` (single-valued,
   `contexts/agents/infrastructure/tables.py:50`).
3. The delta loader defines "what the graph knows" by one agent's rooms
   (`graphrag.py:91`).

`GraphRagConfig` carries `agent_id` as an intrinsic field
(`domain/graphrag.py:30-42`). File-RAG is already looser (project-scoped `RagConfig`,
many-agent allowlist `RagDocument.agent_ids`, `models.py:147`) — it is the existing
template for the many-agent decoupling applied here.

### 4.4 Retrieval assembly — one point, read-only, fail-soft

`TurnEngine._run_locked` (`contexts/agents/application/runtime/turn_engine.py:794-1138`,
worker-only) is the single assembly point where all context sources are folded into
`system_parts`. Both retrievers are queried from one `knowledge_queries` built off the
latest user message (`turn_engine.py:900-902`):

- File RAG block: `_rag_context` (`turn_engine.py:903-905, 1658-1663`) via
  `RagContextProvider.query(rag_config_id, ...)`.
- GraphRAG block: `_graphrag_context` (`turn_engine.py:906-908, 1666-1670`) via
  `GraphRagContextProvider.query(graphrag_config_id, ...)` — **single config id today**.

Both fail soft (retrieval error never fails the turn:
`graphrag_context_provider.py:85-91`, `rag_context_provider.py:150-156`). GraphRAG
evidence excerpts are fetched back from conversation via an injected `EvidenceFetcher`
(`graphrag_context_provider.py:203-224`) so `knowledge` never imports `conversation`.

### 4.5 Ownership hierarchy and the missing "group"

`orgs -> projects -> workspaces -> chatrooms -> messages`. Agents are project-scoped and
join chatrooms via `chatroom_agents(chatroom_id, agent_id, role)`
(`contexts/conversation/infrastructure/tables.py:50-70`).

- **No `agent_group` entity exists** anywhere in the codebase (grep-confirmed).
- `Workflow` (`contexts/workflow/domain/models.py:86-95`, table `tables.py:17-39`) is
  workspace-scoped and references agents/chatrooms only as UUID strings inside its
  `definition` JSONB — no relational membership; the linter extracts refs at
  `application/linter.py:111-120,361`.
- `workflow_runs` (`contexts/orchestration/infrastructure/tables.py:17-39`) is ephemeral
  (archived/deleted ~90d) — a poor config owner.
- `workspace` (`contexts/conversation/infrastructure/tables.py:10`) already co-owns both
  `chatrooms.workspace_id` and `workflows.workspace_id` — the tightest existing boundary.

### 4.6 Frontend & existing tests

- All RAG/GraphRAG UI lives in the `agents` slice (per SRS §24.2,
  `docs/implement/E-agents-knowledge.md:288`): routes `slices/agents/routes.ts:19-37`;
  agent Knowledge tab `AgentDetailView.vue:935-1000`; RAG config detail
  `RagConfigDetailView.vue`; GraphRAG config list `GraphragConfigListView.vue`; graph
  visualizer `GraphragGraphView.vue` (Vue-Flow, already built).
- "Conversation memory" today = only the `context_mode: compact` running-summary
  (§9.3, R9.09-R9.11), configured on the agent editor **General** tab
  (`AgentDetailView.vue:820-855`), executed by the backend summariser
  (`contexts/agents/application/runtime/summariser.py`, `.../context.py`).
- Relevant audit: `docs/audits/graphrag-neo4j-audit.md` (2026-06-28) flags 2PC-persistence,
  lock-steal, and reconciler non-determinism — read before touching the builder.

## 5. Design

### 5.1 The four quadrants, mapped to code

| Quadrant | Axis | Source | Owner / scope | Status today |
|---|---|---|---|---|
| File | 1 (Designer) | uploaded docs | project + agent allowlist | Exists (§10) |
| Knowledge Map | 1 (Designer) | uploaded docs | project + agent allowlist (mirror File) | **NEW** |
| General | 2 (Context) | chat history | per agent (transcript) | Exists (§9.3) |
| Concept Map | 2 (Context) | chat history | **polymorphic: chatroom / agent_group / workspace** | Exists as 1:1-agent; **re-homed + decoupled** |

### 5.2 Concept Map — polymorphic owner

Replace `graphrag_configs.agent_id UNIQUE` with `owner_kind` + `owner_id`:

```
owner_kind ENUM('chatroom','agent_group','workspace')   -- extensible
owner_id   UUID                                          -- FK target depends on kind
UNIQUE (owner_kind, owner_id)                            -- was: UNIQUE(agent_id)
```

Each owner scope is its own `graphrag_configs` row -> its own `graphrag_config_id` ->
its own isolated Neo4j subgraph (Neo4j already scopes every op by `graphrag_config_id`,
`infrastructure/neo4j_driver.py:69-259`) and its own Qdrant points
(`graphrag_vector_store.py` payload `config_id`). No cross-owner bleed at the storage
layer — isolation is intrinsic to the existing scoping.

**The delta-feed query becomes a strategy keyed on `owner_kind`** — three variants of the
existing join (`app/workers/tasks/graphrag.py:83-97`):

- `chatroom`: `WHERE m.chatroom_id = :owner_id`
- `agent_group`: `WHERE ca.agent_id = ANY(:member_agent_ids)` with `DISTINCT m.id`
  (member set from the new `agent_group_members` junction)
- `workspace`: `WHERE cr.workspace_id = :owner_id`

`agent_id` disappears from `GraphRagConfig`; the domain root gains `owner: OwnerRef`.

### 5.3 `agent_group` — new first-class entity

- Table `agent_groups(id PK, project_id FK -> projects, name, created_at, updated_at)`,
  UNIQUE `(project_id, name)` — mirrors the project-scoping of `rag_configs`.
- Junction `agent_group_members(group_id FK, agent_id FK, PRIMARY KEY(group_id, agent_id))`
  — mirrors `chatroom_agents` (`conversation/infrastructure/tables.py:50-70`).
- Owned by a new bounded context boundary decision (§6): either a small `agents`-context
  sub-aggregate or its own context. Recommendation in §6.
- A `concept_map_enabled` flag on the group gates the wide-layer opt-in (§5.6).

### 5.4 Layered retrieval

At turn time, resolve **the set of Concept Maps that cover this agent in this room**:

1. the room's map (`owner_kind=chatroom`, `owner_id=current chatroom`), if it exists;
2. every enabled `agent_group` map whose membership includes this agent;
3. the workspace map (`owner_kind=workspace`, `owner_id=room.workspace_id`), if enabled.

`GraphRagContextProvider.query` changes from a single `graphrag_config_id` to a
**prioritized list**. Each map is queried (reusing `GraphRagRetrieveService`,
`application/graphrag_retrieve.py:67-146`); bundles are merged under the existing 2 KB cap
(R11.06) with **narrow-scope precedence** (room > group > workspace) and entity dedup by
name. The merge is fail-soft per layer: one layer erroring drops only that layer.

### 5.5 Knowledge Map — GraphRAG over files (Axis 1)

A second, independent subsystem sharing the low-level adapters (Q-5):

- Config attaches like file-RAG: project-scoped, agent allowlist (mirror
  `RagConfig`/`RagDocument.agent_ids`, `domain/models.py:113-148`).
- Source = the same uploaded documents as file-RAG; the ingestion pipeline
  (`ingest_service.py`) feeds parsed document text into the **shared triple extractor**
  (`infrastructure/triple_extractor.py`) instead of the conversation delta loader.
- Storage: its own Neo4j subgraph + Qdrant collection, scoped by its own config id via the
  shared `Neo4jAsyncDriver` / `GraphRagVectorStore` adapters.
- Retrieval: a Knowledge Map block in `turn_engine`, parallel to the File RAG block,
  read-only, fail-soft.
- Builder billing uses the designer/project key (Axis-1 knowledge is authored, not the
  runtime builder-key-group split of Axis 2).

### 5.6 Privacy model (default strict)

- `chatroom` layer: enabled by default (a room's memory of its own conversation — no
  cross-room aggregation, matches today's tenant boundary).
- `agent_group` and `workspace` layers: **disabled by default**; require an explicit
  `concept_map_enabled` flag on the owner entity, settable only by Project Owner.
- Wide layers aggregate content across many rooms; enabling one is an explicit, audited
  decision. Retrieval must never fold a wide layer an agent's room is not authorized to
  see. See §8.

### Options considered

**Option A — Single engine, two kinds (kind discriminator on one config table).** One
`graphrag_configs` table with `kind ∈ {knowledge_map, concept_map}`; one builder, one
retrieval provider parameterized by kind. Least duplication; but couples two products with
divergent lifecycles/UIs into one aggregate, and the shared config table accumulates
kind-conditional columns. **Rejected per Q-3.**

**Option B — Two subsystems, shared low-level adapters (chosen).** Separate domain
aggregates, config tables, services, and UI for Knowledge Map vs Concept Map; both depend
on shared `infrastructure` adapters (Neo4j driver, Qdrant store, triple extractor, 2PC
runner). Clean product boundaries and independent evolution; costs one more config table
and some parallel application-service scaffolding. **Chosen (Q-3, Q-5).**

**Option C — Two fully independent stacks (duplicate Neo4j/Qdrant/2PC).** Cleanest
conceptual separation, but duplicates the most complex and error-prone code in the
subsystem (2PC + compensation + reconciler). **Rejected** — maintenance and drift risk
(the embed-model map is already duplicated between
`graphrag_context_provider.py:29-33` and `graphrag.py:30-34`; more duplication compounds
that).

For the Concept Map owner: **workspace-only** was considered (zero new junction, tightest
existing boundary) but rejected per Q-4 — the user wants explicit, user-composed groups,
not a workspace-implicit boundary. `workflow.id` ownership was rejected because workflows
do not own the chatrooms where agents actually chat (refs are loose UUIDs in JSONB,
`linter.py:111-120`), yielding a drifting derived membership.

### Decision

Adopt the two-axis model (§5.1). Concept Map gets a **polymorphic owner**
(chatroom/agent_group/workspace, §5.2) with `agent_group` as a new first-class entity
(§5.3) and **layered, narrow-precedence retrieval** (§5.4). Knowledge Map is a **separate
Axis-1 subsystem** over files, sharing low-level adapters (§5.5). Privacy is
**default-strict**: only the chatroom layer is on by default (§5.6). What is consciously
given up: a single unified graph config (Option A's simplicity) in exchange for clean
product boundaries, and immediate delivery of everything in exchange for phased,
independently-reviewable shipments.

## 6. Detailed Changes

SoC note: the graph **engine** (extractor, Neo4j/Qdrant adapters, 2PC runner, retrieval)
stays in `contexts/knowledge/infrastructure` + `application`, reused by both subsystems.
`conversation` remains the source of messages (via facade, never imported by `knowledge`).

- **Backend — `knowledge` context**
  - `graphrag_configs`: drop `agent_id UNIQUE`; add `owner_kind` + `owner_id`, UNIQUE
    `(owner_kind, owner_id)`. Migration required: yes (reversibility in §10). Domain
    `GraphRagConfig` gains `owner: OwnerRef`, drops `agent_id`
    (`domain/graphrag.py:30-42`).
  - Delta loader -> strategy per `owner_kind` (`app/workers/tasks/graphrag.py:44-107`).
  - `GraphRagContextProvider.query` -> accept a prioritized list of config ids; add a
    bundle-merge with narrow precedence + dedup + 2 KB cap
    (`application/graphrag_context_provider.py`).
  - New `KnowledgeMapConfig` aggregate + `knowledge_map_configs` table + builder path that
    feeds parsed document text into the shared `triple_extractor`; new config service and
    facade methods on `KnowledgeFacade` (`interfaces/facade.py`).
  - New facade method to resolve "Concept Maps covering (agent, chatroom)" for the runtime.
- **Backend — new `agent_group`**
  - `agent_groups` + `agent_group_members` tables; `concept_map_enabled` flag on group and
    on workspace. Facade for CRUD + membership. Owner-context decision:
    **recommend a dedicated `agent_group` sub-module in the `agents` context** (it is an
    agent-composition concept, project-scoped like agents) rather than a new top-level
    context — keeps `chatroom_agents`-style membership near agents.
- **Backend — `agents` context**
  - Remove the single-valued `agents.graphrag_config_id` reverse pointer
    (`tables.py:50`); the agent no longer owns a Concept Map. Keep `rag_config_id` and add
    the Knowledge Map attachment (project-scoped + allowlist, mirroring RAG).
  - Update `_assert_graphrag_config_compatible`
    (`application/agent_service.py:227-259`) — the builder-vs-consumer key-group rule moves
    to the Concept Map owner, not the agent.
- **Backend — runtime**
  - `turn_engine` assembly (`turn_engine.py:889-983`): replace the single GraphRAG block
    with layered Concept Map retrieval; add a Knowledge Map block beside File RAG. Trigger
    fan-out (`turn_engine.py:1272-1281`) enqueues builds for every Concept Map covering the
    replying agent.
- **API contract** — `gen:api` rerun required: yes.
  - New/changed: Concept Map config CRUD keyed by owner (was per-agent `graphrag.py`
    routes); `agent_groups` CRUD + membership; Knowledge Map config + document routes
    (mirror `rag.py`/`tus.py`); admin reset generalized to owner
    (`R11a.02`, `POST /api/admin/graphrag/{id}/reset`). WS channels for build status keyed
    by config id (reuse `ws/graphrag.py`).
- **Frontend — `agents` slice**
  - Split the agent Knowledge tab: Axis-1 (File + Knowledge Map) stays in the agent editor;
    Concept Map moves to owner-centric surfaces (a chatroom memory panel, an agent_group
    editor, a workspace settings panel). New `agent_groups` management UI. Reuse
    `GraphragGraphView.vue` visualizer for any owner's Concept Map and for Knowledge Maps.
    All strings via `$t()`.
- **Deploy/config** — no new stores; Neo4j/Qdrant/MinIO/Redis unchanged. Qdrant collection
  naming for Knowledge Map (e.g. `knowmap_{project_id}`) added to bootstrap
  (`smap/bootstrap/qdrant_init.py`).

## 7. NFR Checklist

- [ ] i18n — all new owner-panel and agent_group strings via `$t()`; no hardcoded text.
- [ ] Audit log — record: enabling a wide Concept Map layer (group/workspace),
  agent_group membership changes, admin reset. Reuse existing audit event patterns.
- [ ] Tenant isolation — every new endpoint verifies project membership; owner_id must
  resolve inside the caller's project; no owner ever spans projects (§8).
- [ ] Error handling UX — build status/loading/empty states already exist for GraphRAG
  (`useGraphragSocket`); extend to Knowledge Map and per-owner Concept Maps; RFC 7807 codes
  via `knowledge/interfaces/error_mapping.py`.
- [ ] Performance — layered retrieval issues up to N graph queries per turn; today the
  provider builds/tears down Neo4j+Qdrant clients per query
  (`graphrag_context_provider.py:137-156`) with no pooling — N-fan-out makes pooling a
  prerequisite, not optional. Build fan-out multiplies worker load; cap and dedup builds
  per message. Delta-feed queries need indexes on the new join predicates.

## 8. Security Considerations

Touches tenant boundaries, provider keys, and user-input processing — required.

- **Cross-scope memory leakage (primary risk).** A workspace/group Concept Map aggregates
  many rooms' content; retrieval could surface room B's concepts to a user in room A.
  Mitigations: (1) wide layers **default-disabled**, Project-Owner-gated, audited (§5.6);
  (2) retrieval resolves coverage strictly from membership/scope — an agent gets a group
  layer **only** if it is a member and the group has `concept_map_enabled`; (3) no layer
  crosses projects (owner_id validated in-project on every read/build).
- **Builder key-group billing.** Axis-2 preserves the builder-vs-consumer key split
  (R11.01); who pays for group/workspace builds must be an explicit owner attribute, not
  silently the replying agent's key.
- **Knowledge Map ingestion** inherits file-RAG's upload surface (MIME/size gate, SHA-256
  dedup, virus scan, `ingest_service.py:115-423`) — reuse it; do not open a second
  unvalidated upload path.
- **AuthZ on new endpoints** — agent_group CRUD, membership, and owner-scoped Concept Map
  config each verify org/project membership before returning data (hard rule).
- No provider keys logged; evidence excerpts already avoid leaking raw keys.

## 9. Quality Notes

- **Existing debt (do not imitate; record, do not silently fix):**
  - Embed-model map + `_resolve_embed_key` duplicated between
    `graphrag_context_provider.py:29-33,158-184` and `app/workers/tasks/graphrag.py:30-34,110-133`
    (deliberate, to keep contexts free of `app` imports, but a drift risk) — FU candidate.
  - GraphRAG retrieval builds/tears down Neo4j+Qdrant clients per query
    (`graphrag_context_provider.py:137-156`) — no pooling; §7 makes this blocking for
    layered retrieval.
  - 2PC builder spans PG+Neo4j+Qdrant+Redis with heavy compensation
    (`graphrag_builder.py`, `graphrag_reconciler.py`) — the audit
    (`docs/audits/graphrag-neo4j-audit.md`) already flags non-determinism; do not extend it
    blindly.
- **Patterns to follow:**
  - File-RAG many-agent scoping is the template for decoupling: project-scoped config +
    `agent_ids` allowlist (`domain/models.py:113-148`).
  - Membership junction: `chatroom_agents` (`conversation/infrastructure/tables.py:50-70`)
    is the exemplar for `agent_group_members`.
  - Cross-context read without import: `EvidenceFetcher` injection
    (`graphrag_context_provider.py:203-224`) — reuse for any conversation reads.
  - Facade-only cross-context access (`knowledge/interfaces/facade.py`); never import
    `app.*` from a context.
- **Reuse inventory:** `Neo4jAsyncDriver` (`infrastructure/neo4j_driver.py`),
  `GraphRagVectorStore` (`infrastructure/graphrag_vector_store.py`), `LlmTripleExtractor`
  (`infrastructure/triple_extractor.py`), 2PC runner (`application/graphrag_builder.py`),
  `GraphRagRetrieveService` (`application/graphrag_retrieve.py`), `RedisLock`
  (`infrastructure/redis_lock.py`), `GraphragGraphView.vue` visualizer, `useGraphragSocket`
  / `useRagConfigSocket` composables, RAG ingestion pipeline (`ingest_service.py`),
  tus finalizer (`application/rag_tus_finalizer.py`).

## 10. Risks and Rollback

- **Migration (`graphrag_configs.agent_id` -> `owner_kind`/`owner_id`).** Reversible:
  existing rows migrate to `owner_kind='chatroom'` is wrong (today's scope is one agent's
  rooms, not one room). Correct forward-migration: for each existing config, create an
  `agent_group` of one member (that agent) OR keep behavior by seeding an
  `owner_kind='agent_group'` singleton. Down-migration restores `agent_id` from the
  singleton group. This mapping is a decision to confirm at build time (Open Q-A).
- **Layered retrieval regressions.** Merge/budget bugs could crowd out the room layer with
  workspace noise. Mitigation: narrow-precedence + per-layer fail-soft + tests on the merge
  policy.
- **Build fan-out load.** One message feeding 3 layers triples build volume. Mitigation:
  per-message build dedup, existing per-config Redis lock (R11a.01), trigger thresholds.
- **Staging data.** Existing per-agent GraphRAG configs on `smap.rcsl.online` must migrate
  without orphaning Neo4j subgraphs (scoped by `graphrag_config_id`, which is preserved).
- Rollback path per phase: each phase ships behind the existing fail-soft retrieval, so a
  broken layer degrades to no-context rather than a failed turn.

## 11. Acceptance Criteria

- [ ] AC-1: `graphrag_configs` has no `agent_id`; it has `owner_kind` + `owner_id` with
  UNIQUE `(owner_kind, owner_id)`; `agents.graphrag_config_id` is removed. (migration test)
- [ ] AC-2: A Concept Map can be created with `owner_kind ∈ {chatroom, agent_group,
  workspace}` and only builds from that scope's messages (delta-loader strategy test per
  kind).
- [ ] AC-3: `agent_group` + `agent_group_members` exist; a group owns a Concept Map;
  membership CRUD verifies project membership. (integration test)
- [ ] AC-4: At turn time, an agent's reply is augmented by the merged bundle of every
  covering Concept Map (room + enabled groups + enabled workspace), narrow-precedence,
  deduped, within 2 KB. (runtime test)
- [ ] AC-5: agent_group and workspace layers are absent from retrieval unless
  `concept_map_enabled` is set on the owner; chatroom layer is present by default.
  (privacy test)
- [ ] AC-6: A user in room A never receives concepts sourced solely from room B via a wide
  layer the agent is not authorized for. (security test)
- [ ] AC-7: A Knowledge Map config can be built from uploaded files (shared ingestion +
  shared triple extractor) and queried at turn time as an Axis-1 block, independent of any
  Concept Map. (integration test)
- [ ] AC-8: Knowledge Map and Concept Map share the Neo4j/Qdrant/extractor adapters (no
  duplicated adapter code) while having separate config tables/services/UI. (structure
  review)
- [ ] AC-9: Enabling a wide layer, membership changes, and admin reset are audit-logged.
- [ ] AC-10: `pnpm typecheck && pnpm lint` and `ruff/mypy/pytest` pass; `gen:api`
  regenerated; no hardcoded user-facing strings.

## 12. Test Plan

- Migration: unit test the up/down of the `owner_kind` change and the existing-config
  mapping (AC-1, R10-scope in §10).
- Delta-loader strategies: unit tests per `owner_kind` on the message-enumeration query
  (AC-2), extending patterns near `tests/` for `_DbDeltaLoader`.
- agent_group: integration tests for CRUD + membership AuthZ (AC-3).
- Layered retrieval merge: unit tests on precedence/dedup/budget; runtime integration test
  through `TurnEngine` (AC-4, AC-5).
- Security: cross-room leakage test with a disabled vs enabled workspace layer (AC-6).
- Knowledge Map: integration test file -> extractor -> Neo4j/Qdrant -> turn block (AC-7);
  structure/lint check for shared-adapter reuse (AC-8).
- Audit: assert events emitted (AC-9). CI gates (AC-10).

## 13. SRS Delta

To apply verbatim to `REQUIREMENTS.md` on approval. Amends §11 and adds an Axis-1
Knowledge Map subsection; the two-axis framing supersedes the implicit single-GraphRAG
model.

Amend:

- **[R11.05]** A Concept Map (conversation-derived Graph RAG) is owned by exactly one
  **owner** identified by `(owner_kind, owner_id)` where `owner_kind ∈ {chatroom,
  agent_group, workspace}`. The prior 1:1 Agent binding is removed. `UNIQUE(owner_kind,
  owner_id)`.

Add (§11.4 Concept Map ownership and layering):

- **[R11.07]** `agent_group` is a first-class, project-scoped entity with a member set of
  Agents (`agent_group_members`). It may own a Concept Map.
- **[R11.08]** The Concept Map delta feed is scoped by owner: `chatroom` -> that room's
  messages; `agent_group` -> the union of member agents' room messages; `workspace` ->
  that workspace's room messages.
- **[R11.09]** At agent invocation, retrieval draws on **every Concept Map covering the
  agent in the current room** (its chatroom map, each enabled agent_group map it belongs
  to, and the enabled workspace map), merged under the 2 KB cap with narrow-scope
  precedence (chatroom > agent_group > workspace) and entity dedup.
- **[R11.10]** Privacy default-strict: the chatroom Concept Map layer is enabled by
  default; agent_group and workspace layers require an explicit `concept_map_enabled`
  flag on the owner, settable only by Project Owner, and their enablement is audit-logged.
  No Concept Map ever spans projects.
- **[R11.11]** The Concept Map builder key group is an attribute of the owner (not the
  replying agent), preserving the builder-vs-consumer billing split of [R11.01].

Add (§11.5 Knowledge Map — Graph RAG over files, Axis 1):

- **[R11.12]** A Knowledge Map is a designer-authored Graph RAG built from **uploaded
  documents** (the same sources as §10 file-RAG), not from conversation. It is
  project-scoped with a per-Agent allowlist, mirroring [R10.11].
- **[R11.13]** Knowledge Map ingestion reuses the RAG ingestion pipeline (§10.1) to parse
  documents, then extracts triples via the shared extractor and persists to its own Neo4j
  subgraph + Qdrant collection, scoped by its config id.
- **[R11.14]** At agent invocation, an attached Knowledge Map is queried as an Axis-1
  system block (`type:"graphrag"` retained) beside file-RAG, independent of any Concept
  Map.
- **[R11.15]** Knowledge Map and Concept Map are distinct subsystems (separate config,
  services, and UI) that share the low-level graph adapters (Neo4j driver, Qdrant store,
  triple extractor, 2PC runner).

## 14. Open Questions

- Open Q-A: Existing-config migration mapping — seed a singleton `agent_group` per current
  agent config, or map to that agent's current-room `chatroom` maps? Decide at the
  decouple phase (§10).
- Open Q-B: Neo4j/Qdrant client pooling for N-fan-out retrieval — introduce in the layered
  phase; scope of the pooling change to confirm.
- Open Q-C: `agent_group` home — sub-module of `agents` context (recommended) vs a new
  top-level context. Confirm at build.
- Open Q-D: Does the Knowledge Map warrant its own retrieval tag distinct from
  `type:"graphrag"` for UI differentiation? (cosmetic, non-blocking)

## Phasing (recommendation — order to confirm per Q-9)

1. **Phase 1 — Decouple (refactor).** `agent_id` -> polymorphic owner; ship the
   `chatroom` layer only (behavior-equivalent-ish, narrowest privacy). Validates the model
   end-to-end at lowest risk. AC-1, AC-2 (chatroom), migration.
2. **Phase 2 — agent_group + layered retrieval (feature).** New entity, membership,
   `agent_group` + `workspace` layers, merge/precedence, privacy gating, pooling.
   AC-3, AC-4, AC-5, AC-6, AC-9.
3. **Phase 3 — Knowledge Map (feature).** Axis-1 file->graph subsystem on shared adapters.
   AC-7, AC-8.
4. **Phase 4 — Frontend re-home (feature).** Split Knowledge tab; owner-centric Concept Map
   panels; agent_group UI; reuse visualizer. AC-10 UI parts.

Each phase is a separate `/spec`-refined `/build` task linking back to this blueprint.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: De-duplicate the embed-model map / `_resolve_embed_key` shared between
  `graphrag_context_provider.py` and `app/workers/tasks/graphrag.py` without violating the
  no-`app`-import rule (extract to a shared `knowledge` helper).
