---
type: feature
status: implemented
created: 2026-10-12
requirements: [R11.08, R11.22, R11a.01]
depends_on: [2026-10-12-graphrag-phase5a-evidence-refs-expand, 2026-10-12-graph-build-busy-retry]
---

# GraphRAG Phase 5b: true member provenance and a per-member read filter for agent_group Concept Maps

## 1. Summary

[R11.22] promises that an agent_group Concept Map records "the contributing member agent"
on what it holds, and that "an optional member filter scopes retrieval to a subset of
members". Phase 2b recorded a member on each relation but never built the filter (its AC-2
is unticked, `docs/tasks/2026-07-07-graphrag-phase2b-layers-privacy-temporal/spec.md:323-325`),
and the two-axis blueprint moved it here (its D-2).

Analysis found that the recorded member is not the contributor. It is the member whose
room a message came from, and when several members share a room only the smallest id is
kept (`backend/app/workers/tasks/graphrag.py:191-210`; pinned by
`tests/wiring/test_graphrag_delta_union.py:149`). A filter on that value would mislead.

This feature therefore does three things:
- **Fixes provenance:** an agent message's author, otherwise the room's co-present
  members.
- **Adds the filter:** a per-member read filter the Project Owner sets.
- **Repairs existing maps:** an operator command rebuilds existing agent_group maps in
  place so their provenance is correct.

## 2. Goals and Non-goals

**Goals**

- G-1: A relation in an agent_group map carries, as `source_member_ids`, the authoring
  member for an agent message, and every co-present member for any other message.
- G-2: The Project Owner can restrict what one member reads from its group map to the
  contributions of chosen members; unrestricted is the default and reads the shared union.
- G-3: At turn time a restricted member sees only relations with at least one selected
  member in their provenance, and only entities reachable through those relations.
- G-4: Existing agent_group maps can be rebuilt in place, by an operator, so their
  provenance follows G-1.

**Non-goals**

- Member tags on Qdrant entity points or Neo4j entity nodes; seed search stays unfiltered
  (Q-6, FU-1).
- Showing or filtering provenance in the human-facing graph view (`GET
  /api/graphrag/{config_id}/graph`, `app/api/v1/graphrag.py:375-409`).
- Chatroom- and workspace-owned maps, which have no members (`graphrag.py:270-285`).
- Historical room presence. `chatroom_agents` keeps no membership history
  (`conversation/infrastructure/tables.py`, `chatroom_agents`), so "co-present" means
  bound to the room when the build reads the message (FU-2).
- Any filter on Knowledge Maps.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How is R11.22's filter handled, given the recorded member is not the contributor? | Fix provenance first, then add a per-member filter. | Requester's choice. Rejected: amending R11.22 to drop the filter, and a human-facing graph-view filter only. |
| Q-2 | Does this depend on the evidence-refs expand dossier? | Yes. | Overlap prerequisite: both edit the relation write and read Cypher in `neo4j_driver.py` (`:49-82, 150, 187-214, 447-466`) and the row loop in `graphrag_retrieve.py:156-186`. |
| Q-3 | What does provenance record? | An agent message whose sender is a group member: that member. Any other message (a person, an agent outside the group): every group member bound to the room. | Requester's choice. The filter then means "what this member said or was present for", consistent with [R11.08]'s co-present members. Rejected: author only, with human messages unattributed; co-present members regardless of author. |
| Q-4 | Who sets the filter, and how is it evaluated? | Per member, by the Project Owner: `agent_group_members.read_member_ids` (`NULL` = the whole group). A relation is visible when its provenance intersects the list. A relation with no provenance is hidden under a filter. Entities appear only through a visible relation. | Requester's choice. Intersection, because a relation co-stated by A and B is genuinely A's too. Rejected: one setting per group, and a per-request parameter that the agent turn has no way to supply. |
| Q-5 | How does existing data get the new provenance? | An operator command rebuilds every agent_group map in place, dry-run until armed. Until then, legacy relations keep their old single-member value. | Requester's choice. A delta build never rewrites old relations, and the delta union only adds members (`neo4j_driver.py:56-58`). Rejected: letting maps refresh naturally, which leaves misleading provenance indefinitely. |
| Q-6 | Do entity nodes and Qdrant points get member tags? | No; entities are derived from visible relations. | Requester's choice. No re-embedding and no change to the shared vector payload; seed ranking stays unfiltered (FU-1). |
| Q-7 | Does this depend on `2026-10-12-graph-build-busy-retry`? | Yes. | Requester's choice. Overlap prerequisite: both edit `graphrag_build` / `_run_build` in `app/workers/tasks/graphrag.py` (busy-retry at `:404-424`; this feature adds a full-rebuild path to the same call), and the rebuild command relies on a busy build waiting. |
| Q-8 | When removing a member empties another member's restriction list, what does that member read? | Nothing from the group map, until the owner sets the list again; the UI warns. | Requester's choice (was OQ-1). Fails closed: a restriction is never silently widened. Rejected: reverting to the whole group. |

## 4. Current State

- **Provenance write.** `_resolve_delta_scope` reads live members
  (`app/workers/tasks/graphrag.py:259-269`; `group_repository.py:225-235`). The delta query
  joins `chatroom_agents` and selects `ca.agent_id AS source_member_id` with `DISTINCT ON
  (m.created_at, m.id)` ordered by `ca.agent_id` (`graphrag.py:191-210`, docstring
  `:142-148`). It ignores `messages.sender_id`. The builder attaches the members behind
  each triple's evidence (`graphrag_builder.py:92-106, 325-343, 359`). Neo4j stores the
  list on `REL` edges only (`neo4j_driver.py:49-82, 150, 190, 214`). Entities and Qdrant
  points carry none (`neo4j_driver.py:130-143`; `graphrag_vector_store.py:214-219`).
- **Retrieval.**
  1. The turn engine resolves layers (`turn_engine.py:4386-4396` →
     `KnowledgeFacade.resolve_graphrag_layers`, `knowledge/interfaces/facade.py:134-146` →
     `GraphRagConfigRepository.list_layers_for_turn`, group branch
     `graphrag_repositories.py:512-528`).
  2. It queries each layer (`graphrag_context_provider.py:112-175, 227-307`).
  3. Retrieval searches Qdrant by config (`graphrag_retrieve.py:132-137`) and traverses
     Neo4j with no member predicate (`neo4j_driver.py:447-466`, `LIMIT 50`).
  4. `source_member_ids` is copied onto `RelationEdge` (`graphrag_retrieve.py:173`) and
     read by nothing afterwards.
  5. `bundle.entities` is the raw seed list (`graphrag_retrieve.py:143, 203`).
- **Membership read boundary.** A removed member stops reading on the next retrieval
  (`graphrag_repositories.py:516-528`; Phase 2b AC-11, wiring
  `tests/wiring/test_graphrag_owner_resolution.py:295-362`). Its id stays on old relations.
- **Group surfaces.**
  - **Tables:** `agent_group_members` has only `agent_group_id` and `agent_id`
    (`agent_groups/infrastructure/tables.py:44-60`).
  - **Routes:** `app/api/v1/agent_groups.py` (members GET `:252`, POST `:264`, DELETE
    `:286`; models `:57-61`). Group mutations are strict Project Owner at the route
    (Phase 2b D-4).
  - **UI:** the members table in `frontend/src/slices/agent-groups/views/AgentGroupDetailView.vue:156-214`.
- **Full rebuild.** `GraphRagBuilder.run` takes `mode="full"`, which re-reads all history
  (`graphrag_builder.py:176, 319`), and `replace=True`, which resets evidence and
  provenance per build and prunes relations the build did not produce. The Knowledge Map
  already uses replace (`app/workers/tasks/knowmap.py:907`). `graphrag_build` passes
  neither (`app/workers/tasks/graphrag.py:406`).

## 5. Design

**5.1 Provenance (G-1).** The agent_group delta query returns, per message, a list of
members instead of one:
- `[m.sender_id]` when `m.sender_type` is agent and the sender is a current group member;
- otherwise the members bound to `m.chatroom_id` (`array_agg` over `chatroom_agents`
  restricted to the live member set).

The builder's `msg_member` map becomes message → members, and
`attach_member_provenance` unions the lists behind a triple's evidence. The Neo4j write is
unchanged in shape: `source_member_ids` is already a list. The wiring assertion that pins
`min(a1, a2)` changes to the new rule.

**5.2 Filter setting (G-2).**
- **Storage:** migration adds `agent_group_members.read_member_ids UUID[] NULL`.
- **Write route:** `PUT /api/agent-groups/{group_id}/members/{agent_id}/read-members` with
  body `{"member_ids": [...] | null}`, strict Project Owner as the other group mutations.
  The ids must be current members of the same group (422 otherwise), and an empty list is
  refused (422: use `null` for "whole group").
- **Read route:** `GET .../members` returns each member's `read_member_ids`, through the
  agent_groups facade and service, not the route.
- **Removal:** removing a member from a group also removes its id from every other
  member's `read_member_ids` in the same transaction. A list left empty by that removal is
  kept empty, so that member reads nothing from the group map until the owner changes it
  (Q-8).

**5.3 Retrieval (G-3).** `list_layers_for_turn` returns, for a group layer, the reading
member's `read_member_ids`. This is the querying agent's own row, resolved in the same
membership query that gates the layer, so it carries the same no-cache guarantee. The
retrieval path then carries it down:
- `GraphRagContextProvider` → `GraphRagRetrieveService.query(..., member_ids)` →
  `traverse(..., member_ids)`.
- When set, the traversal Cypher keeps a path only if every edge on it satisfies
  `any(x IN edge.source_member_ids WHERE x IN $member_ids)`. Filtering inside Cypher
  keeps the `LIMIT 50` meaningful, where a post-filter would starve it.
- With a filter, the bundle's entities are derived from the visible relations, reusing
  `entities_from_relations` (`knowmap_context_provider.py:99-109`) moved to the shared
  graph domain.
- Without a filter, behavior is unchanged.

**5.4 Rebuild (G-4).**
- **Worker:** `graphrag_build` gains a keyword-only `full_rebuild: bool = False`, which
  runs `builder.run(mode="full", replace=True)`.
- **Command:** `python -m smap.maintenance rebuild-agent-group-concept-maps`. It lists
  agent_group-owned, non-deleted Concept Maps with their message counts (the token cost
  is borne by each project's builder key), and is dry-run until armed by an environment
  variable, as `purge-session-dirs` is (`backend/smap/maintenance/__main__.py:38-73`).
- **Armed run:** enqueues one full rebuild per config under a distinct job id, so it is
  not deduplicated against an ordinary build, and reports enqueued, deduped and failed.
- **Consistency:** the 2PC build keeps the old graph readable until the new one commits.
  With `2026-10-12-graph-build-busy-retry`, a rebuild that meets a running build waits
  rather than failing.

**Options considered** are the rejected answers in §3; the chosen design is the requester's
on every question.

## 6. Detailed Changes

- **Backend, knowledge context:**
  - the delta query and `_resolve_delta_scope` (`app/workers/tasks/graphrag.py:142-288`);
  - the builder's provenance collection (`graphrag_builder.py:92-106, 325-359`);
  - `traverse` member predicate (`neo4j_driver.py:447-466`);
  - `GraphRagRetrieveService.query` (`graphrag_retrieve.py:95-206`);
  - `GraphRagContextProvider` (`graphrag_context_provider.py:112-307`);
  - `list_layers_for_turn` (`graphrag_repositories.py:479-552`);
  - `entities_from_relations` moved to `contexts/knowledge/domain/graphrag.py`, with
    `knowmap_context_provider.py` importing it.
- **Backend, agent_groups context:** a migration for `read_member_ids` (the next free
  revision after the current head); table, domain model, repository, service and facade
  methods; the member-removal cleanup.
- **Backend, routes:** `app/api/v1/agent_groups.py`, the read-members route and the members
  response field, with Pydantic models.
- **Backend, worker and CLI:** `graphrag_build(full_rebuild=...)`; the
  `rebuild-agent-group-concept-maps` command and its runbook entry in
  `docs/runbook-upgrade.md`.
- **Frontend:**
  - `AgentGroupDetailView.vue` members table: a "Reads from" control per member (whole
    group, or chosen members) and the empty-list warning (Q-8);
  - an `agentGroups` API wrapper method;
  - i18n keys in `en` and `zh-TW`;
  - `pnpm run gen:api`.

## 7. NFR Checklist

- **Performance:** one extra column read in the existing membership query; a per-edge
  list predicate on a traversal capped at 50 rows. The delta query gains an `array_agg`
  per message window.
- **Cost:** the rebuild command re-extracts every agent_group map's history on its
  builder key. The dry run reports the message counts first, and nothing runs unless armed.
- **Compatibility:**
  - The new column is nullable, so old code ignores it.
  - Relations written before the rebuild keep their old single-member value and are
    filtered by it until rebuilt.
  - The worker parameter defaults to today's behavior.

## 8. Security Considerations

- **Narrowing only:** the filter can only narrow what a member reads from a map it is
  already allowed to read. Membership remains the read boundary ([R11.22]). A filter
  never grants a non-member access, and `NULL` is today's behavior.
- **Authorization:** the setting is Project-Owner gated, like every group mutation. Ids
  are validated as current members of the same group, so a request cannot name agents from
  another group or project (tenant boundary).
- **No new prompt surface:** the agent-turn path changes which relations are rendered, not
  how. Rendering still goes through `_render_bundle_text` and the knowledge budget.
- **Legacy provenance:** until the rebuild, a filter is evaluated on legacy single-member
  provenance and may show or hide a relation by the old room rule. The runbook says to run
  the rebuild before owners rely on filters.

## 9. Quality Notes

- **Existing debt:** routes reach services through the facade inconsistently in the
  knowledge area. Follow `agent_groups.py`'s existing facade calls, not the
  `AgentService(db)`-in-route pattern found in `agents.py` (evidence-refs FU and
  display-name-validation FU-8).
- **Patterns to follow:**
  - the Knowledge Map allowlist pipeline (`knowmap_context_provider.py:161-180`) for
    filter → entities from relations;
  - `smap.maintenance` commands for the armed dry run;
  - owner-strict group routes (Phase 2b D-4).
- **Reuse inventory:**
  - `entities_from_relations`;
  - `AgentGroupRepository.list_member_agent_ids`;
  - the builder's existing `mode` and `replace` parameters;
  - `enqueue` and the `_job_id` conventions;
  - the existing members table and the shared form components in the agent-groups slice.

## 10. Risks and Rollback

- **Provenance changes for every new agent_group build.** Relations already in a map keep
  their old value until the rebuild, so the map holds mixed provenance in between.
- **The rebuild re-extracts with the current model**, so a rebuilt map can differ in
  content, not only provenance.
- **Rollback:** revert the code. The nullable column can stay, and the old code ignores
  it. Relations rebuilt with the new provenance are still read correctly by the old code,
  which never filters.

## 11. Acceptance Criteria

- [x] AC-1: in an agent_group map, a relation evidenced by a member agent's message carries
  exactly that member; one evidenced by a person's message carries every group member bound
  to that room; neither is decided by the smallest id (wiring test, real Postgres).
  `tests/wiring/test_graphrag_delta_union.py`: a member's own message in a shared room, a
  person's message, a message from an agent outside the group, and member removal.
- [x] AC-2: the Project Owner can set and clear a member's `read_member_ids`; a non-owner
  gets 403; ids outside the group or an empty list get 422; removing a member strips its id
  from every list in the group (route and service tests).
  `tests/unit/test_agent_group_read_members.py`, `tests/wiring/test_agent_group_read_members.py`.
- [x] AC-3: a member with `read_member_ids = [B]` receives only relations whose provenance
  includes B, only entities reachable through them, and no relation with empty provenance;
  a member with `NULL` receives the full union as today (retrieval tests; one real-Neo4j
  test for the traversal predicate). `tests/unit/test_graphrag_member_filter.py`,
  `tests/integration/test_graphrag_member_filter_traverse.py` (a hidden first hop does
  not lead to a visible second one), `tests/wiring/test_graphrag_owner_resolution.py`.
- [x] AC-4: `rebuild-agent-group-concept-maps` lists without arming, enqueues one full
  replace rebuild per agent_group map when armed, and after it the maps' relations carry
  the new provenance (CLI test plus the compose-stack check below).
  `tests/unit/test_rebuild_agent_group_concept_maps.py`,
  `tests/wiring/test_rebuild_agent_group_concept_maps.py`; live in D-6.
- [x] AC-5: on the compose test stack with the provider stub of
  `2026-07-07-graphrag-two-axis-redesign` D-6, a two-member group map built from both
  members' rooms shows each restricted member only its selected contributions in the turn's
  system prompt, observed live. See D-6.
- [x] AC-6: the group detail page lets the owner set "Reads from" per member, shows the
  empty-list state, and every new string is in `en` and `zh-TW` (component test).
  `frontend/src/slices/agent-groups/__tests__/AgentGroupDetailView.test.ts`; the two
  locale files hold the same 64 keys.
- [x] AC-7: backend and frontend lint, typecheck, tests (db tier included) and build pass in
  CI; `gen:api` regenerated; the migration upgrades and downgrades. CI run 38211945379
  green at `97ab75c7`. The migration was round-tripped down and up on the compose stack.

## 12. Test Plan

- **Unit:**
  - the delta-row → member-list mapping;
  - `attach_member_provenance` with lists;
  - the retrieval filter and entity derivation;
  - the read-members route (authz, validation, `null` vs empty);
  - the member-removal cleanup;
  - the CLI dry and armed paths.
- **Wiring (real Postgres):** `test_graphrag_delta_union.py` updated to the new rule
  (AC-1); `test_graphrag_owner_resolution.py` extended for the per-layer
  `read_member_ids`.
- **db tier (real Neo4j):** the traversal predicate keeps only edges intersecting the
  members, across a two-hop path.
- **Frontend:** `AgentGroupDetailView` test for the control and its empty state.
- **Live:** AC-5 on the compose stack.

## 13. SRS Delta

Replace [R11.22] with:

- **[R11.22]** An agent_group Concept Map records, on each relation, the members it came
  from (`source_member_ids`): the authoring member for a member agent's message, and every
  group member bound to the room for any other message. Retrieval reads the shared group map
  across all members by default. The Project Owner may restrict what one member reads to
  the contributions of chosen members; a restricted member then sees only relations whose
  members include a chosen one, and only entities reachable through those relations. The
  record is provenance metadata within the group's trust boundary: group membership is the
  read boundary, and a restriction only narrows it. Removing an agent from a group revokes
  its read access on the next retrieval and removes it from every member's restriction.

## 14. Open Questions

None. OQ-1 was resolved at approval as Q-8.

## 15. Deviation Log

Plan approved by the requester before implementation (migration, API contract, cross-context).
D-1 to D-4 are the implementation choices named in that plan.

- **D-1. The provenance seam carries a list.** `DeltaMessage.source_member_id` (one id)
  became `source_member_ids` (a tuple) in `graphrag_ports.py`. The Knowledge Map loader
  and extractor share the port and now send an empty tuple. The delta query computes the
  list per message in SQL (`CASE` over `m.sender_type` / `m.sender_id`, else an `ARRAY`
  subquery over `chatroom_agents`). Which row `DISTINCT ON` keeps therefore no longer
  matters, and the `ca.agent_id` tiebreak was removed.
- **D-2. The read filter travels as a layer object.** `list_layers_for_turn` returns
  `ConceptMapLayer(config, read_member_ids)` (new, in `knowledge/domain/graphrag.py`)
  instead of bare configs. The turn engine (`agents` context, `_graphrag_context`) passes
  the group layers' filters to `query_layers` as a `member_filters` mapping, and the
  provider hands each layer its own filter. `_has_knowledge_source` only tests for any
  layer and is unchanged.
- **D-3. The members response is extended, not replaced.** `GET` and `POST
  .../members` keep `members: [uuid]` and gain `read_members: [{agent_id,
  read_member_ids}]`, so an older client is unaffected.
- **D-4. The rebuild is a worker keyword plus a command-owned job id.**
  `graphrag_build(full_rebuild=True)` runs `mode="full", replace=True`. The command
  enqueues it as `graphrag:rebuild:{config}:{run timestamp}` with
  `triggered_by="provenance_rebuild"`, so it is never deduplicated against an ordinary
  build. A busy retry keeps the keyword, because arq `Retry` re-runs the same job.
- **D-5. The delta scope's unused fragments were removed** (after the quality audit).
  `_DeltaScope.order_extra` and `expanding` lost their last use with D-1, and the member
  set is bound once, as `uuid[]` (`ca.agent_id = ANY(...)`).
- **D-6. Live verification (AC-4, AC-5).** It ran on the compose stack: branch code in
  `backend-web`, a real arq worker with the OpenAI boundary stubbed (blueprint D-6), and
  migration 0103 applied (and round-tripped down and up).
  - **Setup:** a project with members Ann and Ben in separate rooms, and an enabled
    agent_group map built from one person message in each room.
  - **Provenance:** Neo4j held `Photosynthesis -relates_to-> Plants` as `[Ann]` and
    `Mitochondria -relates_to-> Cells` as `[Ben]`.
  - **Turns** (the agent's system block):
    1. Both unrestricted: Ann's turn listed both relations.
    2. Ann restricted to `[Ben]`: only the Mitochondria relation, with entities
       `Mitochondria, Cells`.
    3. Ben restricted to `[Ann]`: only the Photosynthesis relation.
    4. Ann reset to `null`: both again.
  - **Rebuild:** I overwrote every relation's provenance with `['legacy-member']`. The
    dry run listed the map (10 messages) and enqueued nothing. The armed run enqueued it,
    and after the build the provenance was `[Ann]` and `[Ben]` again. A third relation,
    stated in a person's message in each room, carried `[Ann, Ben]`.
  - **Side effect:** the armed run enqueued 22 maps, because the wiring tests run against
    the same database had left group maps there.
- **D-7. Audit outcomes.**
  - **Quality:** no Critical or Warning. Of the Info findings, the dead `order_extra` and
    `expanding` fields (D-5), the duplicated response mapping and an unpointed cost
    estimate were fixed in `96fde3d2`. The view's size goes to FU-8.
  - **Security:** no Critical or High. The one Medium (plausible) is FU-5, and the
    builder docstring that claimed provenance is "never from LLM output" was corrected.
    The three Hardening items are FU-4, FU-6 and FU-7.
- **D-8. `/code-review` fixes.**
  - **Deleted agents in a filter.** A filter naming a since-deleted agent made the
    "Reads from" dialog unsaveable, since the backend refuses non-members. The dialog now
    keeps only current members, and the row labels a deleted agent "a removed agent"
    instead of its id. Covered by a component test.
  - **Pruning on rebuild.** A full replace rebuild keeps only what it re-derives from
    the rooms of the group's current live members, so it removes relations that came
    only from other rooms. For a group with no live member it would have emptied the
    map. The rebuild command now lists such maps and skips them, and runbook §3.2 states
    the pruning.
  - **Not changed:**
    - Co-presence at build time is a non-goal (§2, FU-2).
    - Replace builds keep the old confidence and timestamps (FU-11).
    - The provenance subquery's per-row cost is FU-12.
    - The cost estimate's duplicate scope and its N+1 are covered by D-7.
    - Filters naming soft-deleted agents are FU-7.

## 16. Follow-ups

- **FU-1.** Member tags on Qdrant entity points and Neo4j entity nodes, so seed search can
  also be scoped (Phase 2b D-2's deferred half).
- **FU-2.** Room membership history: "co-present" uses the binding at build time, not at
  message time.
- **FU-3.** A member-provenance column in the human-facing graph view.
- **FU-4.** `set_read_members` validates the member set, then writes. A concurrent
  `remove_member` committing in between leaves a filter naming the removed agent. This
  only narrows, never widens beyond the group. Lock the group's member rows or
  re-validate inside the `UPDATE` (security audit, Hardening).
- **FU-5.** Which messages count as a relation's evidence is the extraction model's
  output, checked only against the build's own messages
  (`graphrag_builder.attach_member_provenance`). A content author who steers the
  extractor can therefore tag a relation with another member's provenance and get it
  past a read filter inside the same group. This cannot cross a group, project or tenant
  boundary (security audit, Medium, plausible). The options are to cross-check a cited
  message against the triple, or to say in [R11.22] and the UI that the filter scopes
  relevance, not confidentiality against content authors.
- **FU-6.** `rebuild-agent-group-concept-maps` has no per-project or per-config scope, and
  no guard against a second armed run re-extracting every map again on the tenants'
  keys (security audit, Hardening).
- **FU-7.** The read filter is validated only when written. Intersecting it with the live
  member set at read time would also clean out ids of soft-deleted agents, which removal
  cleanup does not touch (security audit, Hardening).
- **FU-8.** `AgentGroupDetailView.vue` now holds the members table, add flow, privacy
  toggle and the read-filter editor. Extract the editor as a `MemberReadsModal`
  component (quality audit).
- **FU-9.** Pre-existing in touched code (quality audit):
  - the agent_groups service imports infrastructure directly;
  - the knowledge repository joins agent_groups and agents tables directly;
  - `is_armed()` with the private `agent_fs_gc._TRUTHY` now has five copies across
    `smap.maintenance`;
  - `graphrag_repositories.py` is about 950 lines.
- **FU-10.** `AgentGroupFacade.list_members` has no production caller left; both routes
  use `list_members_with_reads`.
- **FU-11.** A replace build resets evidence and provenance per build, but the upsert
  still keeps the higher of the old and new confidence and the earliest and latest
  seen-at stamps (`neo4j_driver._build_upsert_cypher`). A full rebuild therefore keeps
  ranking signals from evidence it no longer holds. This predates this dossier: the
  Knowledge Map replace path behaves the same.
- **FU-12.** The per-message provenance subquery is evaluated for every joined
  `chatroom_agents` row before `DISTINCT ON` collapses them: O(messages x members in the
  room) on a full rebuild of a large group. Distinct first, or compute room membership
  once per room.
