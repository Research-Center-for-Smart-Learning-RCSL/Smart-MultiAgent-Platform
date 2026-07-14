---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.04]
supersedes:
---

# F-26: `admin_reset` forces IDLE without compensating 2PC external state

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-26).

## 1. Summary

`GraphRagConfigService.admin_reset` sets a GraphRAG config's build state to `IDLE` and emits
an audit record, but does nothing else. It never releases the Redis build lock, deletes the
per-config snapshot, clears the current-build pointer, or compensates a half-written Neo4j
subgraph — all of which the reconciler's terminal paths do. Because `_STUCK_STATES` excludes
`IDLE`, a config forced to `IDLE` while its external stores are inconsistent becomes permanently
invisible to the reconciler sweep: the orphan Neo4j triples are never compensated, the snapshot
lingers until its 24h TTL, the current pointer stays stale, and a still-held build lock blocks
rebuilds for up to 10 minutes — all while the config advertises itself as terminally healthy.
The fix makes `admin_reset` run the reconciler's terminal discard/compensation synchronously
(roll the wedged Neo4j build back to its snapshot, delete the snapshot, clear the pointer,
force-release the lock) *before* forcing `IDLE`, and record the outcome truthfully rather than
unconditionally claiming success.

## 2. Observed vs Expected

- **Observed** — `admin_reset` reads the config, captures `prev = cfg.last_build_state`, calls
  `set_state(state=IDLE, error=None)`, and audits `admin.graphrag_reset` with `previous_state`;
  it then re-reads and returns (`backend/contexts/knowledge/application/graphrag_config_service.py:395-428`).
  The service holds only `self._db` and `self._configs`
  (`:55-57`) — no lock store, snapshot store, or Neo4j driver — so it cannot and does not touch
  any external state. `_STUCK_STATES = (FAILED_COMPENSATING, NEO4J_COMMITTED, RUNNING)`
  excludes `IDLE`, `QDRANT_COMMITTED`, and `FAILED`
  (`backend/contexts/knowledge/application/graphrag_reconciler.py:61-65`), and the sweep only
  iterates those states (`:147-148`). Called from `POST /api/admin/graphrag/{id}/reset`
  (`backend/app/api/v1/graphrag.py:608-626`, which constructs `GraphRagConfigService(db)`
  directly).
- **Expected** — a forced reset leaves the config's external stores consistent with the `IDLE`
  state it advertises: any in-flight build is discarded (Neo4j rolled back to the pre-build
  snapshot, snapshot deleted, current pointer cleared, lock released), so the config is genuinely
  idle and rebuildable — and if compensation cannot complete, the config is not falsely marked
  healthy. Intent: [R11.04] (transactional consistency). R11a.02 documents only the state reset,
  not the un-cleared external state.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the compensation run? | **Inline synchronous cleanup** — `admin_reset` runs the reconciler's terminal discard/compensation before forcing IDLE. | Chosen over enqueuing a reconciler pass. An admin resetting a wedged config expects an immediate, fully-consistent result; an async pass leaves the config briefly non-IDLE and eventually-consistent, and (per this finding) IDLE is not even a swept state, so a naive "set a swept state and wait" would still need extra wiring. Inline reuses the reconciler's proven discard logic and returns a truthful terminal state in one call. Trade-off: external-store calls run in the reset request path (bounded, admin-only, infrequent). |
| Q-2 | Roll the in-flight build **forward** (retry Qdrant) or **discard** it (roll Neo4j back)? | **Discard** — roll Neo4j back to the snapshot and drop the build. | `admin_reset` is a *force reset*, not a heal; the safe, predictable outcome is to abandon the wedged build and return to the last consistent state, mirroring the reconciler's `_rollback` terminal path (`graphrag_reconciler.py:350-397`) rather than its Qdrant-retry recovery path. |
| Q-3 | What if compensation's external calls fail (store unreachable)? | Do **not** force IDLE-as-healthy; surface the failure and leave the config in a reconciler-visible state. | Forcing IDLE on failed compensation would re-introduce F-7's "failure recorded as success, made unrecoverable" defect. On compensation failure the config must stay swept-eligible (e.g. `FAILED_COMPENSATING`) and the reset must report failure so the admin retries. Cross-refs F-7 (§6). |
| Q-4 | How is the lock released, given the token-checked release? | Add an unconditional admin **force-release** to the lock store (`DEL graphrag:lock:{config_id}`). | `RedisBuildLockStore.release` is token-checked and only the acquiring worker instance holds the token (`backend/contexts/knowledge/infrastructure/redis_lock.py:85-89`); the reset process cannot release it that way. A stale lock otherwise blocks rebuilds for up to `LOCK_TTL_S` (10 min). A privileged force-release is appropriate for an explicit admin reset. |

## 4. Reproduction

1. A Concept or Knowledge Map config is wedged in `FAILED_COMPENSATING`: this build's triples
   are in Neo4j, Qdrant lacks them, the snapshot and current pointer exist, and the build lock
   may still be held.
2. An admin calls `POST /api/admin/graphrag/{id}/reset`.
3. Observed: state becomes `IDLE`; the config now reports healthy and is excluded from the
   reconciler sweep (`_STUCK_STATES` has no `IDLE`). The orphan Neo4j triples are never
   compensated, the snapshot survives to its 24h TTL, the pointer is stale, and a rebuild is
   blocked until the lock's TTL expires.

Deterministic given a config in a stuck state.

## 5. Root Cause Analysis

`admin_reset` performs a **Postgres-only state write** and skips all external-store
compensation (`graphrag_config_service.py:406-410`); the service was never wired with the
lock/snapshot/Neo4j ports needed to compensate (`:55-57`). The root cause is that forcing
`IDLE` decouples the advertised state from the external stores and simultaneously removes the
config from reconciler visibility (`_STUCK_STATES` excludes `IDLE`,
`graphrag_reconciler.py:61-65`), so nothing ever reconciles it afterward. The correct
compensation logic already exists but is method-bound to `ReconciliationLoop` (`_rollback`
`:350-397`, `_resolve_build_id` `:405-424`, `_clear_current` `:399-403`) with injected ports;
it is not reachable from the service today.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — any Concept or Knowledge Map (both products share this engine) that an admin
  resets out of `FAILED_COMPENSATING`, `NEO4J_COMMITTED`, or `RUNNING`: silently inconsistent
  graph vs vector store, leaked snapshot, stale pointer, and a rebuild-blocking lock — presented
  as healthy.
- **Sibling suspects:**
  - **F-7 (related, separate dossier).** The reconciler's `_rollback` swallows Neo4j failures and
    still records terminal success (`graphrag_reconciler.py:350-397`) — the same
    "failure-as-success" hazard this fix must avoid on the `admin_reset` path. F-26's Q-3
    failure handling is deliberately the opposite of F-7's current behavior; the shared
    compensation primitive introduced here should be designed so F-7's fix can make both paths
    truthful.
  - **Reconciler terminal paths (cleared — the exemplar).** `_reconcile_one`/`_rollback`
    (`:267-397`) are the correct discard/compensation logic to reuse; not defects.
  - **Knowledge Map `admin_reset` equivalent (verify).** Confirm whether Knowledge Map configs
    have their own admin-reset entry point or share this one; if a separate reset exists, it has
    the same gap and must receive the same fix (the engine is shared, so a shared compensator
    covers both). Marked for the implementer to confirm.

## 7. Fix Design

Reuse the reconciler's terminal discard/compensation from `admin_reset` via a shared primitive,
and record the outcome truthfully.

1. **Extract a reusable discard primitive.** Factor the reconciler's terminal Neo4j
   compensation into a reusable application-layer routine — e.g.
   `discard_inflight_build(config_id, build_id)` — that performs: resolve snapshot; if present,
   `neo4j.delete_by_build(config_id, build_id)` then `restore_from_snapshot`
   (`graphrag_reconciler.py:363-370`); `snapshots.delete(config_id, build_id)`;
   `snapshots.clear_current(config_id)`; and lock force-release. Refactor `_rollback`
   (`:350-397`) to call it (its only extra step being `set_state(FAILED, ...)`), so the reconciler
   and `admin_reset` share one compensation implementation. Keep it in the knowledge application
   layer, wired with the same ports the reconciler injects (SoC).
2. **Wire ports into `admin_reset`.** Obtain the Neo4j driver, snapshot store, and lock store —
   constructed from `get_settings()` mirroring the existing static
   `cascade_external_stores`/`purge_config_external_stores`
   (`graphrag_config_service.py:444-523`), or injected — so `admin_reset` can call the discard
   primitive.
3. **`admin_reset` sequence.** Resolve `build_id` (mirror `_resolve_build_id`
   `graphrag_reconciler.py:405-424`); run `discard_inflight_build`; on success `set_state(IDLE,
   error=None)` and audit `admin.graphrag_reset` with `previous_state`, resolved `build_id`, and
   a compensation `outcome` (e.g. `discarded`/`noop`). If there is no in-flight build (no snapshot
   / no pointer), the primitive is a safe no-op that still force-releases any stale lock and
   clears the pointer defensively, then IDLE — idempotent.
4. **Add lock force-release.** Add a `force_release(config_id)` to the `BuildLockStore` port and
   `RedisBuildLockStore` — an unconditional `DEL graphrag:lock:{config_id}`
   (`backend/contexts/knowledge/infrastructure/redis_lock.py:44-45,85-89`) — used only by the
   discard primitive. It is safe here because `admin_reset` is an explicit force operation.
5. **Truthful failure (Q-3).** If Neo4j rollback or another compensation step fails, do **not**
   `set_state(IDLE)`; leave the config in `FAILED_COMPENSATING` (reconciler-visible) and raise a
   5xx so the admin retries, auditing `outcome=compensation_failed`. This keeps the fix from
   re-creating F-7 on the reset path.

**Scope boundary:** Qdrant orphan points minted by a discarded build (the builder uses per-build
`uuid4` point IDs) are not addressed here — that is the F-8/F-9 territory; recorded as FU-1.

**Reuse inventory:**
- `ReconciliationLoop._rollback` / `_resolve_build_id` / `_clear_current`
  (`graphrag_reconciler.py:350-424`) — the logic to extract and share.
- `RedisSnapshotStore` (`get`/`delete`/`clear_current`) and `RedisBuildLockStore`
  (`redis_lock.py:102-204,60-99`); `Neo4jDriver.delete_by_build`/`restore_from_snapshot`.
- Port construction pattern in `cascade_external_stores` (`graphrag_config_service.py:444-490`).

**Data repair:** for configs already forced to IDLE by the old behavior, a one-off admin
re-reset after this fix ships (or a targeted reconcile) will discard their lingering
snapshot/pointer and compensate Neo4j; note in the deploy runbook rather than a migration.

## 8. Regression Test Plan

Extend `backend/tests/unit/test_graphrag_reset.py` (which today asserts only that state becomes
IDLE and one audit row is written, with fakes that have no lock/snapshot/Neo4j interaction):

1. **Reset of a `FAILED_COMPENSATING` config compensates (red-first)** — with fake
   snapshot/lock/Neo4j stores holding an in-flight build, `admin_reset` calls
   `delete_by_build` + `restore_from_snapshot`, deletes the snapshot, clears the current
   pointer, force-releases the lock, and then sets IDLE. Fails today (none are called).
2. **Idempotent reset of a clean config** — no snapshot/pointer present: no compensation errors,
   still force-releases a stale lock defensively, sets IDLE.
3. **Compensation failure is not advertised healthy** — Neo4j `restore_from_snapshot` raises;
   `admin_reset` does **not** set IDLE, leaves the config in a `_STUCK_STATES` state, and
   raises/reports failure (cross-ref F-7).
4. **Audit metadata** — the audit record carries `previous_state`, the resolved `build_id`, and a
   compensation `outcome`.

Primary red-first: (1).

## 9. Risks and Rollback

- **Reusing reconciler internals.** Extracting the compensation must not change reconciler
  behavior; the reconciler tests in `backend/tests/unit/test_graphrag_builder.py`
  (`test_reconciler_exhausted_rolls_back` `:859`, recovery tests `:697-963`) must stay green.
- **External-store calls in the request path.** Bounded (admin-only, rare); a slow/unreachable
  store surfaces as a 5xx per Q-3 rather than a false success.
- **Force-release safety.** `force_release` deletes another instance's lock unconditionally; safe
  only because it runs inside the explicit force-reset. Keep it out of the normal build path.
- **F-7 coupling.** F-26 must not depend on F-7 landing first, but the shared primitive should be
  written so F-7's fix can make both callers truthful. If F-7 lands separately, re-verify the
  shared primitive's failure semantics.
- **Rollback** — revert to the Postgres-only `admin_reset`; no schema/data migration. The
  extracted primitive is inert if unused.

## 10. Acceptance Criteria

- [ ] AC-1: The compensating-reset test (§8.1) fails before the fix and passes after.
- [ ] AC-2: `admin_reset` on a config with an in-flight build discards it — Neo4j rolled back to
  snapshot, snapshot deleted, current pointer cleared, build lock force-released — before setting
  IDLE.
- [ ] AC-3: `admin_reset` is idempotent on a clean/idle config and still force-releases any stale
  lock and clears any stale pointer.
- [ ] AC-4: When compensation cannot complete, `admin_reset` does not set IDLE, leaves the config
  reconciler-visible, and reports failure (no false "healthy").
- [ ] AC-5: The audit record includes `previous_state`, resolved `build_id`, and compensation
  `outcome`.
- [ ] AC-6: Existing reconciler behavior/tests are unchanged by the extraction.
- [ ] AC-7: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`.

## 11. SRS Delta

None — restores [R11.04] transactional consistency for the admin-reset path; R11a.02's
state-reset description is unchanged, only completed.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (F-8/F-9, Qdrant orphans):** a discarded build's Qdrant points (per-build `uuid4` IDs)
  are not swept by this fix; addressed by the F-8/F-9 idempotency/orphan work.
- **FU-2 (F-7 truthful compensation):** the reconciler's `_rollback` still records swallowed
  Neo4j failures as terminal success; the shared discard primitive introduced here should be the
  place F-7's fix makes both callers truthful.
- **FU-3 (facade hop):** the admin reset endpoint instantiates `GraphRagConfigService(db)`
  directly (`graphrag.py:619`) rather than via `KnowledgeFacade`; if the fix adds new
  dependencies, consider routing through the facade to match the project's import rules.
