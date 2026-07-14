---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.04]
---

# F-7: Failed Neo4j compensation is recorded as successful and made unrecoverable

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-7).

## 1. Summary

When a GraphRAG build fails Phase 2 (Qdrant) and its retries are exhausted, the reconciler
compensates by rolling Neo4j back to a pre-build snapshot. The rollback runs inside a
`try/except` that logs and swallows any exception, after which control falls through
*unconditionally* to a finalization block that marks the config `failed` with error
`"phase2 retries exhausted; rolled back"`, publishes terminal `FAILED`, **deletes the
snapshot**, clears the current-build pointer, and audits `outcome=rolled_back` — even when
`restore_from_snapshot` actually threw. So a transient Neo4j failure during compensation
destroys the only recovery material, advertises a successful rollback, and leaves the graph
inconsistent with no automatic retry (the config is now in `FAILED`, which is not in
`_STUCK_STATES`, so the reconciler never revisits it). Both Concept Maps and Knowledge Maps
share this engine. The fix keeps the config in `failed_compensating` and retains the
snapshot and current pointer until *both* rollback operations succeed; only then does it mark
`failed`, delete the snapshot, and audit `rolled_back`. A failed compensation is surfaced
distinctly and re-attempted on the next sweep.

## 2. Observed vs Expected

- **Observed** — `_rollback` (`backend/contexts/knowledge/application/graphrag_reconciler.py:350-397`)
  fetches the snapshot (`:357-360`); if present it runs `delete_by_build` then
  `restore_from_snapshot` inside a `try` whose `except` only logs
  (`:361-372`). Control then falls through unconditionally: `set_state(FAILED, "phase2
  retries exhausted; rolled back")` (`:373-377`), publish terminal `FAILED` (`:378-380`),
  `snapshots.delete(...)` (`:381-384`), `_clear_current(...)` (`:385`), and audit
  `metadata.outcome="rolled_back"` (`:386-397`). Nothing distinguishes a thrown
  `restore_from_snapshot` from a clean one. Because `FAILED` is absent from `_STUCK_STATES`
  (`:61-65`), the reconciler sweep (`run_once :147-148`) never picks the config up again.
- **Expected** — [R11.04] and §11.2a step 5 of `REQUIREMENTS.md`: the reconciler
  "compensates by rolling back Neo4j… **On successful rollback,** `last_build_state =
  'failed'`." The transition to `failed`, the snapshot deletion, and the `rolled_back`
  outcome are all conditioned on the rollback succeeding. A failed compensation must retain
  the recovery material and remain reconcilable, not be advertised as healed.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | On a failed compensation, terminal `FAILED` or keep `FAILED_COMPENSATING`? | **Keep `FAILED_COMPENSATING`**, retain snapshot + current pointer, audit a distinct `outcome` (e.g. `compensation_failed`), and let the next sweep retry. | `FAILED_COMPENSATING` is already in `_STUCK_STATES` (`graphrag_reconciler.py:61-65`), so retaining it re-enrolls the config in the heal loop for free. Re-running compensation is safe: `delete_by_build` is build-id-scoped and idempotent (`neo4j_driver.py:183-205`), and `restore_from_snapshot` is MERGE-based and idempotent (`neo4j_driver.py:248-295`). |
| Q-2 | Bound the compensation retries to avoid an infinite loop? | Not in this fix — rely on the reconciler's existing per-sweep cadence and the 24h snapshot TTL as the natural bound; add an alertable audit signal. | Compensation failure is a transient-Neo4j condition; unbounded but low-frequency retry converges once Neo4j recovers. A dedicated max-attempt / needs-manual-intervention terminal state is recorded as FU-1 rather than blocking this fix. |

## 4. Reproduction

Preconditions: a config that has reached `failed_compensating` (a build whose Phase 2 fails
all retries), a valid pre-build snapshot in Redis, and a Neo4j that will reject the
compensation writes (transient outage) — modeled in a unit test with a Neo4j stub whose
`restore_from_snapshot` raises.

1. Drive the reconciler into `_rollback` with a snapshot present
   (`graphrag_reconciler.py:357-360`).
2. `restore_from_snapshot` raises; the `except` at `:371-372` swallows it.
3. Observe that the config is nonetheless set to `FAILED` (`:373-377`), the snapshot is
   deleted (`:381-384`), the current pointer is cleared (`:385`), and the audit records
   `outcome=rolled_back` (`:386-397`).
4. Re-run the sweep: the config is in `FAILED`, absent from `_STUCK_STATES`, and is never
   revisited — the partial Neo4j build persists permanently.

Deterministic under the stub.

## 5. Root Cause Analysis

The causal chain:

1. The rollback `except` at `graphrag_reconciler.py:371-372` swallows the exception without
   recording failure or branching on it.
2. The finalization steps at `:373-397` run **unconditionally** after the `try/except`,
   independent of whether the compensation succeeded. **This is the root cause** — the
   earliest link whose correction (gate the state transition, snapshot deletion, pointer
   clear, and `rolled_back` audit on rollback success) prevents every symptom: false
   `FAILED`, destroyed snapshot, false `rolled_back`, and permanent loss of reconcilability.
3. Deleting the snapshot at `:381-384` is the aggravating factor that makes the inconsistency
   *unrecoverable* — once the snapshot is gone, even a corrected sweep cannot compensate
   (it lands in the no-snapshot `FAILED` path `:273-282`).
4. `FAILED` being outside `_STUCK_STATES` (`:61-65`) is correct for a genuine rollback but,
   combined with (2), turns a transient failure into a terminal one.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Concept Map and Knowledge Map (shared engine) whose build hits
  `failed_compensating` while Neo4j is transiently unavailable during compensation: the
  config is left with partially-committed Phase-1 triples, no snapshot, a `FAILED` state that
  advertises a clean rollback, and no automatic path back to consistency.
- **Sibling suspects:**
  - **No-snapshot compensation path (`:273-282`) — confirmed related.** When
    `_resolve_build_id` returns `None`, the code sets `FAILED` with `"no snapshot available
    for compensation"` and does **not** emit a `rolled_back` audit — so it already avoids the
    false-success claim, but still marks terminal `FAILED` with partial Neo4j data left in
    place. F-7's fix reduces the frequency of reaching this path (snapshots are no longer
    deleted on failed compensation), but the path itself still needs an honest,
    non-`rolled_back` terminal signal; addressed by reusing the distinct outcome from Q-1
    where a snapshot was expected but missing. Verify the audit metadata here does not read
    as healed.
  - **Crashed-Phase-1 `RUNNING` rollback (`:289`) — same `_rollback`, same fix.** The
    Phase-1 crash path also calls `_rollback`; it inherits the corrected gating with no extra
    work.
  - **`admin_reset` (F-26, out of scope) — cleared here.** `admin_reset`
    (`graphrag_config_service.py:395-428`) forces `IDLE` without clearing snapshot/pointer/
    lock or compensating; it is a separate finding and is not modified by this fix, but the
    two interact (an admin reset of a `failed_compensating` config removes it from
    `_STUCK_STATES`). Noted as FU-2, not fixed here.

## 7. Fix Design

Reorder `_rollback` (`graphrag_reconciler.py:350-397`) so terminal finalization is gated on
compensation success:

1. **Track rollback success.** In the `try` at `:361-370`, on success set a local
   `compensated = True`; in the `except` (`:371-372`) keep the log, set
   `compensated = False`, and do **not** delete the snapshot or clear the pointer.
2. **Branch on the outcome:**
   - **Success** — proceed exactly as today: `set_state(FAILED, "phase2 retries exhausted;
     rolled back")`, publish `FAILED`, delete snapshot, clear current pointer, audit
     `outcome=rolled_back`.
   - **Failure** — set (or leave) `set_state(FAILED_COMPENSATING, "compensation failed;
     will retry")`, publish `FAILED_COMPENSATING` (not terminal `FAILED`), **retain** the
     snapshot and current pointer, and audit `outcome=compensation_failed` (a new, distinct
     audit outcome that monitoring can alert on). Return without deleting recovery material.
3. **No-snapshot path (`:273-282`) alignment.** Keep it terminal `FAILED` (compensation is
   genuinely impossible without a snapshot) but ensure its audit outcome is not
   `rolled_back` — use a distinct `compensation_unavailable` outcome so dashboards never
   count it as a clean rollback.

Because `FAILED_COMPENSATING` is in `_STUCK_STATES`, the next sweep re-enters
`_reconcile_one`, re-attempts Phase 2 (idempotent once F-9 lands; independently safe today),
and, on continued Phase-2 failure, re-runs the now-idempotent compensation until Neo4j
recovers. The snapshot's 24h TTL (`SNAPSHOT_TTL_S`, `graphrag_builder.py:60`) bounds the
retry window.

**Data repair:** configs already wedged in a false `FAILED` from this bug have lost their
snapshots and cannot be auto-compensated. They must be found via audit rows with
`outcome=rolled_back` whose builds left orphan `build_id` triples, and repaired by a manual
rebuild (which re-derives the graph) or `admin_reset` + rebuild. Recorded as FU-3; no
migration.

## 8. Regression Test Plan

Unit test in `backend/tests/unit/test_graphrag_reconciler.py` (extend the existing rollback
coverage):

1. **Failed compensation retains recovery material** (primary red-first test): configure the
   reconciler with a Neo4j stub whose `restore_from_snapshot` raises and a snapshot store
   holding a snapshot; drive `_rollback`. Assert: state is `FAILED_COMPENSATING` (not
   `FAILED`); the snapshot store still holds the snapshot (not deleted); the current pointer
   is retained; and the emitted audit `outcome` is `compensation_failed`, not `rolled_back`.
   Fails today — current code sets `FAILED`, deletes the snapshot, and audits `rolled_back`
   (`graphrag_reconciler.py:373-397`).
2. **Successful compensation unchanged** (guard): with a Neo4j stub whose compensation
   succeeds, assert the existing behavior holds — `FAILED`, snapshot deleted, pointer
   cleared, `outcome=rolled_back`.
3. **Re-enrollment** (guard): after test (1), assert a subsequent
   `list_in_state(FAILED_COMPENSATING)` returns the config, proving it is re-swept.

## 9. Risks and Rollback

- **Retry storms** — a persistently unreachable Neo4j keeps the config in
  `failed_compensating`, re-attempting each sweep; the compensation ops are idempotent so
  this is safe but noisy. Mitigated by the audit signal (ops can intervene) and the snapshot
  TTL bound; a hard cap is FU-1.
- **State-machine coupling** — retaining `FAILED_COMPENSATING` re-uses an existing
  `_STUCK_STATES` member, so no state-enum or sweep-selection change is needed, minimizing
  blast radius.
- **Rollback** — revert `_rollback` to the unconditional finalization; code-only, no schema
  change. Configs healed under the fix remain consistent after rollback.

## 10. Acceptance Criteria

- [ ] AC-1: The failed-compensation regression test (§8.1) fails before the fix and passes
  after.
- [ ] AC-2: On a compensation whose Neo4j restore throws, the config remains
  `failed_compensating`, the snapshot and current-build pointer are retained, and no audit
  row records `outcome=rolled_back`.
- [ ] AC-3: On a successful compensation, behavior is unchanged: `failed`, snapshot deleted,
  pointer cleared, `outcome=rolled_back` (§8.2).
- [ ] AC-4: A config left in `failed_compensating` by a failed compensation is re-selected by
  the next reconciler sweep (§8.3).
- [ ] AC-5: The no-snapshot compensation path (`graphrag_reconciler.py:273-282`) does not
  emit `outcome=rolled_back`.
- [ ] AC-6: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`.

## 11. SRS Delta

None. This restores the [R11.04] / §11.2a step-5 "on successful rollback" contract the code
already claims to honor.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (bounded compensation retries):** add a max-attempt counter or a distinct
  `needs_manual_intervention` terminal state so an indefinitely-unreachable Neo4j does not
  loop forever; out of scope here.
- **FU-2 (F-26 interaction):** `admin_reset` forces `IDLE` without compensating or clearing
  external state; a config reset out of `failed_compensating` still leaves partial Neo4j/
  Qdrant data. Separate finding (F-26), not addressed here.
- **FU-3 (deploy data repair):** identify configs already wedged in false `FAILED` by this
  bug (audit `outcome=rolled_back` with orphan `build_id` triples) and rebuild them; no
  migration.
- **FU-4 (doc/behavior gap):** the reconciler module docstring and the cron comment
  (`graphrag_reconciler.py:1-10`, `backend/app/workers/main.py:319-320`) say the sweep scans
  only `failed_compensating`, but it heals all three `_STUCK_STATES`; correct the comments.
