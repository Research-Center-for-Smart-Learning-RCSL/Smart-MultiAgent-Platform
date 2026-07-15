---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.01, R11.11, R11.14, R11.25]
---

# F-14: Knowledge Map builder-key update can collide with attached consumer keys

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-14).
Release blocker — routes through `/check-security` before merge (audit FU-1).

## 1. Summary

The SMAP agents context enforces a BYO-key isolation invariant: an Agent's own (consumer)
Key Group must differ from the builder Key Group of any Knowledge Map it is attached to, so
graph-builder spend and rate limits are billed to a separate owner
(`backend/contexts/agents/application/agent_service.py:266-270`, `KnowmapBuilderKeyGroupConflict`).
That guard fires only on the **Agent** side — on Agent create, attach, or consumer-key change.
The **Knowledge Map config** update path
(`backend/contexts/knowledge/application/knowmap_config_service.py:117-166`) validates only
project membership, embedding availability, and embedding dimension when it changes the map's
`builder_key_group_id`, and never looks at the Agents already attached. A designer can therefore
change a map's builder group to the exact group an attached Agent uses as its consumer group,
silently collapsing the enforced billing/rate-limit split for every attached Agent. Per the
approved remedy (Q-1), the update is **accepted** and each colliding Agent is automatically
**detached** from the map in the same transaction, restoring the invariant while preserving the
designer's builder-group intent.

## 2. Observed vs Expected

- **Observed** — `KnowmapConfigService.update`
  (`knowmap_config_service.py:117-125` signature; group-change branch `:132-149`) runs three
  validations when `builder_key_group_id` changes: `_assert_builder_group_in_project`
  (`:133`, helper `:212-221`), `_resolve_group_pin` (`:134`, helper `:223-228`), and the
  `project_pinned_dim` dimension check (`:140-145`). It then writes the new group via
  `self._configs.update(...)` (`:151`) and audits (`:154-165`). There is **no** query of the
  agents context and **no** comparison against any attached Agent's `key_group_id`. The
  builder group is stored in `knowmap_configs.builder_key_group_id`
  (`backend/contexts/knowledge/infrastructure/knowmap_tables.py:24-29`), and Agents attach via
  `agents.knowmap_config_id` (`backend/contexts/agents/infrastructure/tables.py:49-53`;
  migration `backend/alembic/versions/0048_knowmap.py:172-180`, `ON DELETE SET NULL`).
- **Expected** — the `[R11.01]` builder-vs-consumer distinctness rule applies to Knowledge Maps
  (unlike Concept Maps, exempted by `[R11.11]`, because a Knowledge Map has a *determinate*
  consumer set — its attached Agents, `[R11.14]`/`[R11.23]`). The invariant must be enforced
  symmetrically: the agent-side guard already rejects a colliding attach/re-key
  (`agent_service.py:266-270`); the config-side builder-group change must not be allowed to
  leave any attached Agent whose consumer group equals the new builder group. The SRS is
  currently silent on Knowledge Map distinctness — §11 states it only for Graph RAG generally
  (`[R11.01]`) and explicitly exempts Concept Maps (`[R11.11]`); the load-bearing basis today is
  the code's own `agent_service` invariant. This spec closes that ambiguity with an SRS Delta
  (§11, new `[R11.25]`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | On a builder-group change that collides with attached Agents: reject the update, or accept it under a detach/migration policy? | **Detach/migration policy.** Accept the change; automatically detach each colliding Agent (null its `knowmap_config_id`) in the same transaction; audit each detach and surface the detached Agent ids in the response. | User decision. A designer editing the *map* may affect many Agents; hard-rejecting would force manual cleanup of every attachment before any builder change. Detach restores the isolation invariant immediately while honoring the designer's intent. The asymmetry with the agent-side reject (`agent_service.py:266-270`) is deliberate: editing one Agent, the user can trivially pick a non-colliding group, so reject is the right UX there. |
| Q-2 | Detach vs force-rekey the colliding Agent? | **Detach** (clear the map binding), not rekey. | Force-rekey has no well-defined automatic target group and would silently change the Agent's *entire* consumer billing identity (its normal LLM calls, not just the map). Detach is the minimal, auditable action that restores the invariant; the designer can re-attach with a compatible group afterward. |
| Q-3 | Should the fix amend the SRS to state Knowledge Map builder/consumer distinctness? | **Yes** — add `[R11.25]` (§11). | The audit flagged that `[R11.01]`'s rule is written for RAG and `[R11.11]` exempts only Concept Maps, leaving Knowledge Maps ambiguous; the code enforces it but no requirement states it. Making the invariant authoritative removes the ambiguity and gives the fix a real intent source. |
| Q-4 | HTTP surface for the detach outcome? | Update returns 200 with the detached Agent ids in the response body; no error status. | Detach is a successful mutation, not a conflict rejection — 409 would be wrong under the accepted policy. The colliding case is communicated via `detached_agent_ids` so the UI can inform the designer. |

## 4. Reproduction

1. Project P has Key Groups G_build and G_consume. Create a Knowledge Map config M with
   `builder_key_group_id = G_build`.
2. Create Agent A in P with `key_group_id = G_consume` and attach it to M
   (`knowmap_config_id = M`). The agent-side guard passes because `G_consume != G_build`
   (`agent_service.py:266-270`).
3. `PATCH /api/knowmap-configs/{M}` with `builder_key_group_id = G_consume`
   (`backend/app/api/v1/knowmap.py:289-310`). If G_consume resolves to the same embedding
   provider/model/dimension (or M has no live sibling pin), the dimension check passes.
4. **Today:** the update succeeds; M is now built by G_consume while Agent A consumes it with
   the same G_consume — the enforced billing/rate-limit split is gone, with no error and no
   record. **After the fix:** the update succeeds, Agent A is detached from M
   (`knowmap_config_id` cleared), the response reports A's id in `detached_agent_ids`, and an
   audit event records the detach.

## 5. Root Cause Analysis

The causal chain:

1. `KnowmapConfigService.update`'s builder-group-change branch
   (`knowmap_config_service.py:132-149`) validates only project/embedding/dimension and never
   consults attached Agents. **This is the root cause** — the config-side mutation path has no
   knowledge of the builder-vs-consumer invariant that the agent-side path enforces.
2. The invariant lives entirely in the agents context and reads the map's builder group as a
   *fixed* value (`agent_service.py:259-270`), so nothing re-runs it when the builder group is
   what changes.
3. `agents.knowmap_config_id` has no DB-level constraint tying an Agent's `key_group_id` to the
   map's builder group (it is a bare nullable FK, `tables.py:49-53`), so the database cannot
   catch the collision either.

Correcting (1) — reconcile attached Agents against the new builder group inside the update
transaction — prevents the isolation-violating state from ever being committed.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Agent already attached to a Knowledge Map whose builder group is
  changed to that Agent's consumer group. Configs already in the collided state (from a pre-fix
  update) are *not* auto-repaired by this change; a one-time reconciliation sweep is recorded as
  FU-1.
- **Sibling suspects:**
  - **Agent-side attach/re-key guard** (`agent_service.py:422-443`, `:266-270`): CLEARED —
    already enforces the invariant on Agent create/attach/key-change. This fix adds the missing
    *config-side* half; the two must agree on the comparison (consumer `key_group_id` vs map
    `builder_key_group_id`).
  - **Concept Map builder-group update** (`graphrag_config_service.py`): CLEARED and out of
    scope — `[R11.11]` explicitly exempts Concept Maps (no determinate consumer set). Do not add
    a distinctness check there.
  - **File RAG config update** (`config_service.py:180-188`): CLEARED — File RAG configs have no
    builder Key Group (they carry only embedding/rerank pinned keys, F-1 surface); there is no
    builder-vs-consumer relationship to violate.
  - **F-13 (embedding-model swap guard)** — RELATED, separate dossier
    (`docs/tasks/2026-07-14-embedding-model-swap-guard/spec.md`). Both add validation to
    `knowmap_config_service.update`'s builder-group-change branch. Coordinate so the checks
    compose and their error precedence is defined (see §9); F-13 rejects a same-dimension model
    change, this fix detaches colliding Agents — distinct concerns on the same branch.

## 7. Fix Design

The reconciliation is an **agents-context** responsibility (that context owns the invariant, the
Agent tables, and the existing clear-knowmap semantics). The knowledge config service invokes it
through `AgentsFacade` — the sanctioned cross-context seam, symmetric to `agent_service` already
reading `KnowledgeFacade.get_knowmap_config` (`agent_service.py:259`).

1. **New agents-context reconciliation method.** Add
   `AgentsFacade.detach_agents_colliding_with_knowmap_builder(*, knowmap_config_id, new_builder_key_group_id, actor_user_id, actor_ip, request_id=None) -> list[uuid.UUID]`
   (`backend/contexts/agents/interfaces/facade.py`, delegating to `AgentService`). It selects
   Agents where `knowmap_config_id == config_id AND key_group_id == new_builder_key_group_id`
   (project-scoped), clears each such Agent's `knowmap_config_id`, and returns the detached
   Agent ids. Clearing the binding is a single-field write — the same field the Agent update
   path sets to `NULL` at `agent_service.py:468-469` — and knowmap attachment carries **no**
   dependent tool/flag state to reconcile (contrast F-18, which concerns config *deletion* and
   Agent tool state; no Knowledge Map tool exists per F-15). Add the backing repository write to
   `AgentRepository` (`backend/contexts/agents/infrastructure/repositories.py`) as a targeted
   `UPDATE agents SET knowmap_config_id=NULL, version=version+1 WHERE knowmap_config_id=:cid AND
   key_group_id=:kg AND project_id=:pid` (bump `version` so it composes with the optimistic-
   concurrency `patch` used elsewhere, `agent_service.py:502-506`), and emit one
   `agent.knowmap_detached` audit event per detached Agent attributed to the acting user. Note
   `agents.knowmap_config_id` is unindexed (`0048_knowmap.py`); scope by the indexed
   `project_id` — a dedicated index is not required for this low-cardinality config-edit path
   but is recorded as FU-2.
2. **Serialize builder-change against Agent-attach (concurrency — required).** Neither
   `KnowmapConfigService.update` nor `AgentService.patch`'s attach branch takes any lock today
   (verified: the knowledge context has no `pg_advisory`/`FOR UPDATE` at all; the agent create
   path locks on `project_id` only for the R9.01 cap, `agent_service.py:284-285`, and the
   *patch* attach branch at `:435-443` takes no lock). So a concurrent "attach Agent A (consumer
   G) to map M" and "change M's builder to G" can interleave — A's guard reads M's old builder,
   B's detach reads the not-yet-committed attach — and **both commit, leaving the collision this
   fix exists to prevent** (the same unlocked read-then-write class the audit flagged in F-11).
   Take a transaction-scoped advisory lock keyed on the **config id** in *every* path that binds
   or re-validates an Agent against that map's builder group, mirroring the create-path precedent
   (`agent_service.py:284-285`) / the orchestration helper
   (`backend/contexts/orchestration/infrastructure/repositories.py:532-535`): acquire it at the
   top of `KnowmapConfigService.update` (before reading attached Agents), and in **both**
   `AgentService.patch` (before `_assert_knowmap_config_compatible`, `agent_service.py:438`) and
   `AgentService.create` (which also validates a knowmap attach) whenever a knowmap
   attach/re-key is in play. With the lock held, whichever request commits first is fully
   observed by the second: an attach/create that loses the race sees the new builder and is
   rejected by the agent-side guard; a builder-change that loses sees the freshly-attached Agent
   and detaches it. (The create path's existing project-id lock at `:284-285` is a different key;
   the config-id lock is additional and must be acquired in a consistent order — see §9.)
3. **Wire the detach into the config update.** In `KnowmapConfigService.update`, inside the
   `new_group is not None and new_group != cfg.builder_key_group_id` branch
   (`knowmap_config_service.py:132`), after the existing project/pin/dimension validations and
   within the same request transaction (`backend/shared_kernel/db/session.py:95-114` commits at
   request end; the PATCH route does not commit mid-handler), call the new facade method with the
   resolved `new_group` and capture the detached ids. Perform the config `self._configs.update(...)`
   (`:151`) as today; both writes commit or roll back together.
4. **Surface the outcome (Q-4).** Include the detached Agent ids in the update audit metadata
   (`audit.emit`, `:154-165`). To return them to the caller, note `KnowmapConfigOut`
   (`backend/app/api/v1/knowmap.py:92-106`) is the **shared** GET/create/patch response model —
   do **not** widen its common shape. Either add an **optional** `detached_agent_ids:
   list[UUID] | None = None` field (populated only by the patch mapper, omitted elsewhere) or
   introduce a dedicated `KnowmapConfigPatchOut` for the PATCH route (`:289-310`). Either way the
   OpenAPI schema changes, so regenerate the checked-in api-client
   (`frontend/ pnpm run gen:api`) and commit the regenerated `KnowmapConfigOut.ts` /
   `KnowmapService.ts`; CI's `check:openapi-drift` (`frontend/package.json:15`) fails if skipped.
   Add the frontend i18n string and a toast in `KnowledgeMapConfigDetailView.vue`'s
   `saveMutation` success handler (`frontend/src/slices/agents/views/KnowledgeMapConfigDetailView.vue:248-258`)
   when `detached_agent_ids` is non-empty; mirror the string into `zh-TW.json`.

**Patterns to follow (SoC):** the invariant and every Agent-table write stay in the agents
context; the knowledge context only *supplies* the new builder group and *records* the returned
ids. Do not write `agents.knowmap_config_id` from the knowledge context. Watch for an import
cycle — `knowmap_config_service` importing `AgentsFacade` while `agent_service` imports
`KnowledgeFacade`; if the module-level import cycles, use a local import inside `update` (the
facades construct lazily, so this is safe).

**Data repair:** configs already in the collided state are not auto-fixed by this code change.
FU-1 records an optional one-time reconciliation sweep that detaches pre-existing colliding
attachments and reports them for owner review.

## 8. Regression Test Plan

There is no dedicated `test_knowmap_config_service.py` today — the update method's only coverage
is API-level `backend/tests/unit/test_knowmap_authz.py` plus wiring tests. Create
`backend/tests/unit/test_knowmap_config_service.py` for the config-side cases; extend
`backend/tests/unit/test_agent_service.py` for the agent-side guard. Failing-first tests (each
fails against current code, passes after the fix), with a fake/in-memory agents facade or a
seeded test DB:

1. **Config-side detach (primary red-first)** — `knowmap_config_service.update` changing
   `builder_key_group_id` to a group equal to an attached Agent's `key_group_id` detaches that
   Agent (its `knowmap_config_id` becomes NULL), the update succeeds, and the returned
   `detached_agent_ids` contains the Agent id. Fails today (update succeeds, Agent stays attached
   with colliding groups).
2. **Selective detach** — with two attached Agents, one whose consumer group equals the new
   builder group and one whose does not, only the colliding Agent is detached; the other remains
   attached. Fails today (neither is touched).
3. **No collision, no detach** — a builder-group change to a group no attached Agent consumes
   leaves all attachments intact and `detached_agent_ids` empty.
4. **Audit metadata** — a detach emits an `agent.knowmap_detached` audit event whose metadata
   names the detached Agent id(s) and contains no key secret (CLAUDE.md).
5. **Agent-side guard unchanged** — the existing `agent_service` attach/re-key rejection
   (`KnowmapBuilderKeyGroupConflict`) still fires; the config-side fix does not alter it
   (extend `backend/tests/unit/test_agent_service.py`).
6. **Concurrency invariant (seeded-DB, serialized)** — a race between an Agent-attach and a
   builder-group-change on the same map must never leave a collision. A pure race is
   non-deterministic to assert directly, so verify the guarantee's mechanism: (a) assert both
   `KnowmapConfigService.update` and `AgentService.patch`'s attach branch acquire the config-id
   advisory lock (spy/interception), and (b) run the two operations serialized in each order
   against a seeded DB and assert the post-state holds the invariant (loser is either rejected or
   detached). Fails today (no lock; interleaving leaves a collision).

## 9. Risks and Rollback

- **Silent knowledge loss** — a detached Agent loses its Knowledge Map source. Mitigated by Q-4:
  the response and audit report every detached Agent id so the designer is informed and can
  re-attach with a compatible group. This is the explicit trade-off of the chosen migration
  policy over hard-reject.
- **Cross-context write ordering / transaction** — the facade detach and the config update must
  share one transaction so a later failure rolls both back. Verified: the PATCH request runs in a
  single `db_session()` unit of work that commits only after the handler returns
  (`session.py:95-114`); the route does not commit mid-handler (`knowmap.py:289-310`). Do not
  introduce an intermediate commit.
- **Concurrency (addressed, not merely noted)** — the advisory lock (§7.2) is load-bearing for
  the security guarantee; without it the fix has a live race. Define a single lock-acquisition
  order (the config-id lock is the only advisory lock either path takes, and neither nests it
  under another, so deadlock risk is low) and confirm the agents create path's separate
  project-id lock (`agent_service.py:284-285`) does not interleave to form a cycle.
- **Import cycle** — see §7 patterns; use a local import if the module-level one cycles.
- **Interaction with F-13** — both mutate the same builder-group-change branch. Define precedence:
  F-13's embedding-model-change rejection (a hard 409) should run **before** this detach logic —
  a rejected update must not detach any Agent. Sequence the two dossiers and add a composition
  test if both land.
- **Rollback** — revert the dossier's commits and the SRS Delta. The new facade/repo method and
  the additive response field are additive; no schema migration, so rollback restores prior
  behavior. Agents detached under the new rule stay detached (data already changed) — acceptable
  and reversible by manual re-attach.

## 10. Acceptance Criteria

- [x] AC-1: The six regression tests in §8 fail before the fix and pass after.
  (`test_knowmap_config_service.py` covers cases 1-4 + 6; `test_agent_service.py` covers case 5.)
- [x] AC-2: Changing a Knowledge Map's `builder_key_group_id` to a group equal to any attached
  Agent's consumer `key_group_id` detaches exactly those colliding Agents (their
  `knowmap_config_id` cleared) in the same transaction as the config update; non-colliding
  attachments are untouched.
- [x] AC-3: The update response includes `detached_agent_ids` (empty when there is no collision),
  and the detach is recorded in audit metadata with the affected Agent id(s) and no key secret.
- [x] AC-4: All Agent-table writes occur in the agents context via `AgentsFacade`; the knowledge
  context performs no direct write to `agents.knowmap_config_id` (SoC preserved).
- [x] AC-5: The `KnowledgeMapConfigDetailView` surfaces a designer-visible notice (i18n, EN +
  zh-TW) when `detached_agent_ids` is non-empty.
- [x] AC-6: The SRS Delta (§11, `[R11.25]`) is applied to `REQUIREMENTS.md` at approval.
- [x] AC-7: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/` (no new mypy errors vs baseline — see F-15 FU-3); `pnpm lint` / `pnpm typecheck`
  pass in `frontend/`.
- [x] AC-8: `/check-security` review passes for the enforced builder/consumer key-isolation
  boundary (audit FU-1) — covered by the consolidated Step 6 pass over the combined diff.
- [x] AC-9: `KnowmapConfigService.update` and both agent attach paths (`AgentService.patch` and
  `AgentService.create`) acquire the config-id advisory lock, so a concurrent attach/create and
  builder-group-change cannot interleave into a persisted collision (§8.6).
- [x] AC-10: The checked-in api-client is regenerated (`pnpm run gen:api`) and committed so
  `check:openapi-drift` passes (verified: re-export yields no drift); `KnowmapConfigOut`'s shared
  GET/create shape is not broadened — a dedicated `KnowmapConfigPatchOut` subclass carries the
  new field.

## 11. SRS Delta

Add to `REQUIREMENTS.md` §11.5 (Knowledge Map), after `[R11.24]`:

- **[R11.25]** Unlike a Concept Map ([R11.11]), a Knowledge Map has a determinate consumer set —
  the Agents attached to it via the per-Agent allowlist ([R11.12], [R11.23]) — so the [R11.01]
  builder-vs-consumer distinctness rule **applies**: a Knowledge Map's builder Key Group must
  differ from the consumer Key Group of every Agent attached to it. The invariant is enforced on
  both mutation paths. On the Agent side, an attach or consumer-key change that would collide is
  rejected. On the Knowledge Map side, a builder Key Group change that would collide with
  already-attached Agents is accepted and each colliding Agent is automatically **detached** from
  the map (its `knowmap_config_id` cleared) within the same transaction; every detach is
  audit-logged with the affected Agent id and reported to the caller.

## 12. Deviation Log

- **D-1** — §7.1 specified the detach write as `UPDATE agents SET knowmap_config_id=NULL,
  version=version+1 ...`. The manual `version=version+1` was **dropped**: the `agents` table has a
  `smap_bump_version` BEFORE-UPDATE trigger, so any real column change auto-increments `version`.
  Setting it by hand would double-bump. The `AgentRepository.detach_from_knowmap_config` write
  sets only `knowmap_config_id=NULL` and lets the trigger bump the version (repository code must
  never touch `version` — a project-wide invariant). The optimistic-concurrency intent of §7.1 is
  preserved (version still advances on each detached row).
- **D-2** — the reconciliation method signature gained a `project_id` parameter not shown in §7.1's
  facade signature (`detach_agents_colliding_with_knowmap_builder(*, knowmap_config_id,
  new_builder_key_group_id, project_id, actor_user_id, actor_ip, request_id=None)`). §7.1's own
  SQL scopes the write by `project_id` (the indexed column; `knowmap_config_id` is unindexed), so
  the value must reach the repository. The knowledge config service already holds it as
  `cfg.project_id` and passes it through. Purely additive; no behavior change.
- **D-3** — Q-4/§7.4 offered two sanctioned response shapes (optional field on the shared
  `KnowmapConfigOut`, or a dedicated patch model). Chose the **dedicated `KnowmapConfigPatchOut`
  subclass** so the shared GET/create shape stays exactly as-is (AC-10's "not broadened"). Not a
  deviation from the spec — an explicit selection between its offered options, recorded for clarity.

## 13. Follow-ups

- **FU-1**: optional one-time reconciliation sweep that finds and detaches pre-existing colliding
  attachments (Agents whose consumer group already equals their attached map's builder group from
  a pre-fix update) and reports them for owner review.
- **FU-2**: an index on `agents.knowmap_config_id` if the attached-Agent lookup becomes a hot path
  (today it is a low-frequency config-edit path, project-scoped on the indexed `project_id`).
- **FU-3 (coordinate with F-13)**: both fixes add validation to the Knowledge Map builder-group
  update branch; sequence them so F-13's rejection precedes this detach and the error precedence
  is covered by a composition test.
