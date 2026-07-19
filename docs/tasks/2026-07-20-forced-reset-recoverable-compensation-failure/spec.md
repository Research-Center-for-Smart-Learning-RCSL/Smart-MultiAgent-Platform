---
type: bugfix
status: approved
created: 2026-07-20
requirements: [R11a.02, R11.04]
---

# A forced reset over a failed compensation publishes a depleted graph as healthy

## 1. Summary

`POST /api/admin/graphrag/{id}/reset?force=true` sets `last_build_state = idle` when 2PC
compensation failed but the recovery material was present
(`backend/contexts/knowledge/application/graph_admin_reset.py:324-326`). By that point
`delete_by_build` has already removed the failed build's rows and the snapshot restore has
raised, so the subgraph is missing what the rollback was supposed to put back. `idle` is
readable and is outside the reconciler's sweep set, so agents query a depleted graph presented
as healthy and no automatic process ever revisits it. Only a non-null `last_build_error` and an
audit row record that anything went wrong.

- **Goal:** a forced reset must never publish a graph whose rollback did not complete.
- **Non-goals:** changing `force=false` behavior, changing lock-contention override semantics,
  adding a new `BuildState`, or making `RECOVERY_UNAVAILABLE` reconciler-swept.

## 2. Observed vs Expected

- **Observed:** on the `DiscardPlan.COMPENSATE` path, `delete_by_build` then
  `restore_from_snapshot` run under a `try` (`graph_admin_reset.py:265-281`); a restore failure
  sets `comp_error` and `outcome = "compensation_failed"`. With `force=true` the refusal branch
  is skipped (`:292-313`) and `new_state` resolves to `IDLE`, because
  `RECOVERY_UNAVAILABLE` is selected only for `DiscardPlan.UNAVAILABLE` (`:324-326`). `IDLE` is
  absent from `IN_FLIGHT_BUILD_STATES` (`backend/contexts/knowledge/domain/graphrag.py:118-125`)
  so retrieval serves it, and absent from `_STUCK_STATES`
  (`backend/contexts/knowledge/application/graphrag_reconciler.py:79-83`) so the sweep never
  returns. The behavior is pinned by
  `backend/tests/unit/test_graphrag_reset.py:460-472`.
- **Expected:** [R11.04] requires that a failure not leave inconsistent state, and the sibling
  path already honors this: a forced `UNAVAILABLE` reset lands on `RECOVERY_UNAVAILABLE`
  precisely so a graph that cannot be rolled back is not advertised as readable
  (`graph_admin_reset.py:319-323`, `test_graphrag_reset.py:430-456`).

**This was a scoped decision, not an oversight.** `docs/tasks/2026-07-17-graphrag-reset-expired-recovery/spec.md`
D-8 reversed its own Q-3 for the `UNAVAILABLE` path only, and states in terms: "A forced reset
whose recovery material *was* present still lands on `idle`" (`:342`). Its stated rationale —
forcing `idle` "bought no recovery capability", since `RECOVERY_UNAVAILABLE` is already accepted
by the builder engine (`graphrag_builder.py:224-228`) and the manual build endpoint
(`app/api/v1/graphrag.py:539-543`) — applies unchanged to this path. This task extends that
decision to the remaining half rather than contradicting it.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which state should a forced reset land on when compensation failed but material was present? | `RECOVERY_UNAVAILABLE`. | Read-blocked, so a depleted graph is never served; manual rebuild still works, so `force` keeps its unstick purpose; symmetric with the `UNAVAILABLE` path; reuses an existing state, so no migration. |
| Q-2 | Alternative considered: `FAILED_COMPENSATING`, which the reconciler would keep retrying. | Rejected. | The restore is idempotent so a retry could succeed, but leaving the config in the sweep set means `force` did not unstick it, which is the one thing the operator invoked it for. |
| Q-3 | Keep or clear the retained snapshot and current-build pointer? | Keep. | They are gated behind `if comp_error is None` (`graph_admin_reset.py:282-287`) and already survive. A later admin reset can retry compensation with them, and the 24h TTL reclaims them otherwise, so they are useful rather than orphaned. |

## 4. Reproduction

1. Drive a config to `FAILED_COMPENSATING` with a snapshot and current-build pointer present.
2. Make the Neo4j snapshot restore fail (the store rejecting the statement, or unreachable).
3. `POST /api/admin/graphrag/{id}/reset?force=true`.
4. Observe `last_build_state = idle` with a non-null `last_build_error`, the subgraph missing
   the rows `delete_by_build` removed, retrieval serving that subgraph, and the reconciler
   never re-selecting the config.

Covered today only as the asserted-correct outcome at `test_graphrag_reset.py:460-472`.

## 5. Root Cause Analysis

`new_state` is chosen from the *plan* (`graph_admin_reset.py:324-326`) rather than from whether
compensation actually completed. `DiscardPlan.UNAVAILABLE` is a statement about the material
being missing up front; it does not cover a `COMPENSATE` plan that then failed. Both outcomes
leave a subgraph the rollback did not finish, so both must be read-blocked. The earliest
correction is to select the state from `comp_error` as well as the plan.

`restore_from_snapshot` being non-atomic aggravated this — a partial restore added a nodes-only
skeleton on top — but that was fixed separately (commit `d60c57f`) and is not the root cause:
even an all-or-nothing restore failure leaves the graph missing the rolled-back rows.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** every `force=true` admin reset over a failed compensation, for both graph
  products — `graph_admin_reset.py` is shared by the Concept Map and Knowledge Map bindings
  (`graph_admin_reset.py:10-15`), so one change covers both.
- **Confirmed sibling — documentation drift:** D-8 changed the `UNAVAILABLE` landing state but
  neither intent source was updated. `REQUIREMENTS.md:522` still says a forced reset "still
  forces `idle`" when compensation cannot complete, and the shared API prose
  (`backend/app/api/v1/deps.py:15-21`) still says forcing idle "also makes the graph readable
  again, including a partially applied build that can never be rolled back". Both describe
  pre-D-8 behavior and are already wrong before this task; both are corrected here (§11).
- **Cleared — reconciler:** its own compensation-failure path preserves the stuck state and
  retries rather than publishing anything (`graphrag_reconciler.py:479-502`). Not affected.
- **Cleared — `force=false`:** raises `compensation_error` and writes no state
  (`graph_admin_reset.py:292-313`). Unchanged.

## 7. Fix Design

1. Select the terminal state from the compensation outcome, not the plan alone: land on
   `RECOVERY_UNAVAILABLE` when `comp_error` is set, and on `IDLE` only for a genuinely clean
   discard or no-op. Keep the existing `UNAVAILABLE` behavior, which this subsumes.
2. Leave the snapshot and pointer handling untouched (Q-3) — the existing
   `if comp_error is None` gate already retains them on this path.
3. Update the comment at `graph_admin_reset.py:319-323` so it explains the outcome-based rule
   rather than the plan-based one.
4. Correct the shared API description (`deps.py:15-21`) to match, per §11.

No data repair is required: the affected configs hold a graph the rollback did not finish, and
a rebuild is the documented escape. Configs already forced to `idle` by the old behavior are not
migrated — nothing durably records which ones they were, and a rebuild fixes them.

### Patterns to follow

`graph_admin_reset.py` is shared application logic behind per-product bindings; keep the change
in the shared function so both products stay provably identical (`:10-15`). The
state-selection change belongs at the existing `new_state` assignment, not spread into the
compensation `try`.

### Reuse inventory

`BuildState.RECOVERY_UNAVAILABLE` (`domain/graphrag.py:85`), `IN_FLIGHT_BUILD_STATES`
(`:118-125`), the existing `comp_error` / `outcome` locals, `_emit_reset_audit`, and the
`FakeNeo4j(raise_on_restore=True)` fixture already used by `test_graphrag_reset.py:466`. No new
helper, state, or migration.

## 8. Regression Test Plan

1. Rewrite `test_graphrag_reset.py:460-472`
   (`test_force_true_on_a_recoverable_failure_still_lands_idle`) to assert
   `RECOVERY_UNAVAILABLE` and membership in `IN_FLIGHT_BUILD_STATES`, mirroring its sibling at
   `:430-456`. Renamed to match. It fails against current code, which returns `IDLE`. This is
   the §10 AC-1 test: it encodes the corrected contract, so amending it is the fix's definition,
   not a weakened assertion.
2. Add an assertion that the snapshot and current-build pointer survive (Q-3).
3. Add the Knowledge Map counterpart to `test_knowmap_reset.py`, which today covers the
   `UNAVAILABLE` forced case (`:147-160`) but not the failed-compensation one.
4. Retain the clean-discard `IDLE` landing (`test_knowmap_reset.py:116-117`), the `force=false`
   refusal, and the forced `UNAVAILABLE` case unchanged.

## 9. Risks and Rollback

An operator who relied on `force=true` to make a graph readable after a failed compensation will
now get a read-blocked config and must rebuild. That is the intent, and it matches what a forced
`UNAVAILABLE` reset has already done since D-8. The API prose and SRS are updated so the
contract is not surprising. Rollback is reverting one expression and the two prose edits.

## 10. Acceptance Criteria

- [ ] AC-1: The rewritten `test_graphrag_reset.py` forced-recoverable-failure test fails before
  the fix (observes `IDLE`) and passes after (`RECOVERY_UNAVAILABLE`).
- [ ] AC-2: A `force=true` reset whose compensation failed lands on `RECOVERY_UNAVAILABLE`, is
  in `IN_FLIGHT_BUILD_STATES`, and records a non-null `last_build_error` with audit
  `outcome=compensation_failed` and `forced=true`.
- [ ] AC-3: The snapshot and current-build pointer survive that reset.
- [ ] AC-4: A clean discard and a no-op reset still land on `IDLE`.
- [ ] AC-5: `force=false` still refuses with no state change and preserved material; the forced
  `UNAVAILABLE` path still lands on `RECOVERY_UNAVAILABLE`.
- [ ] AC-6: Knowledge Map and Concept Map behave identically, each covered by its own test.
- [ ] AC-7: `REQUIREMENTS.md` [R11a.02] and `deps.py:15-21` describe the shipped behavior for
  both forced-failure paths.
- [ ] AC-8: Backend lint, format, typecheck, and the CI unit selection pass.

## 11. SRS Delta

[R11a.02] is currently wrong independently of this task: it documents pre-D-8 behavior for the
`UNAVAILABLE` path. Replace the `force=true` sentence in `REQUIREMENTS.md:522`:

> An explicit `force=true` overrides lock contention and unsticks the config, but never
> re-opens reads over an unfinished rollback: when compensation cannot complete — whether the
> recovery material was missing (`outcome=compensation_unavailable`) or the rollback itself
> failed (`outcome=compensation_failed`) — the reset records the incomplete outcome (non-null
> `last_build_error`) and sets `last_build_state = 'recovery_unavailable'`, which is
> read-blocked but accepted by manual rebuild. Only a clean discard or a no-op reset sets
> `last_build_state = 'idle'`.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- FU-1: `docs/tasks/2026-07-17-graphrag-reset-expired-recovery/spec.md` D-8 changed shipped
  behavior without updating [R11a.02] or the API prose. Worth a check of that dossier's other
  deviations for the same write-back gap; out of scope here, where only the `force` sentence is
  corrected.
