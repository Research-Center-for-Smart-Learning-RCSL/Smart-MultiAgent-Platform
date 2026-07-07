---
type: refactor
status: implemented
created: 2026-07-07
requirements: [R11.05, R11.07, R11.08]
---

# GraphRAG Phase 1 — Decouple to typed-FK owner + agent_group (behavior-preserving)

Parent blueprint: `docs/tasks/2026-07-07-graphrag-two-axis-redesign/spec.md` (Phase 1).
Depends on Phase 0 (`docs/tasks/2026-07-07-graphrag-phase0-engine-cleanup/spec.md`): the
repo Port + `ConfigLike`, the `KnowledgeFacade.list_graph_configs_for_agent` method, the
`embed_resolution` helper, and the `evidence_refs` neutral type are assumed in place.

## 1. Summary

Replace the 1:1 `graphrag_configs.agent_id` ownership with the typed-FK discriminated owner
model, using a singleton `agent_group` per former agent so observable behavior is identical.
Done as **expand -> migrate -> contract** so every milestone leaves the tree green:

1. **Expand** — create `agent_groups` / `agent_group_members`; add the owner columns
   (`owner_kind` + three typed FK columns + CHECK + partial unique indexes) alongside the
   still-present `agent_id`; backfill one singleton group per config and set its owner.
2. **Migrate** — switch every reader (retrieval, triggers, config create/update validation)
   from `agent_id` / the `agents.graphrag_config_id` reverse pointer to the membership
   resolver; keep the agent-centric create UX by mapping `agent_id -> ensure-singleton-group`.
3. **Contract** — drop `graphrag_configs.agent_id` (+ its UNIQUE/FK), drop the reverse
   pointer `agents.graphrag_config_id` (+ FK), remove their domain/DTO/frontend footprint,
   delete the now-moot R11.01 tests, regenerate the API client.

## 2. Motivation

Named debt (parent §4.3, §4b-2): the conversation graph is welded to one agent by three
schema anchors — `graphrag_configs.agent_id UNIQUE NOT NULL`
(`graphrag_tables.py:21-27`, `0013_graphrag.py:52-54`), the single-valued reverse pointer
`agents.graphrag_config_id` + `fk_agents_graphrag_config` (`agents/tables.py:50`,
`0013_graphrag.py:80-87`), and the agent-keyed config lookup
(`graphrag_repositories.py:100-119` `list_for_agents`). This prevents a graph from being
owned by anything but one agent and is the coupling the whole two-axis redesign must break.
Phase 1 breaks it while preserving behavior; wider ownership (chatroom/workspace, multi-member
groups, layered retrieval) rides on top in Phase 2b.

## 3. Non-goals

- **No externally observable behavior change to retrieval or builds** — the singleton group
  reproduces exactly today's per-agent scope (§6 proof). Documented, intended API/DTO changes:
  `GraphRagConfigOut.agent_id -> owner_*` fields, and removal of `agent_id`/`graphrag_config_id`
  from the Agent DTOs (both were internal-wiring fields; the reverse pointer was already dead
  after decoupling — parent §4b-2-D).
- **No public `agent_group` CRUD / UI.** In Phase 1 groups are internal singleton substrate,
  auto-managed; users still create/see Concept Maps per agent. Multi-member groups + owner
  picker are Phase 2b/4.
- **No new owner kinds populated.** The schema gets all three owner FK columns + the full
  CHECK/indexes (so Phase 2b adds no migration), but only `agent_group` rows are written here.
- **No provider fan-out / layering / temporal / windowing** — Phase 2a/2b/3.
- **No engine de-concreting or cleanup contract** — those are Phase 0.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Add all three owner FK columns now or only `agent_group`? | All three columns + `owner_kind` enum (3 values) + full CHECK + 3 partial unique indexes; populate `agent_group` only. | Phase 2b then adds behavior only, no schema migration. Empty chatroom/workspace indexes are free. |
| Q-2 | Backfill group name? | Synthetic `graphrag-owner-{agent_id}`. | `agents.name` is unique only among live agents (partial index `0011:102-105`); a soft-deleted + live same-name pair would collide on `agent_groups(project_id,name)`. `agent_id` is globally unique and stable. |
| Q-3 | Backfill configs whose agent is soft-deleted? | Yes — backfill every config; never filter on the *agent's* `deleted_at`. | Filtering on the agent would leave a live config with a NULL owner, violating the owner CHECK. The synthetic name avoids collisions; the member FK is CASCADE. |
| Q-4 | How do users create a new Concept Map in Phase 1 without group CRUD? | Create API keeps `agent_id`; the service ensures a singleton `agent_group` for that agent and sets it as owner. | Preserves the agent-centric create UX + `GraphRagConfigCreateIn` contract; defers the owner picker to Phase 4. |
| Q-5 | Refactor or feature? | Refactor (expand-migrate-contract) with documented DTO changes. | Behavior is preserved; new tables are internal substrate, not a user feature. |

## 5. Current vs Target Structure

### Schema (owner shape — exemplar `0042_prompt_studio.py:27-88`, `projects` XOR `0002:78-81`)

New tables (templates: `agent_group_members` <- `chatroom_agents`
`conversation/tables.py:50-70`; `agent_groups` <- `rag_configs`
`knowledge/tables.py:14-48` + `0012:88-91`):
```
agent_groups(id PK, project_id FK->projects CASCADE NOT NULL, name TEXT NOT NULL,
             created_at, deleted_at)
  partial unique: uq_agent_groups_project_name_active ON (project_id,name) WHERE deleted_at IS NULL
agent_group_members(agent_group_id FK->agent_groups CASCADE, agent_id FK->agents CASCADE,
                    PRIMARY KEY(agent_group_id, agent_id))   -- no role column
```
`graphrag_configs` gains (via `CREATE TYPE owner_kind AS ENUM('chatroom','agent_group','workspace')`):
```
owner_kind owner_kind NULL (expand) -> NOT NULL (contract)
owner_chatroom_id     UUID NULL FK->chatrooms CASCADE
owner_agent_group_id  UUID NULL FK->agent_groups CASCADE
owner_workspace_id    UUID NULL FK->workspaces CASCADE
CHECK ck_graphrag_configs_owner: exactly one owner_* non-null matching owner_kind (0042 _SCOPE_CHECK style)
partial unique per kind: uq_graphrag_configs_owner_{chatroom,agent_group,workspace}
```
Dropped in contract: `graphrag_configs.agent_id` (+ auto `graphrag_configs_agent_id_key`,
`graphrag_configs_agent_id_fkey`); `agents.graphrag_config_id` (+ `fk_agents_graphrag_config`).
Keep `ix_graphrag_configs_project`.

### Resolver (the one logical change, behavior-preserving)

`GraphRagConfigRepository.list_for_agents(agent_ids)` (`graphrag_repositories.py:100-119`) —
**keep the signature**, swap the SQL body to the membership join:
`graphrag_configs JOIN agent_groups ON id=owner_agent_group_id JOIN agent_group_members
ON agent_group_id=agent_groups.id WHERE member.agent_id IN (:ids) AND deleted_at IS NULL`.
For a singleton group this returns exactly the config `WHERE agent_id IN (:ids)` returned (§6).

### Retrieval / triggers

- Retrieval `turn_engine._graphrag_context` (`:1666-1671`): resolve via
  `KnowledgeFacade.list_graph_configs_for_agent(agent.id)` (from Phase 0), pass `configs[0].id`
  (or `None`) to the unchanged `GraphRagContextProvider.query`. `agent.id` is in scope;
  `chatroom_id` not needed.
- Triggers `turn_engine.py:1272-1281` and `app/api/v1/messages.py:300-319`: **no change** —
  they call `evaluate_graphrag_message_triggers(agent_ids=...)`, whose only agent->config hop
  is the redefined `list_for_agents`.

### Config-service validation (key-group delta, verified §D)

Drop the builder-vs-consumer distinctness check in all three sites (each read the agent's
`key_group_id`, which an `agent_group` owner lacks): `graphrag_config_service.py:76-79`
(create), `:181-184` (update), and the entire `agent_service._assert_graphrag_config_compatible`
(`:227-261`) + its call sites (`:301-306` create, `:424-430` patch). Keep the
`builder_key_group_id`-in-project check (`config_service.py:81-95`, `:185-199`). Create:
map `draft.agent_id -> ensure_singleton_agent_group(project_id, agent_id) -> owner_agent_group_id`.

### Reverse-pointer removal footprint (parent §4b-2-D, re-confirmed)

Backend: `agents/domain/models.py:141,229,241`; `repositories.py:53,119,141`; the
`agent_service` attach/clear branches; DTOs `app/api/v1/agents.py:75,106,127,150,219,314,324`.
Config DTO: `GraphRagConfigOut.agent_id` (`graphrag.py:86,132-137`) -> owner fields;
`GraphRagConfigCreateIn.agent_id` kept (Q-4). Frontend (minimal dead-usage removal, full
re-home is Phase 4): `GraphragConfigListView.vue:130-131` `isBound` badge + `:441-455`;
`AgentDetailView.vue` graphrag form control + `:597-602,:621-625`; `schemas.ts:32`;
regenerated `shared/api-client/models/Agent*.ts` + `GraphRag*.ts`.

## 6. Characterization Test Plan

Behavior to pin before the contract step:
- **Resolver equivalence** — a test asserting `list_for_agents([a])` returns the same config
  id via the membership join as it did via `agent_id` for a singleton group (the core
  behavior-preservation guarantee). New integration test over the Phase-0 tables.
- **Retrieval** — `test_graphrag_retrieve.py` + a turn-level test that a bound agent still
  gets its graph block; assert `_graphrag_context` resolves the same config.
- **Triggers** — `test_graphrag_triggers.py` (fake `list_for_agents`, asserts
  `seen_agent_ids == [agent_id]`, signature preserved) and `test_message_wakeup_dispatch.py:85-117`
  stay valid; add a membership-join integration test.
- **Migration** — a test asserting: post-backfill every config has exactly one singleton
  group + member = former `agent_id`, `owner_kind='agent_group'`, and the CHECK/partial-unique
  hold; down-migration restores `agent_id` from the singleton member.
- Tests to **delete/rewrite** (assert dropped behavior): `test_agent_service.py:488-587` (4
  R11.01 reverse tests), `test_agent_config_project_guard.py:84-161` (builder-key-group-conflict
  cases `:111-117,:154-161`), config-service `GraphRagBuilderKeyGroupConflict` assertions.

## 7. Migration Steps (expand -> migrate -> contract)

**M1 — Expand (migration `0043_graphrag_owner`, down_revision `0042_prompt_studio`).**
`CREATE TYPE owner_kind`; create `agent_groups` + `agent_group_members` (+ partial unique);
add the three owner FK columns (nullable) + `owner_kind` (nullable this step) + CHECK
(allow NULL owner_kind during expand, or add CHECK in contract) + three partial unique
indexes. Backfill via the `0036_agent_tools.py:116-179` row-loop idiom (`op.get_bind()` +
`sa.table` handles): per config, `INSERT agent_groups(... name='graphrag-owner-'||agent_id)
RETURNING id` -> `INSERT agent_group_members` -> `UPDATE graphrag_configs SET
owner_agent_group_id=..., owner_kind='agent_group'`. Update ORM bindings (`graphrag_tables.py`
add owner cols; new `agent_group` tables module). Green: both `agent_id` and `owner_*` valid.

**M2 — Migrate reads.** Swap `list_for_agents` SQL to the membership join; add/repoint the
`KnowledgeFacade.list_graph_configs_for_agent` impl; edit `turn_engine._graphrag_context` to
resolve via the facade; config-service: drop the distinctness checks, add
`ensure_singleton_agent_group` on create. Green: all logic uses owner; `agent_id` still exists
but unread for resolution.

**M3 — Contract (migration `0044_graphrag_drop_agent_id`).** Set `owner_kind NOT NULL` + add
the CHECK if deferred; drop `graphrag_configs.agent_id` (+ its unique/FK), drop
`fk_agents_graphrag_config` then `agents.graphrag_config_id`. Remove the domain fields, the
`agent_service` attach/clear machinery + `_assert_graphrag_config_compatible`, the DTO fields
(`GraphRagConfigOut.agent_id -> owner`, Agent DTOs drop `graphrag_config_id`); `pnpm run
gen:api`; minimal frontend dead-usage removal (isBound badge, graphrag form control); delete/
rewrite the moot tests (§6). Green.

Each milestone is independently `git revert`-able; M1 and M3 are separate alembic revisions.

## 8. Risks and Rollback

- **Backfill correctness is the highest risk.** A wrong join or a missed config leaves a live
  config with a NULL owner (CHECK violation aborts the migration — fail-loud, good). Mitigate
  with the migration test (§6) on a seeded DB before staging. Down-migration must reconstruct
  `agent_id` from the singleton member.
- **Soft-deleted-agent configs** (Q-3): backfilled with a group whose member points at a
  soft-deleted agent — harmless (member FK CASCADE), but assert the migration doesn't skip them.
- **Staging** (`smap.rcsl.online`) has live configs + graph data; the migration only rewrites
  Postgres ownership columns (Neo4j/Qdrant keyed on `graphrag_config_id`, unchanged) — no graph
  data moves. Run M1 and M3 as distinct deploys with a soak between.
- **DTO/gen:api ripple** — the AgentOut/GraphRagConfigOut changes need `gen:api` +
  `check:openapi-drift` green and the frontend dead-usage removal in the same M3 commit or the
  frontend build breaks.
- Rollback: `git revert` per milestone; `alembic downgrade` for M1/M3 (down-migrations
  specified; reversible against freshly-migrated singleton data per parent §10).

## 9. Acceptance Criteria

- [x] AC-1: `graphrag_configs` has `owner_kind` + three typed owner FK columns + a CHECK
  (exactly one, matching kind) + three partial unique indexes; `agent_id` and its unique/FK are
  gone; `agents.graphrag_config_id` + `fk_agents_graphrag_config` are gone. (0043 + 0044 written;
  ORM `graphrag_tables.py`/`agents/tables.py` match. Live DB assertion is CI-gated — see §12.)
- [x] AC-2: `agent_groups` + `agent_group_members` exist (composite PK, CASCADE legs; partial
  unique on `(project_id,name)`). (0043 + `contexts/agent_groups/infrastructure/tables.py`.)
- [x] AC-3: Backfill created exactly one singleton `agent_group` per config (member = former
  `agent_id`, `owner_kind='agent_group'`), including configs of soft-deleted agents; no config
  has a NULL owner. (0043 `_backfill_singleton_groups`; live-DB invariant assertion CI-gated.)
- [x] AC-4: `list_for_agents` returns, via the membership join, the identical config a bound
  agent resolved before — retrieval and both trigger paths are behavior-preserved.
  (`test_graphrag_owner_resolution.py` wiring test written; unit trigger tests green. Live-DB
  equivalence run is CI-gated.)
- [x] AC-5: Creating a Concept Map with `agent_id` still works: the service ensures a singleton
  `agent_group` and sets it as owner; `GraphRagConfigCreateIn` contract unchanged.
- [x] AC-6: The builder-vs-consumer key-group distinctness check is removed from all three
  sites; the `builder_key_group_id`-in-project check remains; `_assert_graphrag_config_compatible`
  and its call sites are deleted.
- [x] AC-7: `agents.graphrag_config_id` has no remaining reader (grep) — domain, repo, service,
  DTO, and frontend `isBound`/form usages removed; `Agent*` TS models updated (D-4 — full
  `gen:api` deferred); the moot R11.01 tests deleted/rewritten.
- [x] AC-8: backend `ruff`/`mypy`/`pytest` (1233 unit) green after each milestone; frontend
  `typecheck`/`build` green + touched tests pass. Pre-existing gate debt (lint warnings, flaky
  Landing test, pre-existing mypy errors) documented in FU-F, not introduced here.

## 10. SRS Delta

None — R11.05/R11.07/R11.08 already exist (parent blueprint). Phase 1 realizes the
agent_group slice of them; behavior is preserved.

## 11. Deviation Log

- **D-1 — `GraphRagConfigOut.agent_id` kept as a derived compat field** (approved
  mid-build). §5 called for replacing `GraphRagConfigOut.agent_id` with `owner_*` fields.
  Instead the Out DTO keeps `agent_id` (and `GraphRagConfigCreateIn.agent_id` per Q-4),
  derived from the singleton owner group's member, so the frontend config list keeps its
  agent column with zero behavior change; owner-based UI is deferred to Phase 4.
- **D-2 — the domain `GraphRagConfig.agent_id` is kept (derived), not replaced by owner
  fields.** §7 M3 implied the domain dataclass would drop `agent_id` and gain owner fields.
  Two findings during build forced keeping a derived `agent_id`: (a) the build worker
  `app/workers/tasks/graphrag.py:151` loads conversation history via `cfg.agent_id`
  (`_DbDeltaLoader`), so the owning agent must remain resolvable; (b) D-1's Out DTO needs
  it. Implemented as a scalar subquery over `agent_group_members` in the repo
  (`_member_agent_id()`/`_config_select()`), typed `uuid.UUID` since a Phase-1 singleton
  group always has exactly one member. Owner fields were NOT added to the domain dataclass
  (YAGNI — nothing in Phase 1 reads them off the domain object; repos read the owner
  columns directly). Owner-on-domain lands in Phase 2b when a consumer exists.
- **D-3 — removed the now-dead `GraphRagBuilderKeyGroupConflict` /
  `GraphRagConfigOutOfProject` error classes + mappers** from both the agents and knowledge
  contexts. §6 only specified deleting the check and its tests; removing the unraisable
  error classes/mappers is the logical completion (no remaining raiser after M2/M3).
- **D-4 — `openapi.json` full regen deferred; scoped model-only delta applied.** The
  committed `backend/openapi.json` was last regenerated 2026-06-29 and is ~3900 lines /
  ~8 days stale from unrelated backend work (Phase 0, prompt studio 0042, etc.). A full
  `gen:api` would sweep those unrelated API changes into this task's commit, violating
  commit discipline. Instead the three generated `Agent*` TS models were hand-edited with
  exactly the delta `gen:api` would emit for this change (drop `graphrag_config_id`).
  `check:openapi-drift` is pre-existing red independent of Phase 1 (see FU-D).

### Self-audit fixes (defects introduced then caught+fixed within this task)

- **SA-1 (`101a9f9`) — `list_for_agents` compile crash.** The derived-`agent_id`
  scalar subquery auto-correlated away its own `agent_group_members` inside
  `list_for_agents` (whose outer query joins that table), leaving the subquery with no
  FROM -> `InvalidRequestError` at statement-compile. Blast radius was the core message
  path (`messages.py` / `turn_engine` trigger dispatch + retrieval + agent-delete cascade),
  not GraphRAG-only. Fixed with `.correlate(t.graphrag_configs)`; verified by compiling all
  call-site statements. Missed by unit tests (they stub the repo); the real-SQL wiring test
  is DB-gated.
- **SA-2 (`448ca9b`) — concurrent owner-group create returned 500 not 409.** See the fix
  commit: `_ensure_singleton_agent_group` now maps the group-insert `IntegrityError` to
  `GraphRagConfigAlreadyExists`. Regression test added.

## 12. Follow-ups

- FU-A: Public `agent_group` CRUD + membership management + the owner picker UI — Phase 2b/4.
- FU-B: The frontend `isBound`/binding lexicon is removed here as dead; the owner-centric
  Concept Map surfaces replace it in Phase 4.
- FU-C: `GraphRagConfigCreateIn` still takes `agent_id` as a convenience alias in Phase 1; when
  the owner picker lands (Phase 4) the create contract gains explicit owner fields and `agent_id`
  becomes a compatibility shim to retire.
- FU-D: Regenerate `backend/openapi.json` + the full frontend client to clear the ~8-day
  accumulated drift (see D-4). Independent repo-hygiene task; unblocks `check:openapi-drift`.
- FU-E: `GraphragConfigListView.vue` still enforces the create-time UX mirror of the removed
  builder-vs-consumer distinctness (`needSecondKeyGroup` warning + consumer-key-group
  exclusion in the builder picker). Now over-restrictive but harmless; retire with the owner
  picker (Phase 4).
- FU-F: Pre-existing gate debt surfaced but out of scope: frontend `pnpm lint` has ~296
  warnings in untouched files; `Landing.test.ts` is flaky under jsdom canvas; backend
  `mypy .` has ~17 pre-existing errors in untouched modules; `test_wiring.py`/`seed.py` omit
  the required `effort` arg to `AgentRepository.create`.

### DB-gated verification (deferred to CI/staging — no local Postgres)

Per the approved "defer DB gates to CI" decision, these run against a real DB before merge:
- `alembic upgrade head` applies 0043 then 0044; `alembic downgrade` back through both is
  clean (0044 reconstructs `agent_id` + the reverse pointer from the singleton member; 0043
  drops the substrate).
- Backfill invariants (AC-3): every config gets exactly one singleton `agent_group`
  (member = former `agent_id`, `owner_kind='agent_group'`), including soft-deleted-agent
  configs; no config left with a NULL owner; the 0044 CHECK + partial-unique hold.
- Resolver equivalence (AC-4): `tests/wiring/test_graphrag_owner_resolution.py` asserts
  `list_for_agents` via the membership join returns the same config the legacy `agent_id`
  scope did, and an unrelated agent resolves nothing.
