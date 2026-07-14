---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.04]
---

# F-26: `admin_reset` forces IDLE without compensating 2PC external state

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-26).

## 1. Summary

`GraphRagConfigService.admin_reset` sets a GraphRAG config's build state to `IDLE` and emits
an audit record, but does nothing else. It never releases the Redis build lock, deletes the
per-config snapshot, clears the current-build pointer, or compensates a half-written Neo4j
subgraph — all of which the reconciler's terminal path does. Because `_STUCK_STATES` excludes
`IDLE`, a config forced to `IDLE` while its external stores are inconsistent becomes permanently
invisible to the reconciler sweep: orphan Neo4j triples are never compensated, the snapshot
lingers until its 24h TTL, the current pointer stays stale, and a still-held build lock blocks
rebuilds for up to 10 minutes — all while the config advertises itself as terminally healthy.
The fix makes `admin_reset` perform the reconciler's discard sequence synchronously — via the
same infrastructure store ports — before forcing `IDLE`: roll the wedged Neo4j build back to its
snapshot, delete the snapshot, clear the pointer, and force-release the lock. If compensation
cannot complete, the config is left reconciler-visible and the reset reports failure rather than
falsely claiming health.

## 2. Observed vs Expected

- **Observed** — `admin_reset` reads the config, captures `prev = cfg.last_build_state`, calls
  `set_state(state=IDLE, error=None)`, audits `admin.graphrag_reset` with `previous_state`, then
  re-reads and returns (`backend/contexts/knowledge/application/graphrag_config_service.py:395-428`).
  The service holds only `self._db` and `self._configs` (`:55-57`) — no lock store, snapshot
  store, or Neo4j driver — so it touches no external state. `_STUCK_STATES =
  (FAILED_COMPENSATING, NEO4J_COMMITTED, RUNNING)` excludes `IDLE`, `QDRANT_COMMITTED`, and
  `FAILED` (`backend/contexts/knowledge/application/graphrag_reconciler.py:61-65`), and the sweep
  only iterates those states (`:147-148`). Called from `POST /api/admin/graphrag/{id}/reset`
  (`backend/app/api/v1/graphrag.py:608-626`, which constructs `GraphRagConfigService(db)`
  directly, `:619`).
- **Expected** — a forced reset leaves the config's external stores consistent with the `IDLE`
  state it advertises: any in-flight build is discarded (Neo4j rolled back to the pre-build
  snapshot, snapshot deleted, current pointer cleared, lock released), so the config is genuinely
  idle and rebuildable — and if compensation cannot complete, the config is not falsely marked
  healthy. Intent: [R11.04] (transactional consistency). R11a.02 documents only the state reset,
  not the un-cleared external state.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the compensation run? | **Inline synchronous cleanup** — `admin_reset` runs the discard sequence before forcing IDLE. | Chosen over enqueuing a reconciler pass. An admin resetting a wedged config expects an immediate, fully-consistent result; an async pass leaves the config briefly non-IDLE and eventually-consistent, and (per this finding) `IDLE` is not a swept state, so a naive "set a swept state and wait" would still need extra wiring. Inline reuses the same store operations the reconciler already performs and returns a truthful terminal state in one call. Trade-off: short-lived external-store clients in the reset request path (bounded, admin-only, infrequent) — the exact pattern `cascade_external_stores` already uses on config delete. |
| Q-2 | Roll the in-flight build **forward** (retry Qdrant) or **discard** it (roll Neo4j back)? | **Discard** — roll Neo4j back to the snapshot and drop the build. | `admin_reset` is a *force reset*, not a heal; the safe, predictable outcome is to abandon the wedged build and return to the last consistent state, mirroring the reconciler's `_rollback` terminal path (`graphrag_reconciler.py:350-397`) rather than its Qdrant-retry recovery path (`_reconcile_one`). |
| Q-3 | What if compensation's external calls fail (store unreachable)? | Do **not** force IDLE-as-healthy; keep the recovery material and leave the config swept-eligible. | Forcing IDLE (and deleting the snapshot) on failed compensation is exactly F-7's "failure recorded as success, made unrecoverable" defect. On failure `admin_reset` must leave the snapshot/pointer intact and the config in a reconciler-visible state, and report failure so the admin retries. Cross-refs F-7 (§6). |
| Q-4 | How is the lock released, given the token-checked release? | Add an unconditional admin **force-release** to the lock store (`DEL graphrag:lock:{config_id}`). | `RedisBuildLockStore.release` is token-checked and only the acquiring worker instance holds the token (`backend/contexts/knowledge/infrastructure/redis_lock.py:85-89`); the reset process cannot release it that way. A stale lock otherwise blocks rebuilds for up to `LOCK_TTL_S` (10 min). A privileged force-release is appropriate for an explicit admin reset. |

## 4. Reproduction

1. A Concept Map config is wedged in `FAILED_COMPENSATING`: this build's triples are in Neo4j,
   Qdrant lacks them, the snapshot and current pointer exist, and the build lock may still be
   held.
2. An admin calls `POST /api/admin/graphrag/{id}/reset`.
3. Observed: state becomes `IDLE`; the config now reports healthy and is excluded from the
   reconciler sweep (`_STUCK_STATES` has no `IDLE`). The orphan Neo4j triples are never
   compensated, the snapshot survives to its 24h TTL, the pointer is stale, and a rebuild is
   blocked until the lock's TTL expires.

Deterministic given a config in a stuck state.

## 5. Root Cause Analysis

`admin_reset` performs a **Postgres-only state write** and skips all external-store compensation
(`graphrag_config_service.py:406-410`); the service was never wired with the lock/snapshot/Neo4j
ports needed to compensate (`:55-57`). The root cause is that forcing `IDLE` decouples the
advertised state from the external stores *and* simultaneously removes the config from reconciler
visibility (`_STUCK_STATES` excludes `IDLE`, `graphrag_reconciler.py:61-65`), so nothing ever
reconciles it afterward. The correct discard steps already exist as store operations the
reconciler's `_rollback` performs (`get`/`delete_by_build`/`restore_from_snapshot`/`delete`/
`clear_current`, `:357-385`) — they are simply unreachable from the service, which holds no ports.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — any Concept Map that an admin resets out of `FAILED_COMPENSATING`,
  `NEO4J_COMMITTED`, or `RUNNING`: silently inconsistent graph vs vector store, leaked snapshot,
  stale pointer, and a rebuild-blocking lock — presented as healthy.
- **Sibling suspects:**
  - **Knowledge Map has NO admin-reset endpoint (verified — F-26 is graphrag-only).** A
    repo-wide search finds exactly one reset: `admin_reset` behind `POST
    /api/admin/graphrag/{id}/reset` (`graphrag.py:608-626`; openapi
    `admin_reset_api_admin_graphrag__config_id__reset_post`). Knowledge Map configs share the 2PC
    engine but are healed only by the reconciler's `_knowmap_loop`
    (`backend/app/workers/graphrag_reconciler.py:172-208`) — there is no forced-IDLE path to
    hide them from the sweep. So this fix does not extend to knowmap; a future knowmap
    admin-reset must not repeat the gap (recorded FU-4).
  - **F-7 (related, separate dossier).** `_rollback` swallows Neo4j
    `delete_by_build`/`restore_from_snapshot` failures (`graphrag_reconciler.py:371-372`) and
    still deletes the snapshot and records terminal success (`:381-397`) — the exact
    "failure-as-success, made unrecoverable" hazard. F-26's Q-3 sequencing is deliberately the
    opposite; this fix must **not** replicate `_rollback`'s swallow on the admin path, and must
    **not** modify `_rollback` (that is F-7's job).
  - **Reconciler terminal path (cleared — reference).** `_rollback`/`_reconcile_one`
    (`:267-397`) are the semantic reference for the discard steps; not reused as code (§7.4).

## 7. Fix Design

`admin_reset` performs the discard sequence directly against the infrastructure store ports, in
an order that honors Q-3, then forces `IDLE`. The reconciler is not modified.

**7.1 Construct the ports (short-lived, closed in `finally`).** Mirror the per-call construction
`cascade_external_stores` already uses (`graphrag_config_service.py:459-490`): a `Neo4jAsyncDriver`
from `get_settings()` (closed in `finally`), plus the Redis stores — note **both Redis stores are
zero-arg** and build their own client: `RedisSnapshotStore()` and `RedisBuildLockStore()`
(`backend/app/workers/graphrag_reconciler.py:148,150`;
`backend/contexts/knowledge/infrastructure/redis_lock.py`). No new long-lived wiring is needed;
inject them for testability (defaulting to the real constructors) so the unit tests can pass fakes.

**7.2 Discard sequence.**
- Resolve `build_id` via `snapshot_store.get_current(config_id)` (mirror `_resolve_build_id`,
  `graphrag_reconciler.py:405-424`).
- If a `build_id` and a snapshot exist (`snapshots.get`): `neo4j.delete_by_build(config_id,
  build_id)` then `neo4j.restore_from_snapshot(config_id, snapshot)` (mirror `_rollback:361-370`).
  If either raises, **stop** — do not delete the snapshot, clear the pointer, or set IDLE (Q-3,
  §7.3). If a `build_id` is known but no snapshot exists, best-effort `delete_by_build` only
  (no restore material — matches the reconciler's degraded no-snapshot handling).
- **Only after** the Neo4j rollback succeeds (or there was nothing to discard):
  `snapshots.delete(config_id, build_id)`, `snapshots.clear_current(config_id)`,
  `locks.force_release(config_id)`, then `set_state(IDLE, error=None)`.
- Audit `admin.graphrag_reset` with `previous_state`, the resolved `build_id`, and a compensation
  `outcome` (`discarded` / `noop`).
- **No in-flight build** (no `build_id`/snapshot): defensively `force_release` the lock and
  `clear_current`, then IDLE — idempotent and safe to call repeatedly.

**7.3 Truthful failure (Q-3).** On any external compensation failure, `admin_reset` does **not**
set IDLE and does **not** delete the snapshot/pointer; it leaves the config in its current
`_STUCK_STATES` state (reconciler-visible, so the periodic sweep can still heal it) and raises so
the endpoint returns 5xx, auditing `outcome=compensation_failed`. This is the opposite of
`_rollback`'s current swallow, and it does not touch the reconciler (F-7 remains separate).

**7.4 Add lock force-release.** Add `force_release(config_id)` to the `BuildLockStore` port and
`RedisBuildLockStore` — an unconditional `DEL graphrag:lock:{config_id}`
(`redis_lock.py:44-45,85-89`) — used only by `admin_reset`. Safe here because the reset is an
explicit force operation; keep it out of the normal build path.

**7.5 DRY note.** The discard sequence mirrors `ReconciliationLoop._rollback` minus its
FAILED-terminal `set_state` and channel publish. F-26 deliberately implements it via the shared
**infrastructure ports** rather than extracting the loop's private methods, to avoid coupling the
admin service to the worker and to leave reconciler behavior (and its tests) untouched. A future
refactor could extract a shared discard primitive once F-7 makes `_rollback`'s failure handling
truthful (FU-3).

**Reuse inventory:**
- `RedisSnapshotStore` (`get_current`/`get`/`delete`/`clear_current`) and `RedisBuildLockStore`
  (+ new `force_release`) (`redis_lock.py`); `Neo4jDriver.delete_by_build`/`restore_from_snapshot`.
- Port construct/close pattern: `cascade_external_stores` (`graphrag_config_service.py:459-490`)
  and the reconciler worker wiring (`app/workers/graphrag_reconciler.py:124-150`).
- `_resolve_build_id` / `_rollback` (`graphrag_reconciler.py:350-424`) as the semantic reference.

**Data repair:** configs already forced to IDLE by the old behavior are invisible to both the
reconciler and this reset. A one-off operational step after ship (targeted re-drive of the
affected config ids through the discard sequence, or a temporary widening of the reconciler
scan) clears their lingering snapshot/pointer and compensates Neo4j. Document in the deploy
runbook; no migration.

## 8. Regression Test Plan

Extend `backend/tests/unit/test_graphrag_reset.py` (today it asserts only that state becomes
IDLE and one audit row is written, with fakes that have no lock/snapshot/Neo4j interaction).
Inject fake snapshot/lock/Neo4j stores into the service:

1. **Reset of a `FAILED_COMPENSATING` config discards it (red-first)** — fakes hold an in-flight
   build; `admin_reset` calls `delete_by_build` + `restore_from_snapshot`, then deletes the
   snapshot, clears the current pointer, force-releases the lock, and sets IDLE. Fails today
   (none are called).
2. **Idempotent reset of a clean config** — no snapshot/pointer: no compensation errors, still
   force-releases a stale lock defensively and clears any stale pointer, sets IDLE.
3. **Compensation failure keeps recovery material and is not advertised healthy** — Neo4j
   `restore_from_snapshot` raises; `admin_reset` does **not** set IDLE, does **not** delete the
   snapshot or clear the pointer, leaves the config in a `_STUCK_STATES` state, and raises
   (cross-ref F-7).
4. **Audit metadata** — the audit record carries `previous_state`, resolved `build_id`, and a
   compensation `outcome`.

Primary red-first: (1).

## 9. Risks and Rollback

- **External-store calls in the request path.** Bounded (admin-only, rare); a slow/unreachable
  store surfaces as a 5xx per Q-3 rather than a false success. Clients are short-lived and closed
  in `finally`, matching `cascade_external_stores`.
- **Force-release safety.** `force_release` deletes another instance's lock unconditionally; safe
  only because it runs inside the explicit force-reset. Keep it off the normal build path.
- **F-7 coupling.** F-26 must not modify `_rollback` and must not depend on F-7 landing first;
  its own sequencing already keeps recovery material on failure. Re-verify the failure semantics
  if F-7 later extracts a shared primitive.
- **Ordering of Neo4j vs Redis ops.** Deleting the snapshot before confirming the Neo4j rollback
  succeeded would strand a partially-compensated graph; §7.2 fixes the order (Neo4j first,
  Redis-delete only on success).
- **Rollback** — revert to the Postgres-only `admin_reset` and drop `force_release`; no
  schema/data migration.

## 10. Acceptance Criteria

- [ ] AC-1: The compensating-reset test (§8.1) fails before the fix and passes after.
- [ ] AC-2: `admin_reset` on a config with an in-flight build discards it — Neo4j rolled back to
  snapshot, snapshot deleted, current pointer cleared, build lock force-released — before setting
  IDLE.
- [ ] AC-3: `admin_reset` is idempotent on a clean/idle config and still force-releases any stale
  lock and clears any stale pointer.
- [ ] AC-4: When compensation cannot complete, `admin_reset` does not set IDLE, preserves the
  snapshot/pointer, leaves the config reconciler-visible, and reports failure (no false "healthy").
- [ ] AC-5: The audit record includes `previous_state`, resolved `build_id`, and compensation
  `outcome`.
- [ ] AC-6: The reconciler (`graphrag_reconciler.py`) is not modified; its existing tests in
  `test_graphrag_builder.py` remain green.
- [ ] AC-7: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`.

## 11. SRS Delta

None — restores [R11.04] transactional consistency for the admin-reset path; R11a.02's
state-reset description is unchanged, only completed.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (F-8/F-9, Qdrant orphans):** in the stuck states discarded here, Qdrant holds no points
  from the wedged build (Phase-2 never committed), so no Qdrant cleanup is needed; a discarded
  build that *had* committed Qdrant points is the F-8/F-9 idempotency/orphan territory.
- **FU-2 (F-7 truthful compensation):** the reconciler's `_rollback` still records swallowed
  Neo4j failures as terminal success; unchanged here, tracked by F-7.
- **FU-3 (shared discard primitive):** once F-7 makes `_rollback` truthful, a discard primitive
  could be extracted and shared by the reconciler and `admin_reset`.
- **FU-4 (facade hop / future knowmap reset):** the endpoint instantiates
  `GraphRagConfigService(db)` directly (`graphrag.py:619`) rather than via `KnowledgeFacade`; if a
  Knowledge Map admin-reset is ever added, it must perform the same discard sequence.
