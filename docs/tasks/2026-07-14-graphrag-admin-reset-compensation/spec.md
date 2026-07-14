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
  idle and rebuildable. When compensation cannot complete, a default reset refuses rather than
  advertise a false-healthy config; an explicit `force=true` still forces IDLE (the R11a.02
  escape hatch) but records the true, incomplete outcome instead of a silent success. Intent:
  [R11.04] (transactional consistency across Neo4j+Qdrant; §11.2a's Redis snapshot at
  `graphrag:build:{config_id}:{build_id}` and Neo4j rollback), [R11a.01] (single build per config,
  lock `graphrag:lock:{config_id}`, 10-min TTL), [R11a.02] (admin may force `idle` "in case
  reconciliation is stuck", always audit-logged — which documents only the state reset, not the
  un-cleared external state).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the compensation run? | **Inline synchronous cleanup** — `admin_reset` runs the discard sequence before forcing IDLE. | Chosen over enqueuing a reconciler pass. An admin resetting a wedged config expects an immediate, fully-consistent result; an async pass leaves the config briefly non-IDLE and eventually-consistent, and (per this finding) `IDLE` is not a swept state, so a naive "set a swept state and wait" would still need extra wiring. Inline reuses the same store operations the reconciler already performs and returns a truthful terminal state in one call. Trade-off: short-lived external-store clients in the reset request path (bounded, admin-only, infrequent) — the exact pattern `cascade_external_stores` already uses on config delete. |
| Q-2 | Roll the in-flight build **forward** (retry Qdrant) or **discard** it (roll Neo4j back)? | **Discard** — roll Neo4j back to the snapshot and drop the build. | `admin_reset` is a *force reset*, not a heal; the safe, predictable outcome is to abandon the wedged build and return to the last consistent state, mirroring the reconciler's `_rollback` terminal path (`graphrag_reconciler.py:350-397`) rather than its Qdrant-retry recovery path (`_reconcile_one`). |
| Q-3 | What if compensation's external calls fail (store unreachable — the "reconciliation is stuck" case R11a.02 names)? | Add an explicit **`force`** param. Default (`force=false`): attempt compensation, and on failure return 5xx without forcing IDLE (keep the recovery material, stay reconciler-visible). `force=true`: force IDLE regardless, auditing `outcome=compensation_failed` and setting a non-null `last_build_error` so the incomplete state is honest, not silently healthy. | Reconciles R11a.02 (a reliable escape hatch "in case reconciliation is stuck") with R11.04 (never advertise inconsistent state as healthy). The default protects consistency; `force=true` preserves the admin escape hatch for the stores-down case, but — unlike F-7's silent false-success — records the true outcome and flags the residue. The same flag governs lock contention (Q-5). |
| Q-4 | How is the lock released, given the token-checked release? | Add an unconditional admin **force-release** to the lock store (`DEL graphrag:lock:{config_id}`), used only as the `force=true` fallback (Q-5). | `RedisBuildLockStore.release` is token-checked and only the acquiring worker instance holds the token (`backend/contexts/knowledge/infrastructure/redis_lock.py:85-89`); the reset process cannot release another instance's lock that way. A stale lock otherwise blocks rebuilds for up to `LOCK_TTL_S` (10 min). A privileged force-release is appropriate only for an explicit `force=true` reset. |
| Q-5 | How does `admin_reset` serialize against a live builder/reconciler on the same config ([R11a.01]: one build per config at a time)? | **Acquire the build lock first.** If acquired (or the prior lock has expired), proceed and release normally. If held: `force=false` → return 409 (build/heal in progress, retry); `force=true` → force-release, re-acquire under this process's token, proceed. | Blindly force-releasing then compensating could race a concurrent reconciler heal (`run_once` takes the same lock, `graphrag_reconciler.py:154-156`) on the same Neo4j subgraph/snapshot. Acquiring `graphrag:lock:{config_id}` serializes `admin_reset` against both the builder (`graphrag_builder.py:183`) and the once-per-minute reconciler, honoring R11a.01; the `force` override is the deliberate, admin-accepted exception. |

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

`admin_reset` gains a `force` flag, acquires the build lock to serialize against the
builder/reconciler, performs the discard sequence directly against the infrastructure store
ports, and forces `IDLE`. The `force` flag governs both lock contention and compensation failure.
The reconciler is not modified.

**7.1 Signature, endpoint, and ports.** Add `force: bool = False` to `admin_reset`, threaded from
the endpoint (`backend/app/api/v1/graphrag.py:608-626`) as a new request param (query or body;
default `false`) — an OpenAPI change, so `pnpm run gen:api` afterwards. Construct short-lived
ports mirroring `cascade_external_stores` (`graphrag_config_service.py:459-490`): a
`Neo4jAsyncDriver` from `get_settings()` (closed in `finally`), plus the Redis stores — **both are
zero-arg** and build their own client: `RedisSnapshotStore()` and `RedisBuildLockStore()`
(`backend/app/workers/graphrag_reconciler.py:148,150`). No long-lived wiring is needed. Make the
stores injectable (optional attrs/params defaulting to the real constructors) so tests can override
them via the existing attribute-patch pattern (`test_graphrag_reset.py:86` already does
`service._configs = repo`).

**7.2 Serialize via the build lock (Q-5, [R11a.01]).** Acquire `graphrag:lock:{config_id}` before
any compensation:
- Acquired (or the prior lock already expired) → proceed; release **normally** at the end (this
  process now holds the token, so no force-release is needed on the happy path).
- Held → `force=false`: return **409** (build/heal in progress; retry). `force=true`:
  `force_release` (§7.5) then re-acquire under this process's token, and proceed — the deliberate,
  admin-accepted override.

This prevents racing a concurrent reconciler heal (`run_once` takes the same lock,
`graphrag_reconciler.py:154-156`) on the same Neo4j subgraph/snapshot.

**7.3 Discard sequence.**
- Resolve `build_id` via `snapshot_store.get_current(config_id)` (mirror `_resolve_build_id`,
  `graphrag_reconciler.py:405-424`).
- If a `build_id` and a snapshot exist (`snapshots.get`): `neo4j.delete_by_build(config_id,
  build_id)` then `neo4j.restore_from_snapshot(config_id, snapshot)` (mirror `_rollback:361-370`).
  If a `build_id` is known but no snapshot exists, best-effort `delete_by_build` only (no restore
  material — matches the reconciler's degraded no-snapshot handling).
- **Only after** the Neo4j rollback succeeds: `snapshots.delete(config_id, build_id)`,
  `snapshots.clear_current(config_id)`, then `set_state(IDLE, error=None)`; release the lock.
  Audit `admin.graphrag_reset` with `previous_state`, resolved `build_id`, `forced`, and
  `outcome` (`discarded` / `noop`).
- **No in-flight build** (no `build_id`/snapshot): clear any stale pointer, set IDLE, release the
  lock — idempotent and safe to call repeatedly.

**7.4 Failure handling by mode (Q-3).** If the Neo4j rollback raises:
- `force=false` → **stop**: do not delete the snapshot/pointer and do not set IDLE; leave the
  config in its current `_STUCK_STATES` state (reconciler-visible, so the sweep can still heal it),
  release the lock, and raise so the endpoint returns 5xx, auditing `outcome=compensation_failed`.
  This is the opposite of `_rollback`'s current swallow, and it does not touch the reconciler
  (F-7 remains separate).
- `force=true` → force IDLE anyway (the R11a.02 escape hatch): set `IDLE` but with a non-null
  `last_build_error` (e.g. `"admin reset: compensation incomplete"`) and audit
  `outcome=compensation_failed`, `forced=true`. Unlike F-7, the residue is recorded and visibly
  flagged, never a silent false-healthy. Recorded FU-5: such a config is IDLE and thus
  reconciler-invisible, so the residue needs a manual re-run — surfaced by the non-null error.

**7.5 Add lock force-release.** Add `force_release(config_id)` to the `BuildLockStore` port and
`RedisBuildLockStore` — an unconditional `DEL graphrag:lock:{config_id}`
(`redis_lock.py:44-45,85-89`) — used **only** on the `force=true` contention path (§7.2). Keep it
off the normal build path.

**7.6 DRY note.** The discard sequence mirrors `ReconciliationLoop._rollback` minus its
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
   build with the lock free; `admin_reset` (default `force=false`) acquires the lock, calls
   `delete_by_build` + `restore_from_snapshot`, deletes the snapshot, clears the current pointer,
   sets IDLE, and releases the lock. Fails today (none are called).
2. **Idempotent reset of a clean config** — no snapshot/pointer: no compensation errors, clears
   any stale pointer, sets IDLE, releases the lock.
3. **`force=false` compensation failure refuses and keeps recovery material** — Neo4j
   `restore_from_snapshot` raises; `admin_reset` does **not** set IDLE, does **not** delete the
   snapshot or clear the pointer, leaves the config in a `_STUCK_STATES` state, releases the lock,
   and raises (cross-ref F-7).
4. **`force=true` compensation failure forces IDLE with honest flag** — same raise, but with
   `force=true`: state becomes IDLE, `last_build_error` is non-null, audit `outcome=compensation_failed`
   and `forced=true`. Not a silent success.
5. **Lock contention** — a held lock makes `force=false` return/raise a 409-equivalent (no state
   change); `force=true` force-releases, re-acquires, and proceeds.
6. **Audit metadata** — the record carries `previous_state`, resolved `build_id`, `forced`, and
   `outcome`.

Primary red-first: (1).

## 9. Risks and Rollback

- **External-store calls in the request path.** Bounded (admin-only, rare); a slow/unreachable
  store surfaces as a 5xx (`force=false`) rather than a false success. Clients are short-lived and
  closed in `finally`, matching `cascade_external_stores`.
- **Force-release safety.** `force_release` deletes another instance's lock unconditionally; it
  runs only on the `force=true` contention path, where the admin has explicitly accepted
  interrupting a possibly-live build. Keep it off the normal build path and out of `force=false`.
- **`force=true` interrupting a genuinely live build.** If an admin forces a reset while a real
  build holds the lock, the force-release + discard can tear down an in-progress build. This is the
  documented, admin-accepted meaning of `force`; the default `force=false` returns 409 instead.
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
- [ ] AC-2: `admin_reset` acquires `graphrag:lock:{config_id}` before compensating and, on a
  config with an in-flight build, discards it — Neo4j rolled back to snapshot, snapshot deleted,
  current pointer cleared — before setting IDLE and releasing the lock.
- [ ] AC-3: `admin_reset` is idempotent on a clean/idle config (clears any stale pointer, sets
  IDLE, releases the lock).
- [ ] AC-4: With `force=false`, a held lock returns 409 (no state change) and a compensation
  failure returns 5xx without forcing IDLE or destroying recovery material (config stays
  reconciler-visible) — no false "healthy".
- [ ] AC-5: With `force=true`, a held lock is force-released and re-acquired, and a compensation
  failure still forces IDLE but sets a non-null `last_build_error` and audits
  `outcome=compensation_failed`, `forced=true` (honest, not silent).
- [ ] AC-6: The audit record includes `previous_state`, resolved `build_id`, `forced`, and
  compensation `outcome`.
- [ ] AC-7: The reconciler (`graphrag_reconciler.py`) is not modified; its existing tests in
  `test_graphrag_builder.py` remain green.
- [ ] AC-8: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`;
  `pnpm run gen:api` regenerates the client for the new `force` param.

## 11. SRS Delta

The fix restores [R11.04], but [R11a.02] as written describes only the state flip and omits the
compensation, lock serialization, and the `force` escape hatch this fix defines. Since analysis
showed [R11a.02] is incomplete rather than wrong, propose amending it (apply on approval):

> **[R11a.02]** Admin can reset a stuck config via `POST /api/admin/graphrag/{id}/reset`. The
> reset first acquires the build lock (`graphrag:lock:{config_id}`, R11a.01), then compensates the
> two-phase state before forcing `idle`: it discards any in-flight build — rolling Neo4j back to
> the pre-build snapshot, deleting the snapshot, and clearing the current-build pointer — and then
> sets `last_build_state = 'idle'`. A default reset (`force=false`) returns 409 if a build or heal
> is in progress and 5xx (no state change, recovery material preserved) if compensation fails, so
> it never advertises inconsistent state as healthy (R11.04). An explicit `force=true` overrides
> lock contention and, when compensation cannot complete, still forces `idle` but records the
> incomplete outcome (non-null `last_build_error`, audit `outcome=compensation_failed`). Every
> reset is audit-logged.

If you prefer to keep the SRS terse and treat this purely as a bugfix, the alternative is an empty
delta; flag which you want at approval.

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
- **FU-5 (force-true residue):** a `force=true` reset whose compensation failed leaves an IDLE
  config with a non-null `last_build_error`; because IDLE is reconciler-invisible, the residual
  Neo4j/snapshot state needs a manual re-run. The flagged error surfaces it, but a follow-up could
  make such flagged-IDLE configs sweep-eligible.
