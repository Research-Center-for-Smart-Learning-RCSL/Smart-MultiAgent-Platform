---
type: bugfix
status: approved
created: 2026-07-17
requirements: [R10.06, R11.19]
---

# Failed final collection teardown releases its embedding pin

## 1. Summary

This dossier remediates F-3 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md` and its
confirmed Concept Map sibling. All three knowledge products clear their durable project
embedding pin before attempting the post-commit Qdrant collection drop. A failed drop is
swallowed as success, so a different-dimension config can be accepted against the retained
old-dimension collection (`backend/contexts/knowledge/application/config_service.py:431-477`).

- **Goal:** retain the pin until Qdrant confirms deletion/absence and durably retry failed
  configless collection teardown.
- **Non-goals:** make ordinary config CRUD depend on Qdrant availability (see Q-5 for the one
  narrow exception this task accepts), change embedding-model choices, redesign project
  collection names, or consolidate the triplicated teardown lifecycle (deferred, FU-1).

## 2. Observed vs Expected

- **Observed:** File RAG, Knowledge Map, and Concept Map delete paths clear the pin before
  collection teardown (`backend/app/api/v1/rag.py:377-389`;
  `backend/app/api/v1/knowmap.py:355-364`; `backend/app/api/v1/graphrag.py:469-480`). Their
  drop helpers catch Qdrant errors but still return success, because `return True` sits
  outside the `try` (`backend/contexts/knowledge/application/config_service.py:449-477`;
  `backend/contexts/knowledge/application/knowmap_config_service.py:391-418`;
  `backend/contexts/knowledge/application/graphrag_config_service.py:697-724`). The result is
  audited as fact: all three endpoints record `collection_dropped: true` while the collection
  still stands (`backend/app/api/v1/rag.py:402`; `backend/app/api/v1/knowmap.py:377`;
  `backend/app/api/v1/graphrag.py:496`).
- **Expected:** [R10.06] and [R11.19] require one fixed collection dimension per project and
  rejection of incompatible later configs (`REQUIREMENTS.md:461,542`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Add a teardown table or reuse an existing durable marker? | Reuse a configless `project_embedding_pins` row as the pending-teardown marker. | The pin already carries project, kind, provider/model, and dimension and is independent of config lifecycle (`backend/contexts/knowledge/infrastructure/embedding_pin_repository.py:1-17`). |
| Q-2 | When may the pin be removed? | Only after Qdrant reports the collection deleted or already absent, while holding the existing `(project, kind)` advisory lock. | It serializes teardown with create and makes a stale pin fail closed if the final DB commit fails. |
| Q-3 | Which products are in scope? | File RAG, Knowledge Map, and Concept Map. | All three copied the same clear-before-drop lifecycle (`backend/contexts/knowledge/application/graphrag_config_service.py:679-723`). |
| Q-4 | The current ordering is a recorded prior decision (DOM-4/W5: "irreversible external deletes trail the durable DB commit, so a failed commit never leaves a dropped collection behind a live pin", `backend/contexts/knowledge/application/config_service.py:450-457`). Does this task overturn it? | Partly, and deliberately. The external drop still trails the config soft-delete commit, so DOM-4 holds. W5's window is knowingly re-opened: a drop that succeeds before the pin-clear commit fails leaves a dropped collection behind a live pin. | The two failure modes are not symmetric. F-3 fails **open** — a missing pin lets an incompatible config be accepted, breaking [R10.06]/[R11.19] with no recovery. The re-opened W5 fails **closed** — a stale pin only over-rejects, is self-healing via the Q-5 retry paths, and never corrupts the invariant. Trading a fail-open integrity break for a bounded fail-closed availability cost is the right direction. The DOM-4/W5 docstrings must be rewritten to say this, or they will read as contradicting the new code. |
| Q-5 | A retained pin blocks a different-dimension create until something clears it. How is that window closed? | Both: a periodic sweep as the durable backstop, plus a create-path retry. The create path attempts teardown **only** when a pin exists at a different dimension and no live config remains — the exact case that would otherwise be wrongly rejected. | `ensure` already no-ops when the pin matches the requested dimension (`backend/contexts/knowledge/infrastructure/embedding_pin_repository.py:80-102`), so a same-dimension create needs no teardown and touches no Qdrant. The narrow hook keeps the §2 non-goal intact for ordinary CRUD while collapsing the user-visible rejection window to zero. A sweep alone at `retention_sweep`'s daily 03:30 cadence (`backend/app/workers/main.py:312`) would leave a user wrongly rejected for up to 24h. A different-dimension create genuinely cannot succeed while the old collection stands, so failing it during a Qdrant outage is honest, not a regression. |
| Q-6 | Keep the §7.5 three-service consolidation in this bugfix? | No. Fix the three call sites in place; the consolidation becomes FU-1. | The triplication is pre-existing debt (§6), not introduced here. A bugfix commit that also moves code across three services is harder to review and harder to revert if the fix regresses. Regression tests land against the existing shape and will still hold after FU-1. |
| Q-7 | The Qdrant delete runs inside an open Postgres transaction holding the `(project, kind)` advisory lock. Bound it? | Yes — give the teardown's Qdrant client an explicit timeout. | The lock-across-network-call shape is pre-existing (`backend/contexts/knowledge/application/config_service.py:459` then `:470-474`), but this task makes it load-bearing. Unbounded, a hung Qdrant holds a pooled Postgres connection and blocks config creation for that project/kind indefinitely. A timeout surfaces as `failed`, retains the pin, and routes to the Q-5 retry paths — exactly the fail-closed behavior this task wants. |

## 4. Reproduction

1. Create the final config for a project/kind and let it create a 1536-dimensional
   collection.
2. Make Qdrant unavailable and delete that final config.
3. Observe the pin is cleared and the helper reports success although the collection remains.
4. Create a 3072-dimensional config; save accepts the absent pin, but ingestion/build later
   hits the physical dimension guard (`backend/contexts/knowledge/infrastructure/qdrant_store.py:56-93`).

## 5. Root Cause Analysis

The implementation equates "no live config" with "the physical collection invariant no
longer exists." The durable pin is removed in the database transaction before external
teardown, while the external failure is downgraded to a log
(`backend/contexts/knowledge/application/config_service.py:475-477`). Because the
source-orphan sweep is a *row-absence* backstop — it reclaims infra only for projects whose
`projects` row is physically gone, deliberately and by documented decision Q-4 of that task
(`backend/app/workers/tasks/retention.py:256-273`, esp. the `live_ids` set at `:270`) — a
teardown failure for a project that still exists has no durable retry path at all. The
earliest correction is to make confirmed external absence the precondition for pin release.

The adapter is not at fault and needs no change. `QdrantStore.delete_collection`
(`backend/contexts/knowledge/infrastructure/qdrant_store.py:107-122`) already returns `True`
when it dropped, `False` when the collection was absent, and already raises on error — by
deliberate module policy ("Qdrant failures fail loudly",
`backend/contexts/knowledge/infrastructure/qdrant_store.py:12-13`). The three callers discard
that contract by catching `Exception` and returning `True` from outside the `try`. The
`dropped` / `absent` / `failed` distinction this task needs is therefore already available at
the call site; the fix is to stop throwing it away.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** File RAG, Knowledge Map, and Concept Map final-config deletion under
  Qdrant failure; later incompatible configs are accepted but cannot index/build.
- **Cleared:** non-final deletion rechecks live siblings under the advisory lock before any
  pin action (`backend/contexts/knowledge/application/config_service.py:443-446`).
- **Confirmed sibling:** Concept Map has the same endpoint and service ordering
  (`backend/app/api/v1/graphrag.py:465-479`;
  `backend/contexts/knowledge/application/graphrag_config_service.py:679-723`).
- **Existing debt:** lifecycle code and inaccurate success reporting are duplicated across
  three services; current tests cover only successful File-RAG teardown
  (`backend/tests/unit/test_embedding_pin.py:212-303`).

## 7. Fix Design

1. Commit config soft deletion while retaining the pin. `clear_pin_if_last_config`
   (`backend/contexts/knowledge/application/config_service.py:431-447`) no longer clears; it
   becomes a live-config check that reports whether teardown is owed.
2. In a new transaction, acquire the existing `(project_id, kind)` advisory lock, recheck no
   live config exists, and request collection deletion while holding the lock, with an
   explicit client timeout (Q-7).
3. Clear the pin only after Qdrant confirms `dropped` or `already absent` — i.e. only when
   `delete_collection` returns rather than raises. Retain the pin and report `failed` on any
   raised error. If a same-dimension create wins first, the live-config recheck skips
   teardown. A different-dimension create remains rejected.
4. Fix the three call sites in place (Q-6): stop catching `Exception` around the drop, and
   let the endpoint audit the true outcome. Do not extract a shared port in this task.
5. **Create-path retry** (Q-5): before `ensure`, under the same re-entrant advisory lock, if a
   pin exists at a different dimension **and** no live config remains, attempt the step-2/3
   teardown first, then proceed to `ensure`, which now inserts the new pin. On teardown
   failure, fall through and let `ensure` raise the existing typed conflict exactly as today.
   Same-dimension and no-pin creates take no new path and issue no Qdrant call. If the create
   transaction later rolls back after a successful drop, the pin-clear rolls back with it —
   leaving a stale pin over a dropped collection, which the sweep resolves as `absent`.
6. **Durable sweep** (Q-5): a bounded periodic task enumerating configless pins across all
   three kinds, retrying the same helper, isolating per-item failures. Prefer appending a
   `(name, callable)` tuple to `_POLICIES` (`backend/app/workers/tasks/retention.py:585-608`),
   which the master `retention_sweep` runs in sequence — no `main.py` change needed. Register
   a dedicated `arq.cron` only if a cadence faster than `retention_sweep`'s daily 03:30
   (`backend/app/workers/main.py:312`) is wanted; with the Q-5 create-path retry closing the
   user-visible window, daily is sufficient. Repeated attempts are idempotent; no new schema.
7. Audit `dropped`, `absent`, `skipped_live_config`, and `failed` accurately, replacing
   today's unconditional `collection_dropped: true`. Never log Qdrant credentials or raw
   client errors containing headers.
8. Rewrite the DOM-4/W5 docstrings (`backend/contexts/knowledge/application/config_service.py:450-457`
   and its two siblings) to record the Q-4 trade. Leaving them is a correctness hazard for the
   next reader: they assert the exact ordering the code no longer has.

Reuse `EmbeddingPinRepository.acquire_lock/ensure/clear`
(`backend/contexts/knowledge/infrastructure/embedding_pin_repository.py:38-50,59-150`) — note
`acquire_lock` is re-entrant within a transaction (`:41-43`), which is what makes step 5's
lock-then-teardown-then-`ensure` sequence cheap. Reuse `QdrantStore.delete_collection`'s
existing dropped/absent/raise contract (`:107-122`) and the `GraphRagVectorStore` prefix
convention (`backend/contexts/knowledge/application/knowmap_config_service.py:413` uses
`prefix="knowmap"`; `graphrag_config_service.py:719` uses the default).

### Security Considerations

This is tenant data integrity/availability, not cross-tenant access. Collection naming and
project scoping must remain exact. A failed teardown must fail closed by retaining the prior
pin; audit metadata is limited to project/config/kind and outcome.

## 8. Regression Test Plan

Extend `backend/tests/unit/test_embedding_pin.py`, whose existing File-RAG teardown coverage
(`:212-303`) asserts the *old* contract and must be updated, not merely added to.

1. Parameterize all three product services: make `delete_collection` raise; assert the pin
   remains, the audited outcome is `failed` (never `collection_dropped: true`), and a
   subsequent incompatible `ensure` raises the existing typed conflict.
2. Verify both `delete_collection` returns clear the pin: `True` (dropped) and `False`
   (already absent).
3. Add a real-Postgres two-session integration: concurrent create and teardown serialize so a
   live collection is never dropped.
4. Verify the retry sweep selects configless pins, isolates per-item failures (one project's
   Qdrant error does not abort the others), and clears a pin only on confirmed absence.
5. Create-path retry (Q-5), the paths most likely to regress:
   - different-dimension create + configless pin + Qdrant healthy → old collection dropped,
     pin re-pinned at the new dimension, create succeeds;
   - different-dimension create + configless pin + Qdrant raising → pin retained, create
     rejected with the existing typed conflict;
   - **same-dimension** create + configless pin → `delete_collection` is never awaited and the
     pin is unchanged (guards the §2 non-goal: ordinary CRUD stays Qdrant-independent);
   - different-dimension create + configless pin + a live sibling config racing in → teardown
     skipped by the live-config recheck.
6. Retain the File-RAG runtime mismatch guard tests at
   `backend/tests/unit/test_embedding_pin.py:329-350` unchanged.

## 9. Risks and Rollback

Holding a transaction-scoped advisory lock across Qdrant latency serializes config creation
for one project/kind; teardown is rare and bounded, and Q-7's explicit timeout caps the worst
case. If Qdrant succeeds but pin clearing fails, the retained pin fails closed and the next
retry observes absence (Q-4). Rollback is code-only but reopens the unsafe acceptance window.

The sharpest regression risk is Q-5's create-path retry: a create that today never touches
Qdrant would begin to, if the "different dimension **and** configless" guard is written too
loosely. Test plan item 5's same-dimension case exists to pin that guard.

`QdrantStore._collection_dimension` fails open — it returns `None` for named-vector
collections, so `_assert_dimension` is skipped rather than raising
(`backend/contexts/knowledge/infrastructure/qdrant_store.py:95-105`). This task's correctness
does not depend on that guard (the pin, not the physical probe, is authoritative), but it
means the runtime guard is not a reliable second line of defense behind a pin bug. Recorded as
FU-2, not addressed here.

## 10. Acceptance Criteria

- [ ] AC-1: Qdrant-failure regressions for all three products fail before the fix and pass
  after.
- [ ] AC-2: No final-config delete path clears its pin before collection deletion/absence is
  confirmed.
- [ ] AC-3: A teardown failure leaves the prior pin intact and blocks every incompatible
  create with the existing typed dimension conflict.
- [ ] AC-4: Deleted and already-absent collections permit pin clearing; failures never report
  `collection_dropped=true`.
- [ ] AC-5: Concurrent create/teardown integration proves a live collection cannot be
  dropped and a different dimension cannot be accepted early.
- [ ] AC-6: Configless retained pins are retried durably and per-item failures do not abort
  the sweep.
- [ ] AC-7: A different-dimension create against a configless pin retries teardown first and
  succeeds when Qdrant is healthy; it is rejected with the existing typed conflict when
  teardown fails.
- [ ] AC-8: A same-dimension create against a configless pin, and any create with no pin or
  with live configs, issues no Qdrant call — ordinary config CRUD stays Qdrant-independent.
- [ ] AC-9: The teardown's Qdrant client carries an explicit timeout, so a hung Qdrant cannot
  hold the advisory lock and its Postgres connection open indefinitely.
- [ ] AC-10: The DOM-4/W5 docstrings on all three drop helpers describe the ordering the code
  actually has, and record the Q-4 trade.
- [ ] AC-11: Existing runtime dimension guards, unit/integration tests, backend lint, format,
  and type checks pass.

## 11. SRS Delta

None. This restores [R10.06] and [R11.19].

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1:** Consolidate the triplicated teardown lifecycle behind an application port/helper,
  keeping product-specific collection stores and typed conflict errors. Deferred from this
  task's §7.5 by Q-6. The duplication is why one bug had to be fixed three times; the
  regression tests this task adds will hold across the refactor.
- **FU-2:** `QdrantStore._collection_dimension` fails open for named-vector collections,
  silently skipping `_assert_dimension`
  (`backend/contexts/knowledge/infrastructure/qdrant_store.py:95-105`). Pre-existing; decide
  whether the physical guard should fail closed instead.
