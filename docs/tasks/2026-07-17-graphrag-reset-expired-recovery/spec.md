---
type: bugfix
status: in-progress
created: 2026-07-17
requirements: [R11.04, R11a.02]
---

# Admin reset treats expired recovery material as successful compensation

## 1. Summary

This dossier remediates F-6 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md` and the
shared read-safety sibling in reconciliation. When a stuck graph build outlives the 24-hour
Redis recovery TTL, default `admin_reset` treats a missing pointer/snapshot as a successful
discard and publishes readable `IDLE`
(`backend/contexts/knowledge/application/graphrag_config_service.py:535-605`).

- **Goal:** fail closed when compensation material is unavailable and make irrecoverable
  graph state explicit and unreadable for both Concept and Knowledge Maps.
- **Non-goals:** extend Redis retention, persist snapshots in Postgres, or remove the
  documented explicit `force=true` admin override. Adding project/config scoping to admin
  reset authorization is also out of scope (FU-2) — this task preserves the current
  platform-admin check without widening or narrowing it.

## 2. Observed vs Expected

- **Observed:** with a pointer but no snapshot, `delete_by_build` still runs and only the
  `restore_from_snapshot` call is skipped by the `if snapshot is not None` guard
  (`backend/contexts/knowledge/application/graphrag_config_service.py:554-556`), so
  `comp_error` stays `None`. The build's nodes are removed while the pre-build subgraph is
  never restored: the graph loses pre-build state. Reset then deletes the (absent) snapshot
  and clears the pointer (`:564-565`), destroying the evidence that material was missing,
  sets `IDLE` with `error=None` (`:592-596`), and audits `outcome="discarded"` (`:599-600`) —
  indistinguishable from a real rollback. With no pointer it clears the absent pointer and
  audits `outcome="noop"` (`:566-568`, `:601-602`). The same function *does* refuse when the
  Neo4j driver is unavailable (`:549-553`), so stores-down and material-missing are handled
  asymmetrically. The Redis pointer and snapshot are two independently expiring keys written
  by two separate `SET ... EX` calls with the same 24-hour constant
  (`backend/contexts/knowledge/infrastructure/redis_lock.py:131-135,171`;
  `backend/contexts/knowledge/application/graphrag_builder.py:65,282-292`); the pointer is
  written after the snapshot and therefore always outlives it.
- **Expected:** [R11.04] forbids inconsistent failure state (`REQUIREMENTS.md:456-469`), and
  [R11a.02] says default reset returns 5xx with no state change/material loss when
  compensation cannot complete (`REQUIREMENTS.md:470-471`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Leave the old in-flight state or add a terminal state? | Default reset leaves the state unchanged and returns 503; the reconciler maps proven terminal loss to shared `recovery_unavailable`, which is non-retryable and read-blocked. | This preserves [R11a.02]'s no-state-change reset contract while preventing endless reconciliation and avoiding readable ordinary `FAILED` (`backend/tests/unit/test_graphrag_retrieve.py:186-205`). |
| Q-2 | Gate every `FAILED` graph? | No. | Phase-1 rollback/no-op failures and successful compensation can safely serve the last good graph; only provably irrecoverable partial state needs the new gate. |
| Q-3 | What does `force=true` do? | Preserve [R11a.02]: it may force `IDLE` with non-null error and `compensation_failed`/`compensation_unavailable` audit after explicit admin acceptance. | The task closes the default false-success path without silently changing the documented escape hatch. |
| Q-4 | Native PG enum `ALTER TYPE ... ADD VALUE`, or `sa.Text` + CHECK? | Convert `last_build_state` on both `graphrag_configs` and `knowmap_configs` to `sa.Text` + CHECK and drop the `graphrag_build_state` type. | `0056_skills.py:12-15` records that this backend has **no** `ALTER TYPE ... ADD VALUE` precedent and that growing value sets use Text + CHECK, citing `project_embedding_pins.kind` (0052). Build state is such a growing set. PostgreSQL cannot remove an enum value, so the additive route's downgrade would require rebuilding the shared type and remapping both tables anyway. |
| Q-5 | The new state is in no retry set and in no buildable whitelist — how does a config escape it? | Both: (a) admit the state to the manual-build and engine whitelists but **not** the auto-trigger whitelist or the reconciler sweep; (b) add a Knowledge Map `admin_reset` mirroring the Concept Map one. | Without (a) the state is a permanent wedge: `graphrag_triggers.py:23`, `app/api/v1/graphrag.py:524`, and `graphrag_builder.py:216-222` all whitelist `{IDLE, FAILED}` only, and the reconciler selects by exact state equality (`graphrag_repositories.py:756-770`), so nothing would ever pick it up. Manual rebuild is a deliberate human act and is the right escape; auto-triggers must not silently rebuild over known-inconsistent data. Without (b) the wedge is unrecoverable for Knowledge Maps specifically, because `admin_reset` exists only on `GraphRagConfigService` — `knowmap_config_service.py` has no equivalent and there is no knowmap reset route. |
| Q-6 | Does "unreadable" cover graph visualization, or only turn-context retrieval? | Both. | The single existing gate (`graphrag_retrieve.py:112-113`) covers only turn-context retrieval. `read_graph` (`app/api/v1/graphrag.py`) and `read_knowmap_graph` (`app/api/v1/knowmap.py:427-449`) have no build-state check, and a visualization consumer seeing rollback-intended facts is exactly what §7's security rationale forbids. |

## 4. Reproduction

1. Leave a config in `NEO4J_COMMITTED` or `FAILED_COMPENSATING` with an in-flight build.
2. Expire/evict its snapshot while retaining the pointer, or expire both Redis keys.
3. Call admin reset with `force=false`.
4. Observe no 503: reset sets `IDLE`, clears what remains, and reads are permitted.

Current reset tests cover compensation errors but not missing recovery material
(`backend/tests/unit/test_graphrag_reset.py:185-253`).

## 5. Root Cause Analysis

Reset uses pointer presence as the only signal that compensation is required and treats a
missing snapshot as a valid delete-only rollback. Deleting rows created by the current build
cannot restore pre-existing relations overwritten by that build. The reconciler detects
missing material (`backend/contexts/knowledge/application/graphrag_reconciler.py:422-438`)
but finalizes it as ordinary readable `FAILED`, so the state model cannot distinguish safe
failure from irrecoverable inconsistency. The root correction requires both outcome
classification and read gating.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** Concept Map admin resets after pointer/snapshot expiry or Redis loss.
- **Confirmed sibling:** reconciliation routes missing recovery material to ordinary
  `FAILED` (`backend/contexts/knowledge/application/graphrag_reconciler.py:422-438,497-537`),
  affecting both Concept and Knowledge Maps.
- **Cleared:** transient compensation exceptions retain material and the current stuck state
  for retry (`backend/contexts/knowledge/application/graphrag_reconciler.py:439-465`).
- **Existing debt:** reset and reconciler duplicate discard logic, which allowed their
  missing-material behavior to drift. The frontend keeps a hand-maintained duplicate of the
  build-state union (`frontend/src/slices/agents/api/index.ts:94-108`, `GraphragBuildState`
  plus `GRAPHRAG_IN_PROGRESS`) that slice code uses instead of the generated
  `shared/api-client/models/BuildState.ts:5`; both must be updated. Admin reset performs no
  project/config scoping (FU-2).
- **Surfaces the new state must be classified against** (each is a hand-listed set; none is
  derived from the enum, so none fails loudly on omission except where noted):
  `IN_FLIGHT_BUILD_STATES` (`backend/contexts/knowledge/domain/graphrag.py:91-97`),
  `_STUCK_STATES` (`backend/contexts/knowledge/application/graphrag_reconciler.py:68-72`),
  `_BUILDABLE_STATES` (`backend/contexts/knowledge/application/graphrag_triggers.py:23`),
  the manual-build gate (`backend/app/api/v1/graphrag.py:524`), the engine gate
  (`backend/contexts/knowledge/application/graphrag_builder.py:216-222`), the Prometheus
  label map (`backend/app/workers/tasks/graphrag.py:367-377`, whose `.get(..., "idle")`
  default would silently report an unrecoverable config as healthy), the frontend socket
  whitelist (`frontend/src/slices/agents/composables/useBuildStateSocket.ts:19-20`, which
  would drop `build.state` events for the new state), and the two exhaustive `Record` maps
  in `frontend/src/slices/agents/lib/graphragBuildState.ts:10-26` (these *do* fail loudly —
  omission is a typecheck error).

## 7. Fix Design

1. Add `recovery_unavailable` to `BuildState`
   (`backend/contexts/knowledge/domain/graphrag.py:73-79`). Per Q-4, migrate
   `graphrag_configs.last_build_state` and `knowmap_configs.last_build_state` from the shared
   native `graphrag_build_state` ENUM to `sa.Text` + CHECK, drop that type, and update both
   table modules (`graphrag_tables.py:29-43`, `knowmap_tables.py:43-57`) accordingly. The
   `BuildState(row.last_build_state)` coercion in both repositories
   (`graphrag_repositories.py:101`, `knowmap_repositories.py:50`) keeps rejecting unknown
   values. Expose the state through API/OpenAPI, regenerated client, both frontend unions
   (generated and hand-maintained), UI labels, and both locale files.
2. Add the state to `IN_FLIGHT_BUILD_STATES` but not to `_STUCK_STATES`. Both constants carry
   reciprocal comments asserting they are "the in-flight/uncommitted trio"
   (`graphrag.py:88-90`, `graphrag_reconciler.py:62-67`); this is the deliberate divergence
   those comments anticipate, so update both comment blocks — they become factually wrong
   otherwise. Ordinary `FAILED` remains readable.
3. Per Q-6, apply the same gate to the graph visualization reads `read_graph`
   (`backend/app/api/v1/graphrag.py`) and `read_knowmap_graph`
   (`backend/app/api/v1/knowmap.py:427-449`), which have no build-state check today.
4. Extract a small application-layer discard primitive returning `discarded`, `failed`, or
   `unavailable`. For an in-flight prior state, both build id and snapshot are mandatory;
   absence performs no delete-only rollback and clears no recovery material.
5. Default reset audits `compensation_unavailable`, commits only that audit, leaves the
   existing in-flight state/material unchanged, and raises the existing reset-compensation
   503 (mapped at `backend/contexts/knowledge/interfaces/error_mapping.py:89-93`). When a
   build id is known, include it in audit.
6. Route reconciler no-pointer/no-snapshot outcomes to `recovery_unavailable` rather than
   ordinary `FAILED`. `_finalize_failed` (`graphrag_reconciler.py:498-539`) hardcodes
   `BuildState.FAILED` at `:516` while taking a free-text `outcome`; give it an explicit
   state parameter and update its three call sites (`:325`, `:432`, `:490`) so the
   `rolled_back` path keeps landing in ordinary `FAILED`. It also publishes the state over
   the websocket (`:519-521`), so the new state reaches the UI live.
7. Per Q-5, admit the state to the manual-build gate (`app/api/v1/graphrag.py:524`) and the
   engine gate (`graphrag_builder.py:216-222`) but **not** to `_BUILDABLE_STATES`
   (`graphrag_triggers.py:23`) — an explicit human rebuild is the escape; automatic triggers
   must not rebuild over known-inconsistent data. Note `graphrag_build_job_id`
   (`graphrag_triggers.py:26-44`) embeds `last_build_state.value` in the arq dedup nonce.
8. Per Q-5, add `admin_reset` to `KnowmapConfigService`
   (`backend/contexts/knowledge/application/knowmap_config_service.py`) and an admin route
   mirroring `app/api/v1/graphrag.py:626-652`, reusing the same platform-admin check, the
   same audit shape, and the same 503 error. This restores the documented `force=true`
   escape for Knowledge Maps, which have none today.
9. Add the state to the Prometheus label map (`app/workers/tasks/graphrag.py:367-377`) — its
   `.get(..., "idle")` default would otherwise report an unrecoverable config as healthy,
   the exact class of bug the audit-M2 comment at `:355-358` exists to prevent — and to the
   frontend socket whitelist (`useBuildStateSocket.ts:19-20`), which would otherwise drop
   `build.state` events for it.
10. Preserve clean `IDLE` idempotence, lock serialization, successful discard, transient
    retry, and explicit `force=true` semantics.

Reuse `IN_FLIGHT_BUILD_STATES` (`backend/contexts/knowledge/domain/graphrag.py:91-97`), the
existing reset error (`backend/contexts/knowledge/domain/errors.py:104-115`) and its existing
503 mapping, `_emit_reset_audit` (`graphrag_config_service.py:643-673`, which already records
`previous_state`, `build_id`, `forced`, and `outcome` and never writes error text or snapshot
content), reconciler rollback/audit machinery, `publish_build_state`
(`graphrag_events.py:25-57`), and the `Text` + CHECK migration shape from
`project_embedding_pins.kind` (0052). The reset fakes need no extension:
`FakeSnapshotStore` (`backend/tests/unit/test_graphrag_reset.py:99-116`) already models the
pointer and the snapshot as two independent constructor fields, so missing-snapshot
(`current=build_id, snapshot=None`) and missing-pointer (`current=None`) are both expressible
today, and the audit-assertion pattern at `:270-273`/`:336-339` is established.

### Security Considerations

This remains admin-only and must preserve current project/config authorization. The new state
prevents authorized consumers from seeing uncommitted or rollback-intended facts. Audit must
not include snapshot contents, graph facts, Redis values, or secrets.

## 8. Regression Test Plan

1. In `backend/tests/unit/test_graphrag_reset.py`, cover pointer present/snapshot missing and
   pointer missing for both in-flight states: default reset returns 503, performs no Neo4j
   delete/restore, clears no material, and never writes `IDLE`.
2. Extend reconciler unavailable tests in `backend/tests/unit/test_graphrag_builder.py` to
   expect `recovery_unavailable`.
3. Extend `backend/tests/unit/test_graphrag_retrieve.py` to assert the new state makes no
   Qdrant/Neo4j read for either graph product while ordinary `FAILED` stays readable. The
   gate is a single shared code path (`graphrag_retrieve.py:112-113`) reached by both
   providers, so follow the existing shared-gate test at
   `backend/tests/unit/test_knowmap_context_provider.py:117,199,216`.
4. Cover the visualization read block for `read_graph` and `read_knowmap_graph` (Q-6).
5. Cover the escape hatch (Q-5): the new state is rejected by auto-triggers, accepted by
   manual rebuild and the engine, and reset-able on both products — including the new
   Knowledge Map `admin_reset` route's platform-admin authorization.
6. Cover the migration (both tables, CHECK accepts the new value, downgrade path), API
   serialization/generated types, the Prometheus label map, the frontend socket whitelist,
   UI label/i18n, clean reset, and `force=true` compatibility.

## 9. Risks and Rollback

The new state touches database, backend, generated client, and UI.

**Migration (Q-4).** Converting two columns off a shared native ENUM is the largest single
risk here. `graphrag_build_state` is used by exactly two columns and nothing else, and both
carry `server_default 'idle'::graphrag_build_state`, so the conversion must drop the server
default, alter the column type with an explicit `USING ... ::text`, re-add a plain `'idle'`
default plus the CHECK, and only then `DROP TYPE`. The migration must be forward-compatible
per `backend/CLAUDE.md` (old code runs on new schema): old code reading a `Text` column still
gets the same strings, and old code never writes `recovery_unavailable`. Downgrade re-mints
the type and must remap any `recovery_unavailable` rows to a conservative blocked state
(`failed_compensating`) before the cast, since that value will not exist in the re-minted
type. This is a one-way concern in practice: after downgrade those configs return to being
reconciler-visible, which is the safe direction.

**Behavior.** Existing unavailable configs become visibly blocked rather than falsely
healthy — an intended, user-visible change. Because the state is deliberately outside both
the reconciler sweep and the auto-trigger whitelist, the manual rebuild and the (new, for
Knowledge Map) admin reset are the only ways out; Q-5 exists so that this is a designed
property rather than a wedge. Verify both escapes before closing AC-6.

**Scope.** The Knowledge Map `admin_reset` route is new API surface, so it needs the same
platform-admin check, OpenAPI regeneration, and client regeneration as the existing one.

## 10. Acceptance Criteria

- [x] AC-1: Missing-pointer and missing-snapshot default-reset tests fail before the fix and
  pass after. Verified: the 9 new tests in `test_graphrag_reset.py` failed with
  `DID NOT RAISE GraphRagResetCompensationFailed` before the fix and pass after
  (commit `060d522`).
- [x] AC-2: Default reset of an in-flight config without complete recovery material returns
  503, never sets `IDLE`, performs no delete-only rollback, and clears no material. Verified
  by `test_missing_snapshot_refuses_and_touches_nothing` and
  `test_missing_pointer_on_in_flight_state_refuses`, both parametrised over all three
  in-flight states.
- [x] AC-3: Default reset leaves the previous state unchanged; audit records
  `compensation_unavailable`, forced=false, previous state, and build id when known, without
  snapshot/error secrets. Verified by
  `test_unavailable_audit_metadata_is_distinct_and_carries_no_secrets` (asserts the exact
  metadata key set) and `test_missing_pointer_audit_records_null_build_id`.
- [x] AC-4: `recovery_unavailable` is terminal, not endlessly retried, and blocks both the
  turn-context retrieval path and the graph visualization reads for Concept and Knowledge
  Maps; ordinary safe `FAILED` remains readable on both. Verified by the extended
  `test_query_gated_in_transient_build_states` (retrieval, a single shared code path both
  products reach) and the new/extended `test_graphrag_graph_endpoint.py` and
  `test_knowmap_graph_endpoint.py`, each parametrised over the whole read-blocked set and
  over readable states including `FAILED`. Terminality is pinned by the second-sweep
  assertion in AC-5's test.
- [x] AC-5: Reconciler missing-pointer/snapshot outcomes use the new state, while the
  successful-rollback path still lands in ordinary `FAILED`. Verified by
  `test_no_snapshot_compensation_audits_unavailable_not_rolled_back` (now asserts
  `RECOVERY_UNAVAILABLE` and that a second sweep emits nothing) and the unchanged
  `test_successful_compensation_finalizes_and_audits_rolled_back`, which still asserts
  `FAILED`. `_finalize_failed` takes an explicit `state` so the three terminal paths can
  no longer share one verdict by accident.
- [ ] AC-6: Clean/reset-success idempotence, build-lock behavior, transient compensation
  retry, and documented `force=true` behavior remain unchanged.
- [ ] AC-7: Migration, API/client generation, UI/i18n, focused tests, lint, format,
  typecheck, and frontend build pass.
- [x] AC-8: `last_build_state` is `Text` + CHECK on both config tables, the
  `graphrag_build_state` type is gone, the CHECK accepts exactly the seven states, and the
  downgrade remaps `recovery_unavailable` rows to `failed_compensating` before re-minting
  the type. Verified against a live PostgreSQL 16 on a full upgrade -> downgrade -> upgrade
  round trip: both columns became `text` with `'idle'::text` default and the type was
  dropped (`pg_type` count 0); the CHECK accepted `recovery_unavailable` and rejected
  `bogus_state`; the downgrade logged `remapped 1 graphrag_configs row(s)`, restored the
  6-value enum, and left no orphan CHECK. `alembic check` reports zero drift for
  `last_build_state` and `build_state_valid` (the report's other entries are pre-existing
  partition/index noise unrelated to this change).
- [x] AC-9: A config in `recovery_unavailable` is not selected by the reconciler sweep and
  not auto-built by triggers, but IS accepted by manual rebuild and by the build engine.
  Verified by `test_engine_gate_admits_recovery_unavailable` (parametrised over all six
  states, asserting refused states are left untouched), `test_auto_triggers_refuse_recovery_unavailable`,
  and `test_recovery_unavailable_is_read_blocked_but_not_swept`, which pins
  `_STUCK_STATES` as a strict subset of `IN_FLIGHT_BUILD_STATES` so a future edit cannot
  silently re-couple them.
- [ ] AC-10: Knowledge Map exposes an `admin_reset` with the same platform-admin
  authorization, audit shape, 503 semantics, and `force=true` behavior as the Concept Map
  one; a Knowledge Map config can be driven out of `recovery_unavailable` by it.
- [ ] AC-11: The Prometheus `graphrag_build_state` gauge reports the new state distinctly
  rather than defaulting it to `idle`, and the frontend socket whitelist accepts
  `build.state` events carrying it.

## 11. SRS Delta

None. This restores the existing [R11.04] and [R11a.02] contract.

## 12. Deviation Log

- D-1: Made `backend/alembic.ini` ASCII-only (commit `4178661`), which the spec did not
  call for. Alembic reads that file with `encoding="locale"`
  (`alembic/util/compat.py:87`), so the em-dash on line 1 and the section sign on line 14
  raised `UnicodeDecodeError` on any non-UTF-8 locale — `alembic` would not start at all on
  the zh-TW Windows (cp950) machine this was built on, and neither `PYTHONUTF8=1` nor
  `-X utf8` helps because `locale.getencoding()` ignores UTF-8 mode. This blocked AC-8's
  contract gate outright, so it was fixed here rather than deferred. Agreed with the user
  before the change; the alternative offered was to leave it and verify AC-8 on CI instead.
- D-3: Applying "the same gate" to the visualization routes (§7.3) blocks the whole of
  `IN_FLIGHT_BUILD_STATES`, not only the new state. `read_graph` and `read_knowmap_graph`
  previously served the graph in `running` / `neo4j_committed` / `failed_compensating`
  too, so this is a user-visible change beyond the new state: the visualizer now shows an
  empty graph during a build instead of a half-committed one. That follows the section's
  wording and Q-6's rationale (a viewer seeing rollback-intended facts is the thing being
  prevented), and it makes the visualizer agree with turn-context retrieval, which has
  refused those states since F-10. Narrowing the gate to `recovery_unavailable` alone
  would be a one-line change if the mid-build view turns out to be wanted.
- D-2: `plan_discard`'s outcome enum lives in
  `contexts/knowledge/application/graphrag_config_service.py` rather than in a new shared
  module. §7.4 called for "a small application-layer discard primitive"; keeping it beside
  its only caller avoids a module whose sole purpose is to be shared with a reconciler path
  that FU-4 records as still separate. Revisit if FU-4 is taken.

## 13. Follow-ups

- FU-1: Evaluate durable snapshot storage only if incidents show the 24-hour compensation
  window itself is insufficient; it is not needed for truthful fail-closed behavior.
- FU-2: Admin reset authorizes on `principal.is_admin` alone
  (`backend/app/api/v1/graphrag.py:640-643`) with no project/config scoping, so any platform
  admin can reset any config in any project. Pre-existing and out of scope here; the new
  Knowledge Map route deliberately mirrors the existing check rather than diverging, so both
  can be tightened together.
- FU-3: Deduplicate the frontend build-state union — slice code uses the hand-maintained
  `GraphragBuildState` (`frontend/src/slices/agents/api/index.ts:94-108`) rather than the
  generated `BuildState` (`frontend/src/shared/api-client/models/BuildState.ts:5`), so every
  state change must be made twice.
- FU-4: Reset and reconciler still duplicate discard logic even after the shared primitive in
  §7.4; the reconciler's `_finalize_failed` and the reset path remain separate call graphs.
- FU-6: `project_embedding_pins`' CHECK carries a name mismatch: migration 0052 created
  `ck_project_embedding_pins_kind`, but `embedding_pin_tables.py:37-40` passes that same
  string as `sa.CheckConstraint(name=...)` on metadata that applies the
  `ck_%(table_name)s_%(constraint_name)s` convention, so the ORM renders
  `ck_project_embedding_pins_ck_project_embedding_pins_kind`. Autogenerate would propose
  dropping and recreating it. Found while picking a non-colliding name for this task's
  CHECK; pre-existing, so not fixed here.
- FU-5: `plan_discard` only requires a snapshot when the prior state is in
  `IN_FLIGHT_BUILD_STATES`, per §7.4. A *settled* config (`idle`/`failed`) that still carries
  a stale current-build pointer with no snapshot therefore keeps the historical behaviour:
  `delete_by_build` runs with no restore. That shape is only reachable after a crash between
  the build's completion and its pointer clear, and closing it means deciding whether a
  pointer on a settled config is evidence of live data at all — out of scope here, but it is
  the same destructive primitive this task fixed for the in-flight case.
