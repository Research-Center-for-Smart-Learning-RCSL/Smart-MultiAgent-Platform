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
- [ ] AC-4: at a turn, retrieval assembles chatroom + enabled agent_group + enabled workspace
  layers under a single 2 KB cap with tiered narrow-first fill (chatroom fills first) and
  entity dedup keeping the narrowest occurrence.
- [ ] AC-5: a single-owner (chatroom-only) agent's retrieval output is unchanged from
  pre-2b behavior (characterization test).
- [ ] AC-6: agent_group and workspace maps contribute nothing to retrieval unless
  `concept_map_enabled` is true; only a strict Project Owner can toggle it; each toggle is
  audit-logged.
- [ ] AC-7: a principal who cannot read a chatroom receives no evidence excerpt sourced from
  that room, even when a shared-layer edge references it.
- [ ] AC-8: entities/relations carry `first_seen_at` (earliest-wins) and `last_seen_at`
  (latest-wins) derived from message timestamps, not LLM output.
- [ ] AC-9: retrieval ranking applies `weight × exp(-Δt / recency_half_life_days)`; a recent
  low-confidence edge can outrank a stale high-confidence one; the half-life is per-config and
  validated on create/update.
- [ ] AC-10: deleting a chatroom, workspace, or agent_group purges its owned config's Neo4j
  subgraph and Qdrant points (audit-logged); the reconciler finds no orphan.
- [ ] AC-11: a member removed from a group loses read access to the group map on the next turn.
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

## 16. Follow-ups

- FU-1 — expose recency half-life and layer enablement in the Phase 4 UI.
- FU-2 — bitemporal "as of date X" time-travel queries (R11.21 reserved).
- FU-3 — if telemetry shows wide-layer over-sharing, revisit Q-A (tier matrix on wide layers).
- FU-4 (WS2-scoped) — `_ensure_singleton_agent_group` (`graphrag_config_service.py:144-192`)
  inlines the `agent_groups` + `agent_group_members` inserts that
  `AgentGroupRepository.create_group`/`add_member` now encapsulate (quality-audit DRY).
  WS2's owner-centric create rewrite should route group creation through the repository
  and retire the inline inserts.
- FU-5 (WS2-scoped) — the multi-member delta feed (`app/workers/tasks/graphrag.py`) relies
  on the Phase-1/2a owner→project membership invariant for tenant containment rather than an
  explicit `project_id` predicate (security-audit defense-in-depth, pre-existing). WS2 threads
  owner-kind scoping through this loader; add an explicit `cfg.project_id` scope at that point.
