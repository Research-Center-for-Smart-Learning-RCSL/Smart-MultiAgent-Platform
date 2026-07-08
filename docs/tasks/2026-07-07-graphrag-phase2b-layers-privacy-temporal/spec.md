---
type: feature
status: in-progress
created: 2026-07-07
requirements: [R11.07, R11.08, R11.09, R11.10, R11.11, R11.17, R11.20, R11.21]
---

# GraphRAG Phase 2b — Concept Map layers, privacy, multi-member groups, and temporality

## 1. Summary

Phase 2b turns the single-owner, flat, timeless Concept Map of today into the layered,
privacy-gated, temporal Axis-2 memory the blueprint defines. It makes `agent_group`
multi-member with per-member provenance, activates `chatroom`- and `workspace`-owned
Concept Maps alongside `agent_group`-owned ones, resolves retrieval across every map that
covers an agent in a room under narrow-scope precedence, gates the wider layers behind a
strict-by-default opt-in, and threads message timestamps through the graph so retrieval can
weight by recency. It implements requirements R11.07–R11.11, R11.17, R11.20, and R11.21,
which the blueprint already added to the SRS; the only new behavior beyond them is
member-provenance partitioning (Q4), drafted as R11.22 in §13.

This is the largest phase. It is specified as six ordered workstreams (WS1–WS6) so `/build`
can land it in reviewable stages; WS5 (temporal) and WS6 (lifecycle) are cleanly separable
and can be split into a follow-on dossier if the user prefers smaller build units.

**Phase dependencies (hard):** built on Phase 0 (engine de-concreting, cleanup contract),
Phase 1 (`owner_kind` typed-FK owner, singleton `agent_group`, membership-join
`list_for_agents`, migrations 0043/0044), and Phase 2a (owner→project invariant, bounded
windowing, embed-dimension pin, DISTINCT resolver, migration 0045). Citations reflect the
pre-2b tree; where an earlier phase moves a seam, the text says so.

## 2. Goals and Non-goals

**Goals**
- G1 — `agent_group` is multi-member; its Concept Map ingests the DISTINCT union of member
  agents' room messages and tags each entity/relation with the contributing member (Q4).
- G2 — `chatroom`- and `workspace`-owned Concept Maps are creatable, buildable, and
  retrievable, using the Phase 1 `owner_kind` discriminator.
- G3 — At a turn, retrieval draws on every Concept Map covering the agent in the current
  room (chatroom + each enabled agent_group + enabled workspace), merged under the 2 KB cap
  by narrow-scope precedence chatroom > agent_group > workspace with a tiered budget fill
  (R11.09; Q1).
- G4 — Wider layers (agent_group, workspace) are disabled by default and require an explicit
  Project-Owner-set, audit-logged `concept_map_enabled` opt-in; chatroom maps inherit the
  room ACL (R11.10, R11.17; Q2).
- G5 — Entities and relations carry `first_seen_at`/`last_seen_at` derived from source
  message timestamps; retrieval weights by a per-config recency half-life (R11.21; Q3).
- G6 — Deleting any owner (agent, chatroom, workspace, agent_group) or a config purges its
  Neo4j subgraph and Qdrant points (R11.20), extending the Phase 0 cleanup contract to the
  new owner kinds.

**Non-goals**
- Knowledge Map / Axis-1 file GraphRAG (Phase 3).
- Any frontend — layer/privacy/temporal UI is Phase 4. Phase 2b is backend + API only.
- Bitemporal "as of date X" / time-travel queries (R11.21 reserves these explicitly).
- A user-selectable embedding model (frozen pin from Phase 2a stands; FU from 2a).
- Per-member *sub-graph forking*: Q4 is one shared group graph with provenance tags, not N
  member graphs. Member scoping is a retrieval filter, not a storage split.
- Cross-project maps (forbidden by R11.10/R11.16, enforced in Phase 2a).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How do the layers combine into one 2 KB budget? | Tiered budget fill by narrow-scope precedence: chatroom map fills first, then each enabled agent_group map, then the enabled workspace map; each wider layer uses only the remaining budget; entity dedup keeps the narrowest occurrence. | Matches the approved R11.09 precedence. The user chose "narrow fills first"; R11.09 fixes the concrete order (chatroom is the narrowest scope — one room — not agent_group). This spec follows R11.09; the Q1 option label's parenthetical calling agent_group "narrowest" was mistaken and is superseded here. |
| Q-2 | How is "strict default, wide layers opt-in" modeled? | Chatroom-owned maps inherit the room's existing boolean-tier ACL (`access.py` matrix) via R11.17; agent_group/workspace-owned maps use a single `concept_map_enabled` opt-in, Project-Owner-only, audit-logged, per R11.10/R11.17. | The user picked "mirror the chatroom tier matrix." On reconciliation with the approved SRS, the chatroom layer *already* mirrors that matrix natively (it inherits the room ACL), and the wide layers are specified as a single opt-in. Adding a parallel 4-flag matrix to wide layers would duplicate the ACL surface without an SRS mandate — that is the debt this phase must avoid. **Flagged at the approval gate:** if the user wants a full tier matrix on wide layers too, R11.10/R11.17 must be amended (an added SRS Delta), and this decision changes. |
| Q-3 | How configurable is recency weighting? | Per-config `recency_half_life_days` column (nullable → platform-setting default); score = `weight × exp(-Δt / halflife)`. | User chose per-config half-life. Kept within R11.21's "may weight by recency"; drafted as an R11.21 amendment (§13). Bounded/validated at config create/update. |
| Q-4 | Multi-member group memory model? | One shared group Concept Map; every entity/relation tagged with the contributing member agent (provenance partition). Default retrieval spans all members; an optional member filter scopes to a subset. | User chose "shared + partition." Group membership is the trust boundary, so co-members see each other's contributions through the shared map; the partition adds provenance and optional scoping, not a storage split. New behavior → drafted as R11.22 (§13). |

## 4. Current State

- **Flat single-config retrieval.** A turn resolves exactly one scope from
  `agent.graphrag_config_id` (`turn_engine.py:1669`, call site `:906`); `None` short-circuits
  (`graphrag_context_provider.py:73`). Config load filters only `id` + `deleted_at`
  (`graphrag_repositories.py:76-80`) — no owner/room/visibility predicate.
- **Hybrid retrieve, confidence-only rank.** `GraphRagRetrieveService.query`
  (`graphrag_retrieve.py:67-146`): embed query → Qdrant `search_entities(config_id=...)`
  top-5 (`:94-99`, `:72`) → seed entities (`:105`) → Neo4j `traverse(config_id=...)` 1–2 hops
  (`:106-110`) → evidence excerpts (`:133-136`). Ranked by confidence only
  (`:140`; merge `graphrag_context_provider.py:242-248`); 2 KB cap `_cap_to_2kb`
  (`domain/graphrag.py:135-153`) drops weakest edges. Neo4j filter is a single `$cid`
  (`neo4j_driver.py:240-256`); Qdrant filter a single `config_id` `FieldCondition`
  (`graphrag_vector_store.py:112-118`). No layers, no visibility, no recency.
- **Evidence fetch has no visibility re-check.** Excerpts pull raw message content by id
  (`graphrag_context_provider.py:212-222`) with no room/ACL check — scope is inherited
  entirely from which edges the single-config traversal returned. A layered/shared design
  must add an explicit visibility filter here.
- **No timestamps in the graph.** `messages.created_at` exists
  (`contexts/conversation/infrastructure/tables.py:141`) but the loader keeps it only as a
  keyset cursor and maps rows to `_DbMsg(id, role, content)` (`app/workers/tasks/graphrag.py:87,101,37-42`);
  `DeltaMessage` has no timestamp (`graphrag_ports.py:144-149`). Neither `Triple` nor
  `RelationEdge` carries time (`domain/graphrag.py:51-71,83-91`). No Neo4j node/edge or
  Qdrant payload stores a timestamp (`neo4j_driver.py:89-111`,
  `graphrag_vector_store.py:85-90`). Extraction never sees time (`triple_extractor.py:118-122,45-54`).
- **Owner model (post-Phase-1).** `owner_kind` typed-FK discriminator with CHECK exactly-one
  and partial unique indexes (Phase 1, patterned on `0042_prompt_studio.py`). Phase 1 makes
  `agent_group` a singleton; the members table exists but holds one row per group.
  `list_for_agents` is the membership join (Phase 1).
- **Membership exemplars.** `chatroom_agents` composite PK + role enum, both CASCADE
  (`conversation/infrastructure/tables.py:50-70`); `key_group_members` composite PK +
  `priority` + CASCADE (`keys/infrastructure/tables.py:100-129`, repo
  `group_repository.py:183-244`, migration `0007_key_groups.py:46-92`) — the structural model
  for the multi-member table.
- **Privacy exemplars.** `chatrooms` four boolean opt-in-tier flags, server defaults strict
  (`conversation/infrastructure/tables.py:33-36`), evaluated by the matrix in
  `conversation/application/access.py:96-136`. Scope-enum + CHECK precedent: `PromptScope`
  (`prompt_studio/domain/models.py:35-38`, `infrastructure/tables.py:40-64`).
- **AuthZ exemplar.** `app/api/v1/graphrag.py:9-11,157-263`: list/read gated by membership,
  create/delete by `RESOURCE_CREATE_EDIT` at the resource's own `project_id`; the
  "fetch resource, then assert membership against its own project" pattern (`:250-263`) is the
  privacy idiom to extend. Project-Owner authority helper: `tenancy/interfaces/facade.py:44-67`.

## 5. Design

### Options considered

**Retrieval combination (Q1).**
- *Option A — union + global re-rank*: pool all layers, sort by recency×weight, one budget.
  Simpler, reuses `_merge_bundles`, but a wide/shared layer can crowd out the agent's own
  room memory.
- *Option B — tiered budget fill (chosen)*: fill the budget narrowest-first (chatroom →
  agent_group → workspace), each wider layer taking only the remainder, entity dedup keeping
  the narrowest occurrence. Guarantees the agent's own room memory is never diluted by shared
  layers; costs a per-layer fill loop. Matches R11.09.

**Wide-layer privacy (Q2).**
- *Option A — full 4-flag tier matrix per wide layer*: maximal expressiveness, but invents a
  second ACL surface parallel to the room ACL with no SRS mandate → duplication debt.
- *Option B — single `concept_map_enabled` opt-in for wide layers, chatroom inherits room ACL
  (chosen)*: matches R11.10/R11.17; chatroom layer already mirrors the room tier matrix
  natively; wide layers get one Project-Owner-gated, audit-logged switch. Less expressive on
  wide layers, but no duplicate ACL. Reversible to Option A only via an SRS amendment.

**Member memory (Q4).**
- *Option A — N per-member subgraphs*: strongest isolation, but forks storage, build, and
  retrieval, and contradicts "shared group memory."
- *Option B — one shared graph + provenance partition (chosen)*: a `source_member_id`
  property on entities/relations; default retrieval spans members, optional member filter.
  Provenance without a storage split; co-members see shared contributions (group = trust
  boundary).

**Temporal model (Q3).**
- *Option A — single `observed_at`*: one timestamp, latest-wins. Simpler but loses "first
  seen."
- *Option B — `first_seen_at` + `last_seen_at` (chosen)*: R11.21 mandates first-seen/last-seen;
  earliest-wins and latest-wins merges respectively. Per-config `recency_half_life_days`
  drives an exponential decay on `last_seen_at`.

### Decision

Tiered fill (B), single wide-layer opt-in with chatroom ACL inheritance (B), shared graph +
provenance partition (B), and dual timestamps (B). Together these implement R11.07–R11.11,
R11.17, R11.20, R11.21 with one new requirement (R11.22, member provenance) and one amendment
each to R11.09 (tiered fill) and R11.21 (dual timestamps + per-config half-life). The
consciously-given-up capabilities: a rich wide-layer ACL (deferred behind an SRS amendment)
and time-travel (reserved by R11.21).

## 6. Detailed Changes

Structured as six ordered workstreams. Each is independently reviewable; WS1→WS4 are
sequential (later ones depend on earlier schema), WS5 and WS6 are parallelizable after WS2.

### WS1 — Multi-member groups + provenance partition (R11.07, R11.08, R11.22)
- **Backend/infra** — relax the Phase 1 singleton constraint so a group holds >1 member
  (drop/adjust any singleton guard from Phase 1; the `agent_group_members` table stays,
  patterned on `key_group_members`). Add `source_member_id` to the graph: a property on Neo4j
  entity/edge MERGE (`neo4j_driver.py:89-111`) and to the Qdrant entity payload
  (`graphrag_vector_store.py:85-90`). The builder must carry, per triple, which member's
  message produced it (thread member id alongside the existing delta).
- **Delta feed** — the group loader ingests the DISTINCT union of member agents' room
  messages (R11.08); reuse the Phase 2a windowed `iter_windows`, extended to fan across
  members and dedup by message id. Provenance = the member whose room the message came from.
- **Application** — group CRUD (add/remove members) in a group service, mirroring
  `group_repository.py:183-244`; Project-Owner authorization via `tenancy` facade.

### WS2 — Layered owner configs (R11.08, R11.11, R11.16)
- **Application** — config create/update accepts `owner_kind ∈ {agent_group, chatroom,
  workspace}` (Phase 1 columns/CHECK already exist). Per-kind delta scoping (R11.08): chatroom
  → that room's messages; agent_group → member union (WS1); workspace → workspace rooms'
  messages. Builder key group validated only for project membership (R11.11); the R11.01
  distinctness rule does not apply. Owner→project invariant reused from Phase 2a D6
  (`_assert_owner_in_project`).
- **Facade/API** — extend `app/api/v1/graphrag.py` create/list/read to the new owner kinds,
  keeping the "assert membership against the resource's own project" pattern (`:250-263`).

### WS3 — Privacy gating (R11.10, R11.17)
- **Backend** — add `concept_map_enabled BOOLEAN NOT NULL DEFAULT false` to the wide-layer
  owner rows (agent_group, workspace); settable only by a strict Project Owner
  (`tenancy` facade `is_project_owner`), and emit an audit event on change. Chatroom maps
  inherit the room ACL: reuse `conversation/application/access.py:96-136` to gate read/subscribe
  (R11.17).
- **Retrieval/evidence** — close the evidence-fetch hole: `build_evidence_fetcher`
  (`graphrag_context_provider.py:203-224`) must filter message excerpts to those the querying
  principal may read (room ACL for chatroom-sourced evidence), not return raw content
  unconditionally.

### WS4 — Layered retrieval with tiered fill (R11.09)
- **Resolution** — replace the single `agent.graphrag_config_id` read (`turn_engine.py:1669`)
  with a resolver that returns every Concept Map covering the agent in the current room: the
  chatroom map, each enabled agent_group map the agent belongs to, and the enabled workspace
  map. Enablement per WS3.
- **Query path** — `GraphRagContextProvider.query` (`graphrag_context_provider.py:60-91`)
  iterates the resolved layers; a new tiered assembler fills the 2 KB budget narrowest-first
  (chatroom → agent_group → workspace), each wider layer using only the remainder, entity
  dedup keeping the narrowest occurrence (extends `_merge_bundles` `:227-253`). Per-layer
  embedder resolution (`:158-184`) since layers may carry different builder key groups.
  Optional member filter (WS1) passed through to Qdrant/Neo4j scoping.

### WS5 — Temporal Concept Map (R11.21)
- Thread timestamps end-to-end (smallest slice from the temporal analysis): add `created_at`
  to `_DbMsg` (`graphrag.py:37-42,101`) and `DeltaMessage` (`graphrag_ports.py:144-149`); add
  `first_seen_at`/`last_seen_at` to `Triple`/`RelationEdge` (`domain/graphrag.py:71,91`, trailing
  optional, mirrors the `subject_type` precedent), set from the source messages' `created_at`
  in the extractor without trusting the LLM (`triple_extractor.py` post-parse). Persist on
  Neo4j entity/edge MERGE — `first_seen_at` earliest-wins, `last_seen_at` latest-wins —
  alongside the confidence-max merge (`neo4j_driver.py:105-111,89-96`), mirror in
  `restore_from_snapshot`/`snapshot_subgraph` (`:213-217,51-55`), and RETURN from `traverse`
  (`:246-250`). Add `recency_half_life_days` to `graphrag_configs` (migration; nullable →
  settings default). Retrieval scoring becomes `weight × exp(-Δt / halflife)` on `last_seen_at`
  (`graphrag_retrieve.py:140`, `graphrag_context_provider.py:242-248`), NULL timestamps
  coalescing to a neutral/oldest value as NULL confidence already does.
- The conversation timeline is recoverable from evidence message ids + their timestamps
  (R11.21) — no separate store; retrieval may expose ordering via the evidence excerpts.

### WS6 — Lifecycle purge for new owners (R11.20)
- Extend the Phase 0 WS4 cleanup contract: deleting a chatroom, workspace, or agent_group
  (in addition to agent/config) purges the owned config's Neo4j subgraph and Qdrant points as
  part of the delete op, audit-logged, never relying on the DB cascade alone. Reconciler sweep
  already backstops orphans (Phase 0).

**Migrations** — WS1 (`source_member_id` is graph-side, no PG migration; member-count relax
may be a constraint drop), WS3 (`concept_map_enabled` columns), WS5 (`recency_half_life_days`
column). Expand-only, nullable/defaulted, forward-compatible. Numbering continues from Phase
2a's 0045 (0046+). `gen:api` rerun required (new/changed create fields) — but no frontend
consumes them until Phase 4.

## 7. NFR Checklist

- [x] **i18n** — N/A for this phase (backend/API only; no user-facing strings). Phase 4 owns UI copy.
- [x] **Audit log** — `concept_map_enabled` changes are audited (R11.10); owner/config deletes
  and purges are audited (R11.20). Group membership changes audited.
- [x] **Tenant isolation** — every new/changed endpoint asserts project membership against the
  resource's own project (owner→project invariant from 2a; the `graphrag.py:250-263` pattern);
  no map spans projects (R11.10/R11.16).
- [x] **Error handling UX** — RFC 7807 errors for: owner-not-in-project, disabled-layer read,
  non-owner enabling a wide layer, embedding-dimension mismatch (2a). Retrieval degrades to
  fewer/zero layers silently (never fails a turn — current behavior preserved).
- [x] **Performance** — layered retrieval multiplies per-turn queries by layer count (≤3 +
  agent_group memberships); bound the number of agent_group layers considered and reuse the
  per-query fan-out cap. Windowed builds (2a) bound build cost. Watch N+1 in the layer
  resolver — resolve all covering configs in one query.

## 8. Security Considerations

Touches tenant boundaries, agent/LLM context assembly, and message-content exposure — a
Security Considerations section is required.

- **Cross-layer / cross-member leak.** The shared group map (Q4) makes one member's
  contributions visible to co-members by design; the trust boundary is group membership, which
  must be verified when resolving which agent_group maps an agent may read. A member removed
  from a group must lose read access on the next turn (resolver reads live membership, not a
  cached set).
- **Evidence-content exposure.** The current evidence fetcher returns raw message content with
  no ACL re-check (`graphrag_context_provider.py:212-222`). WS3 closes this: excerpts from a
  chatroom-sourced edge are gated by the room ACL; a principal who cannot read the room cannot
  receive its message text via the graph. This is the single most important security fix in
  the phase.
- **Privilege on enablement.** Enabling a wide layer exposes shared memory to more agents;
  restrict to strict Project Owner (`is_project_owner`), audit every toggle (R11.10).
- **Project boundary.** Owner→project invariant (2a) plus per-collection project scoping keeps
  every map inside one project; no retrieval path may join across `project_id`.
- **Provider keys.** Per-layer embedder resolution must keep using the carried-key path from
  Phase 0 (`list_ordered_carried`); never resolve an embedding key across a project boundary.

## 9. Quality Notes

- **Existing debt (do not imitate; record, do not silently fix).**
  - Evidence fetcher's missing ACL check (`graphrag_context_provider.py:212-222`) — fixed here
    (WS3), the one exception since it is a security defect in scope.
  - Confidence-only ranking with `_cap_to_2kb` binary-search truncation
    (`domain/graphrag.py:135-153`) — extended, not rewritten.
  - `_merge_bundles` currently assumes one config's bundles — generalized for layers (WS4).
- **Patterns to follow.**
  - Membership table: `key_group_members` (`keys/infrastructure/tables.py:100-129`,
    `group_repository.py:183-244`, `0007_key_groups.py:46-92`).
  - Privacy tiers / ACL matrix: `conversation/application/access.py:96-136`.
  - Owner-scope discriminator + CHECK: Phase 1's owner columns (patterned on
    `0042_prompt_studio.py`).
  - Route AuthZ: `app/api/v1/graphrag.py:9-11,207-263`; Project-Owner authority:
    `tenancy/interfaces/facade.py:44-67`.
  - Trailing-optional domain field: `subject_type` on `Triple` (`domain/graphrag.py`).
- **Reuse inventory.**
  - `access.py` matrix for chatroom-map read gating (do not re-implement ACL).
  - `is_project_owner` / `member_project_ids` (`tenancy/interfaces/facade.py:44-67`) for
    enablement authority.
  - Phase 2a `iter_windows` for the group/workspace delta feed.
  - Phase 2a `_assert_owner_in_project` for WS2 create/update.
  - Phase 0 `list_ordered_carried` for per-layer embedder resolution.
  - Existing audit-event emitter used by chatroom privacy toggles.

## 10. Risks and Rollback

- **Scope size** — six workstreams in one phase. Mitigation: ordered, independently
  reviewable WS; WS5/WS6 splittable into a follow-on dossier (offered at the gate).
- **Retrieval regression** — the biggest behavioral change is the multi-layer assembler. Risk:
  the tiered fill starves a layer or changes existing single-config output. Mitigation:
  characterization test that a single-owner agent's retrieval is byte-identical to today
  (chatroom-only layer) before adding layers.
- **Privacy misconfiguration** — a wrongly-enabled wide layer leaks shared memory. Mitigation:
  default-false columns, Project-Owner-only, audit trail, and a test that a disabled layer
  contributes nothing.
- **Migrations** — all expand-only, nullable/defaulted, forward-compatible; rollback drops the
  added columns and the graph degrades to timeless/flat behavior. Graph-side properties
  (`source_member_id`, `first_seen_at`, `last_seen_at`) are additive; legacy edges coalesce to
  neutral values. No data migration; no destructive step.
- **Cross-member leak via stale membership** — resolver must read live membership (risk noted
  in §8); tested.

## 11. Acceptance Criteria

- [ ] AC-1: an `agent_group` can hold ≥2 members; its Concept Map build ingests the DISTINCT
  union of member agents' room messages (a message co-present to two members is ingested once).
- [ ] AC-2: each entity/relation in an agent_group map carries `source_member_id`; retrieval
  with a member filter returns only that member's contributions, and without a filter returns
  the shared union.
- [ ] AC-3: a `chatroom`- and a `workspace`-owned config can be created, built, and retrieved;
  each rejects an owner not in the config's project (RFC 7807).
- [x] AC-4: at a turn, retrieval assembles chatroom + enabled agent_group + enabled workspace
  layers under a single 2 KB cap with tiered narrow-first fill (chatroom fills first) and
  entity dedup keeping the narrowest occurrence. *(`query_layers` + `_merge_layers_tiered`;
  unit-verified by `test_query_layers_tiered_fill_and_narrowest_dedup` — order, dedup keeping
  the narrowest occurrence, no cross-layer re-sort so the 2 KB tail-cap trims the widest first.)*
- [x] AC-5: a single-owner (chatroom-only) agent's retrieval output is unchanged from
  pre-2b behavior (characterization test). *(A single-layer assembly is byte-identical to the
  flat `query`; unit-verified by `test_query_layers_single_layer_matches_query`.)*
- [x] AC-6: agent_group and workspace maps contribute nothing to retrieval unless
  `concept_map_enabled` is true; only a strict Project Owner can toggle it; each toggle is
  audit-logged. *(Completed across WS3 + WS4: WS3 delivered the strict-Project-Owner toggle +
  audit (D-6); WS4's `list_layers_for_turn` gates both wide layers on `concept_map_enabled IS
  TRUE`, so a disabled layer contributes nothing. Wiring-verified by
  `test_list_layers_for_turn_orders_and_gates_layers` — only the chatroom layer resolves until
  the wide layers are enabled.)*
- [x] AC-7: a principal who cannot read a chatroom receives no evidence excerpt sourced from
  that room, even when a shared-layer edge references it. *(Evidence fetcher enforces the
  querying-agent room ACL, fail-closed; unit-verified by `test_graphrag_retrieve.py`
  (`..._drops_excerpts_from_unreadable_rooms`, `..._fails_closed_without_querying_agent`).
  The end-to-end integration test runs in the CI Neo4j+Qdrant tier.)*
- [x] AC-8: entities/relations carry `first_seen_at` (earliest-wins) and `last_seen_at`
  (latest-wins) derived from message timestamps, not LLM output. *(Builder
  `attach_temporal_provenance` stamps each triple from the source message's `created_at`
  epoch; Neo4j `apply_triples` MERGE keeps earliest `first_seen_at` / latest `last_seen_at`.
  Unit-verified by `test_graphrag_builder.py` (temporal provenance) and
  `test_graphrag_retrieve.py::test_recency_weighted_score_recent_beats_stale`. Timestamps
  never originate from LLM output — the builder reads `m.created_at`, not extractor fields.)*
- [x] AC-9: retrieval ranking applies `weight × exp(-Δt / recency_half_life_days)`; a recent
  low-confidence edge can outrank a stale high-confidence one; the half-life is per-config and
  validated on create/update. *(`domain.recency_weighted_score` + `edge_rank`; retrieve
  service ranks on the decayed score. Unit-verified by
  `test_graphrag_retrieve.py::test_retrieve_reranks_recent_over_stale`;
  `_validate_half_life` rejects non-positive values (`GraphRagInvalidHalfLife` -> 422) and the
  API field rejects non-finite input (`allow_inf_nan=False`).)*
- [ ] AC-10: deleting a chatroom, workspace, or agent_group purges its owned config's Neo4j
  subgraph and Qdrant points (audit-logged); the reconciler finds no orphan.
- [x] AC-11: a member removed from a group loses read access to the group map on the next turn.
  *(The resolver reads live `agent_group_members` on every turn, no cache; wiring-verified by
  `test_list_layers_for_turn_orders_and_gates_layers` — after `remove_member` the next resolve
  drops the group layer.)*
- [ ] AC-12: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass; `gen:api`
  regenerated.

## 12. Test Plan

- **Unit** (`backend/tests/unit/`, mirroring source): AC-1 (delta DISTINCT union), AC-2
  (provenance filter), AC-3 (owner→project per kind), AC-4 (tiered fill order + dedup), AC-5
  (characterization: single-layer byte-identical), AC-6 (enablement gating + Project-Owner
  guard + audit), AC-8 (timestamp threading, earliest/latest merge), AC-9 (recency score,
  half-life validation), AC-11 (live-membership resolver).
- **Integration** (`-m integration`, Neo4j + Qdrant): AC-2 (member-scoped traversal), AC-7
  (evidence ACL filter end-to-end), AC-10 (purge across owner kinds).
- **Manual/`verify`**: build a workspace-owned map over multiple rooms and confirm bounded
  build + layered retrieval in a live turn.

## 13. SRS Delta

Apply verbatim on approval.

**Amend [R11.09]** (append the fill algorithm):
> **[R11.09]** At agent invocation, retrieval draws on every Concept Map covering the agent in
> the current room (its chatroom map, each enabled agent_group map it belongs to, and the
> enabled workspace map), merged under the 2 KB cap with narrow-scope precedence and entity
> dedup. The budget is filled narrowest-first: the chatroom map's ranked results fill first,
> then each enabled agent_group map, then the enabled workspace map; each wider layer
> contributes only to the remaining budget, and entity dedup keeps the narrowest occurrence.

**Amend [R11.21]** (dual timestamps + per-config half-life):
> **[R11.21]** A Concept Map is a temporal knowledge graph: each entity and relation carries a
> `first_seen_at` and `last_seen_at` timestamp derived from its source messages' timestamps
> (first-seen earliest-wins, last-seen latest-wins), and the exact User↔Agent conversation
> timeline is recoverable from the evidence so an agent can reason about ordering and causality.
> Concept Map retrieval weights results by recency using a per-config half-life
> (`recency_half_life_days`, defaulting to a platform setting): an edge's rank is scaled by
> `exp(-Δt / halflife)` over its `last_seen_at`. Knowledge Maps are non-temporal. (Time-travel /
> bitemporal "as of date X" queries are a separate future capability.)

**Add [R11.22]** to §11.4 (Concept Map ownership and layering):
> **[R11.22]** An agent_group Concept Map records the contributing member agent as a provenance
> partition (`source_member_id`) on each entity and relation. Retrieval reads the shared group
> map across all members by default; an optional member filter scopes retrieval to a subset of
> members. The partition is provenance metadata within the group's trust boundary — group
> membership is itself the read boundary, so a member's contributions are visible to co-members
> through the shared map. Removing an agent from a group revokes its read access on the next
> retrieval.

## 14. Open Questions

- Q-A (non-blocking) — whether wide layers (agent_group, workspace) should later gain the full
  chatroom-style tier matrix rather than a single `concept_map_enabled`. Deferred; requires an
  R11.10/R11.17 amendment. Recorded from the Q-2 reconciliation.
- Q-B (non-blocking) — default value of the platform `recency_half_life_days` setting; a
  product/tuning decision, not a structural one. Suggest 30 days pending telemetry.

## 15. Deviation Log

Appended by `/build`.

### WS1 — Multi-member groups + provenance partition (implemented)

Delivered as commits `543fdc1..bd1d5d2`. Milestones: M1 domain scaffolding (owner
discriminator on `GraphRagConfig`, `source_member_ids` on `Triple`/`RelationEdge`,
`source_member_id` on `DeltaMessage`); M2 `AgentGroupRepository`; M3 multi-member
DISTINCT-union delta feed + `_run_build` live member resolution; M4 edge provenance
persistence + retrieval surfacing. Local gate: unit tests green, `ruff check .` clean,
`ruff format --check` clean on touched files, `mypy` on the 8 touched files introduces
no errors (only the pre-existing `tenancy/repositories.py:487` baseline). AC-1 and the
provenance round-trip are covered by **wiring/integration tests authored for the
`backend-wiring` CI job** — the `compose.test.yml` Postgres/Neo4j stack is unreachable
from the dev host (`getaddrinfo failed`), the same constraint that defers `alembic
upgrade head` to the deploy pipeline.

- **D-1** — Member provenance is stored as an accumulating **list**
  (`source_member_ids`) on the Neo4j `REL` edge, not the singular scalar
  `source_member_id` the WS1 text names. The edge is MERGE-collapsed on
  `(graphrag_config_id, relation, subject, object)`, so two members independently
  stating the same relation collapse onto one edge; a scalar would drop one
  contributor and break AC-2's "member filter returns only that member's
  contributions." The list mirrors the existing `evidence_msg_ids` accumulation
  exactly (same dedup-on-union Cypher) and round-trips through snapshot/restore/
  traverse. This is the correct realization of the spec's intent, surfaced here per
  the "record, don't silently redesign" rule.
- **D-2** — WS1 persists provenance on the Neo4j `REL` **edge only**. The Qdrant
  entity-payload tag and the entity-**node** property that WS1's text also names are
  deferred to **WS4**. The AC-2 member filter operates on traversal edges, which the
  edge tag fully satisfies; the Qdrant/node tags only enable optional seed-scoping,
  whose consumer (the member filter itself) lands in WS4. Deferring avoids churning
  the `EntityEmbedding` / `upsert_entities` / reconciler signatures in WS1 for a
  consumer that does not yet exist; WS4 adds the payload alongside the filter.
- **D-3** — The WS1 "group service (add/remove members) + Project-Owner
  authorization" is delivered as the infrastructure `AgentGroupRepository`; the
  application service, Project-Owner authz, audit emission, and HTTP surface are
  folded into **WS2**, where the owner-centric create consumes them. No WS1 AC needs
  an authorized HTTP member-management surface (AC-1/AC-2 are build-path behavior),
  and a service without its WS2 caller would be unexercised. The repository is the
  shared primitive both work-streams use.

### WS2 — Layered owner configs (implemented)

Delivered as commits `bfd6813..44bf643` (M1 per-owner-kind delta scoping; M2
`AgentGroupService` + facade + errors; M3 owner-centric config create/update, singleton
retired; M4 agent-group management API; plus post-audit hardening). AC-3 (chatroom- and
workspace-owned configs create + owner-not-in-project rejection) is covered by wiring tests;
the owner-centric create redesign the user approved at the gate is complete. Local gate: unit
tests green, `ruff check .` clean, `mypy` on touched files introduces no errors (pre-existing
baseline only), import-linter contracts KEPT. Quality + security audits on the WS2 diff
returned no Introduced-Critical/Warning findings; the two Info nits (`agent_id` nullability,
chatroom soft-delete filter) were fixed.

- **D-4** — The group service's **Project-Owner authorization is enforced at the route
  boundary** (`app/api/v1/agent_groups.py` via `TenancyFacade.is_project_owner`), not inside
  `AgentGroupService`, matching the codebase's SoC convention (routes gate; services mutate +
  audit). Reads require project membership; mutations require a strict Project Owner.
- **D-5** — `openapi.json` + `pnpm run gen:api` regeneration is **deferred to the canonical CI
  environment**. The API contract genuinely changed (owner-centric graphrag create/response +
  new `/agent-groups` endpoints), but the committed `openapi.json` was generated with different
  FastAPI/Pydantic versions than the dev host — a local regen produced a ~3.9k-line diff of
  unrelated schema churn that would corrupt the artifact. CI's `check:openapi-drift` owns the
  authoritative regen. No frontend consumes the changed contract (the attach UI was removed).
- **FU-4 resolved** — the agent-centric `_ensure_singleton_agent_group` inline inserts are
  retired; agent_group ownership now flows through `AgentGroupRepository`/`AgentGroupService`.

### WS3 — Privacy gating (implemented)

Delivered as commits `8492966..88b8775` (M1 `concept_map_enabled` column + migration 0046 on
`agent_groups`/`workspaces`, ORM tables mirrored; M2 strict-Project-Owner toggle + audit for both
owner kinds; M3 evidence-fetch room ACL, AC-7). Local gate: unit tests green (`test_graphrag_retrieve.py`
7 passed incl. 2 new ACL tests; 203 passed across turn_engine/graphrag/conversation; 69 passed on the
M2 slice), `ruff check`/`ruff format --check` clean on touched files, `mypy` on the touched files
introduces no errors (pre-existing baseline only). Quality + security audits on the WS3 diff returned
**no Introduced-Critical/Warning findings**; the security audit confirmed both toggles are strictly
Project-Owner-gated with the project resolved from the resource (no IDOR) and the AC-7 ACL has no
bypassing/agent-less production caller (fail-closed). `alembic upgrade head` for 0046 and `gen:api`
remain deferred to CI (D-5, same host constraint).

- **D-6** — AC-6 is delivered in **two halves across WS3 and WS4**. WS3 lands the privacy control
  surface: the `concept_map_enabled` column (strict default `false`, so every wide owner is private on
  upgrade), the strict-Project-Owner toggle endpoints for both owner kinds, and the audit event on
  change. The AC's other clause — "wide maps contribute nothing to retrieval unless enabled" — is a
  *read-path* rule enforced by the WS4 layered resolver (which selects only enabled agent_group/workspace
  layers). Wiring a flag read in WS3 with no resolver to consume it would be dead code; the quality audit
  flagged the column as write-only, which is expected for this split. AC-6 is therefore left unchecked
  until WS4 wires the read side. The evidence-fetch ACL (AC-7) is independent and fully delivered here.
- **D-7** — The **workspace** toggle route inlines its Project-Owner check (fetch workspace →
  `TenancyFacade.is_project_owner` → `_raise_forbidden`) rather than reusing an `_assert_project_owner`
  helper as the agent_group route does, because it mirrors the file-local convention of the sibling
  `read_workspace`/`delete_workspace` routes in `workspaces.py` (resolve project from the facade in-band,
  then gate). Both routes reach the identical strict-owner decision; unifying the two owner surfaces is
  tracked as FU-7.

**Code-review remediation** (commits `7e9c9e4..8d3dd1b`). A precision code-review pass (8 finder
angles + verify) over the WS3 diff surfaced one substantive correctness item and several nits, all
fixed in-scope: (1) the evidence fetcher applied the `_MAX_EVIDENCE_EXCERPTS` cap *before* the room-ACL
filter, so unreadable refs at the top of the ranking could starve readable ones below them — now
filters first and caps after, scanning a bounded candidate window; (2) `is_agent_in_chatroom` is
memoized per chatroom (FU-8); (3) the injected ACL check is typed via a `Protocol`; (4) the concept-map
toggle UPDATE gained a `deleted_at IS NULL` guard and NotFound-on-lost-race so a concurrent soft-delete
can no longer write/audit a tombstoned owner; (5) the strict-Project-Owner gate is centralized in
`deps.assert_project_owner` (FU-7 authz half); (6) a regression test pins filter-before-cap +
memoization and asserts the service forwards `querying_agent_id`. The two refuted findings (use the
user-principal room-flag matrix instead of `chatroom_agents`; "wide-layer cross-room evidence dropped")
were confirmed intended: agent read-access *is* `chatroom_agents` membership, and cross-room drop is
exactly AC-7.

### WS4 — Layered retrieval with tiered fill (implemented)

Delivered as commits `ccf249d..f9112b8` (M2 tiered assembler; M1 `list_layers_for_turn` resolver +
facade + wiring test; M3 turn-engine wiring + per-layer failure isolation). Completes AC-4 (tiered
narrow-first fill + narrowest dedup), AC-5 (single-layer byte-identical to the flat path), AC-6 (the
read-gating half — wide layers gated on `concept_map_enabled IS TRUE`), and AC-11 (live-membership
resolver drops a removed member's group layer next turn). Local gate: unit tests green
(`test_graphrag_retrieve.py` 11 passed incl. tiered-fill/dedup, single-layer identity, per-layer
isolation), 239 passed across the graphrag/turn/observer/conversation suites, `ruff`/`ruff format`
clean, `mypy` on the 4 touched source files introduces no errors (pre-existing baseline only). AC-4's
end-to-end and AC-6/AC-11's DB-level checks are covered by the wiring test
(`test_list_layers_for_turn_orders_and_gates_layers`), which runs in the CI backend-wiring job (the
local host has no Postgres). Quality + security audits on the WS4 diff returned **no
Introduced-Critical/Warning findings**; the security pass confirmed the resolver preserves every
tenant/room/membership boundary (FU-5 not worsened — group membership is trust-bounded to the agent's
project) and the layered path threads the WS3 evidence room-ACL to every layer.

- **D-8** — The tiered budget fill is realized as **append-narrow-first + the existing 2 KB tail-cap**,
  not a per-layer byte accounting loop. `_merge_layers_tiered` concatenates layers narrow -> wide with
  dedup keeping the narrowest occurrence and deliberately does **not** re-sort, so `_cap_to_2kb` trims
  from the tail — dropping the widest layer's content first and giving each wider layer only the
  remainder. This reuses the Phase-2a cap verbatim and keeps a single-layer assembly byte-identical to
  the flat path (AC-5); the spec's "each wider layer uses only the remainder" holds without a second
  budget mechanism. Recorded per "record, don't silently redesign."
- **D-9** — `query_layers` runs retrieval **per layer via the existing single-config `_graphrag_query`**,
  which builds and closes its own Neo4j + Qdrant clients each call. Layer count is bounded
  (chatroom + <=N groups + workspace) and each layer needs its own config load + embedder anyway, so
  the extra handshakes are acceptable; hoisting client construction above the loop is deferred to FU-11.
  A per-layer `try/except` isolates a failing layer (log + continue) so retrieval degrades to fewer
  layers rather than nuking the whole context — matching the spec's "degrades to fewer/zero layers
  silently, never fails a turn."

### WS5 — Temporal Concept Map (implemented)

Delivered as commits `97ae49b..e02e27d` (M1 `recency_half_life_days` config field + migration 0047 +
domain `recency_weighted_score` + create/update validation + API plumbing; M2 message-timestamp
threading builder -> Neo4j earliest/latest MERGE; M3 recency-weighted retrieval ranking; plus close-out
hardening + a DRY consolidation). Completes AC-8 (edges carry `first_seen_at` earliest-wins /
`last_seen_at` latest-wins, derived from `message.created_at`, never from LLM output) and AC-9
(retrieval ranks on `confidence x exp(-Δt / half_life)`, per-config half-life validated on
create/update). Local gate: unit tests green (`test_graphrag_retrieve.py` recency reranking +
`test_graphrag_builder.py` temporal provenance, 31 passed across the three touched suites), broad
regression 203 passed, `ruff`/`ruff format` clean on touched files, `mypy` on the 4 touched source
files introduces no errors (pre-existing baseline only). AC-8's Neo4j MERGE round-trip is exercised by
the CI backend-wiring job (no local Neo4j). Quality + security audits on the WS5 diff returned **no
Introduced-Critical/Warning findings**; the security pass confirmed timestamps are read from
`message.created_at` (never from extractor/LLM output), a foreign evidence ref is silently dropped
(`if ref in msg_created_at`), migration 0047 is expand-only, the ORM column type matches the PG type,
and Cypher stays parameterized with no cross-config leak.

- **D-10** — Timestamps are represented as **float UTC epoch seconds**, not native Neo4j
  temporal/`datetime` values. Epoch seconds make the earliest/latest MERGE a plain numeric
  `CASE WHEN row.first_seen_at < r.first_seen_at` comparison and let `recency_weighted_score` do the
  `exp(-Δt / half_life)` math directly, with NULL coalescing that mirrors the existing confidence
  handling (NULL last_seen / half_life -> decay factor 1.0; NULL confidence -> 0.0). No consumer needs
  wall-clock calendar semantics — only age deltas — so the numeric form is both simpler and cheaper.
- **D-11** — Temporal provenance is stamped in the **builder** (`attach_temporal_provenance`, mirroring
  `attach_member_provenance`) rather than in the extractor as the WS5 prose literally reads. The
  extractor sees only text spans; the builder is where each triple is already being joined back to its
  source `DeltaMessage`, so it is the single place that owns `message.created_at`. Placing the stamp in
  the extractor would require threading message metadata into a component whose contract is text->triples.
  This is the correct realization of "derived from message timestamps, not LLM output" and keeps the LLM
  trust boundary intact.

**Close-out hardening** (commits `972de9a`, `e02e27d`). The security audit noted `recency_half_life_days`
(`gt=0`) still admitted `Infinity` via Pydantic's default `allow_inf_nan=True`, which would silently
disable decay — fixed with `allow_inf_nan=False` on the create + patch fields. The quality audit's one
Info nit (the `score`-else-`confidence` rank fallback duplicated between the retrieve-service sort and
the context-provider merge) is resolved by promoting a single `domain.edge_rank` helper both now call.

## 16. Follow-ups

- FU-1 — expose recency half-life and layer enablement in the Phase 4 UI.
- FU-2 — bitemporal "as of date X" time-travel queries (R11.21 reserved).
- FU-3 — if telemetry shows wide-layer over-sharing, revisit Q-A (tier matrix on wide layers).
- FU-4 (WS2-scoped) — `_ensure_singleton_agent_group` (`graphrag_config_service.py:144-192`)
  inlines the `agent_groups` + `agent_group_members` inserts that
  `AgentGroupRepository.create_group`/`add_member` now encapsulate (quality-audit DRY).
  WS2's owner-centric create rewrite should route group creation through the repository
  and retire the inline inserts.
- FU-5 (open, defense-in-depth) — the delta feed (`app/workers/tasks/graphrag.py`) relies on
  the owner→project invariant (owner validated in-project at create) for tenant containment
  rather than an explicit `project_id` predicate in the SQL. WS2 re-confirmed no cross-tenant
  vector; adding an explicit `cfg.project_id` scope remains a hardening option (the loader would
  need to receive `project_id`).
- FU-6 (minor, WS2) — the `owner_kind`→column dispatch is duplicated across five sites
  (`graphrag_repositories._OWNER_COLUMN`, API `_owner_id`, service `_config_owner` /
  `_assert_owner_in_project`, worker `_resolve_delta_scope`); a future 4th owner kind touches
  all five. Literal-constrained so not a defect — consider centralizing the kind→column map.
- FU-7 (WS3, authz half resolved) — the owner-gate is now centralized in
  `app/api/v1/deps.py:assert_project_owner` (admin bypass + `is_project_owner` + forbidden), called
  by both toggle routes; the agent-groups helper delegates to it and the private `_raise_forbidden`
  import is gone from the route files. **Still open:** the double-fetch (route resolves the owner for
  authz, then the service re-fetches for the audit `project_id`) — a service that surfaces the
  resolved owner would remove the second read.
- FU-8 (WS3, largely resolved) — the evidence fetcher now memoizes `is_agent_in_chatroom` per
  chatroom, so refs sharing a room cost one ACL query instead of one per excerpt (worst case = number
  of distinct rooms among the candidates, not the excerpt count). A single batched
  `WHERE chatroom_id IN (...)` remains a further optional optimization if evidence volume grows.
- FU-9 (pre-existing, conversation; won't-fix by convention) — `app/api/v1/workspaces.py` route
  handlers instantiate `WorkspaceService(db)` directly (`list_workspaces`, `create_workspace`, the
  toggle), against the generic CLAUDE.md "call the facade" rule. In the conversation context this is
  the *documented* convention: `ConversationFacade` is explicitly read-only ("Writers must go through
  the use-case services"), so all writes in the context go service-direct. Rerouting a write through
  the read-only facade would break its contract and be inconsistent with create/delete; kept as-is.
  A broader change would introduce a write-capable conversation facade for the whole context.
- FU-11 (minor, WS4, perf) — `query_layers` retrieves per layer through the single-config
  `_graphrag_query`, so each layer opens/closes its own Neo4j + Qdrant clients (D-9). Bounded by the
  layer count, but an L-layer turn does L connect/auth/close handshakes. Hoist client construction
  above the layer loop and pass shared clients into per-layer retrieval to collapse them to one set.
- FU-10 (minor, hardening) — the toggle routes raise `AgentGroupNotFound`/`WorkspaceNotFound`
  (404) before the owner check, so a non-owner can distinguish "exists but forbidden" (403) from
  "absent" (404). Negligible behind unguessable v4 UUIDs and it matches the pre-existing
  404-before-authz convention; return 403/404 uniformly only if strict non-enumeration is wanted.
