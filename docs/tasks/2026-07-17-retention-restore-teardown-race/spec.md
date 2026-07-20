---
type: bugfix
status: implemented
created: 2026-07-17
requirements: [R8.12, R8.13, R10.06, R11.12]
---

# Retention can erase sources for a concurrently restored project

## 1. Summary

This dossier remediates F-7 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md` and the
same ordering defect in immediate admin GDPR deletion. Retention selects eligible project
ids, irreversibly purges their external source infrastructure, and only later conditionally
hard-deletes their database rows (`backend/app/workers/tasks/retention.py:195-243`). A
concurrent restore can make the delete affect zero rows after the external data is gone.

- **Goal:** perform external teardown only for project deletion decisions that are already
  committed and cannot be reversed by a concurrent restore.
- **Non-goals:** change the 60-day retention period, make soft delete irreversible, or add a
  general lifecycle outbox when the existing orphan sweep can provide durable recovery.

## 2. Observed vs Expected

- **Observed:** retention selects project/org candidates without a lock/claim, purges their
  MinIO/Qdrant data, then executes a fresh `deleted_at IS NOT NULL` delete predicate
  (`backend/app/workers/tasks/retention.py:195-243`). Restore is a conditional update that
  clears `deleted_at` (`backend/contexts/tenancy/infrastructure/repositories.py:387-394`). If
  restore wins between those steps, the active row survives without its sources.
- **Expected:** [R8.12] hard-deletes only after the recovery period and includes RAG cleanup,
  while [R8.13] permits restore while the soft-deleted row exists
  (`REQUIREMENTS.md:343-353`). External data for a live/restored project must remain usable
  under [R10.06] and [R11.12].

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Durable `purging` state/outbox or DB-first hard delete? | Atomically `DELETE ... RETURNING`, commit, then tear down only the returned projects; use the existing external-store orphan sweep for crash/failure recovery. | Committed row absence is an irreversible deletion decision and already makes leftover external data discoverable (`backend/app/workers/tasks/retention.py:304-321`, whose live set is every `projects` row regardless of `deleted_at`). It needs no migration and holds no DB lock across network calls. |
| Q-2 | How are org cascades handled? | Capture candidate org-to-project membership, delete eligible orgs with `RETURNING`, and purge only projects belonging to orgs whose deletion committed. | Preselected project ids alone are unsafe; the returned deleted parent is the authoritative cascade decision. |
| Q-3 | Is immediate admin GDPR deletion in scope? | Yes. | It purges selected projects before the later conditional hard delete and races the same restore paths (`backend/contexts/tenancy/application/account_deletion_service.py:168-230`). |

## 4. Reproduction

1. Soft-delete a project beyond the retention cutoff with File RAG/Knowledge Map sources.
2. Let retention select its id at `backend/app/workers/tasks/retention.py:205-218`.
3. Before the later SQL delete, restore the project so `deleted_at=NULL`.
4. Retention has already purged source infrastructure, but its conditional delete affects
   zero rows; the project is active and its data cannot be reconstructed.

The current test asserts only that candidate ids are forwarded to teardown
(`backend/tests/unit/test_retention_deep.py:259-293`).

## 5. Root Cause Analysis

An unlocked eligibility read is treated as ownership of deletion, and irreversible network
work runs before the database records an irrevocable outcome. The whole retention policy also
runs inside one transaction, so external latency occurs before commit
(`backend/app/workers/tasks/retention.py:603-617`). The root correction is ordering: commit
the authoritative delete first, then run idempotent teardown. Row absence plus external-store
enumeration is the durable retry signal.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** File-RAG/Knowledge Map source blobs and the File-RAG project collection
  purged by `backend/contexts/knowledge/application/config_service.py:570-631`, for direct
  project or parent-org retention.
- **Confirmed sibling:** immediate admin GDPR deletion uses the same select, purge, then
  conditional delete ordering
  (`backend/contexts/tenancy/application/account_deletion_service.py:168-230`).
- **Confirmed surface:** org restore and project restore can race parent/direct retention;
  org restore also restores deleted child projects
  (`backend/contexts/tenancy/application/org_service.py:200-222`).
- **Cleared:** individual knowledge-config deletion commits its database deletion before
  external teardown (`backend/app/api/v1/rag.py:367-385`;
  `backend/app/api/v1/knowmap.py:345-361`).
- **Existing debt:** `ProjectService.restore` ignores the repository boolean and may audit a
  no-op (`backend/contexts/tenancy/application/project_service.py:179-198`).

## 7. Fix Design

1. Refactor destructive tenancy retention into a short database phase and a post-commit
   external phase. Do not keep the outer policy transaction open across MinIO/Qdrant calls.
2. Direct projects: atomically delete still-qualified rows with `DELETE ... RETURNING id` and
   commit. Only returned ids enter external teardown.
3. Orgs: capture candidate org-to-project mapping, delete still-qualified orgs with
   `RETURNING id`, commit, and select teardown project ids only for returned org ids. Preserve
   retention guards and batch limits.
4. After commit, call `KnowledgeFacade.purge_project_source_infra_batch` for the committed
   ids, isolate partial failures, then run/retain `_purge_rag_source_orphans` in the same
   retention cycle. A crash between commit and purge is healed by the next sweep because the
   project row is absent.
5. Apply the same DB-first/post-commit sequence to immediate admin GDPR deletion. Persist the
   hard-delete/audit decision before external work.
6. Make audit/metrics distinguish committed hard deletes, full teardown, partial teardown,
   and sweep recovery. Never claim purge for a project whose delete returned no row.
7. Preserve exact UUID prefix/collection isolation and idempotent external adapters.

Reuse the short-lived transaction pattern in retention
(`backend/app/workers/tasks/retention.py:287`, which already opens its own session per unit
of work precisely to avoid holding the policy transaction across network calls),
`DELETE ... RETURNING`, existing batch teardown, and the row-independent orphan sweep
(`backend/app/workers/tasks/retention.py:304-321`).

### Security Considerations

This is destructive tenant-integrity loss. DB-first ordering creates a bounded data-remanence
interval after logical deletion, minimized by same-pass teardown and the next orphan sweep;
it never exposes one tenant's data to another. External purge must remain keyed by exact
project UUID and audit must not include source contents or storage credentials.

## 8. Regression Test Plan

1. Deterministic unit seam: restore the selected project before the hard-delete statement;
   assert zero returned rows and no teardown call. This fails against current ordering.
2. Real-Postgres barrier tests: restore commits first -> row remains/no purge; delete commits
   first -> restore affects zero rows and post-commit purge runs.
3. Repeat for org cascade, user-facing/admin restore, and immediate admin GDPR deletion.
4. Crash test: hard delete commits and the process stops before purge; the next orphan sweep
   removes the prefix/collection.
5. Partial MinIO/Qdrant failure remains discoverable and is retried without affecting other
   projects.

## 9. Risks and Rollback

The database row disappears before external erasure, so privacy deletion becomes briefly
eventually consistent; same-pass teardown plus the already-scheduled orphan sweep bounds it.
Batch project-id capture for org cascades must be tested against actual FK behavior. No
migration is required. If rollback is necessary, suspend destructive retention rather than
restore the known purge-before-decision ordering.

## 10. Acceptance Criteria

- [x] AC-1: The restore-race regression fails before the fix and passes after.
  `TestRestoreRaceOrdering::test_no_teardown_when_restore_wins` failed against the old
  ordering with "Expected purge_project_source_infra_batch to not have been awaited. Awaited
  1 times." and passes after.
- [x] AC-2: External teardown receives only project ids whose direct or parent-org hard
  deletion has committed. `TestRagSourceTeardownWiring::test_tears_down_direct_and_org_cascade_projects`,
  `test_admin_gdpr_hard_delete_returns_committed_projects`.
- [x] AC-3: If restore wins before hard deletion, the row remains and no source blob/vector
  is removed; if deletion wins, restore cannot reactivate it and teardown runs post-commit.
  `test_no_teardown_when_restore_wins`, `test_admin_gdpr_hard_delete_skips_restored_project`,
  `test_admin_hard_delete_commits_before_teardown`.
- [x] AC-4: The invariant covers direct projects, org cascades, user/admin restore, and
  immediate admin GDPR deletion. `tests/integration/test_retention_restore_barrier.py` covers
  all four against real Postgres, in both race directions.
- [x] AC-5: A crash or partial external failure after DB commit is recovered idempotently by
  the existing orphan sweep.
  `test_teardown_crash_leaves_the_project_discoverable_by_the_orphan_sweep` and
  `test_partial_teardown_failure_does_not_affect_other_projects`.
- [x] AC-6: No DB transaction or row/advisory lock is held during MinIO/Qdrant teardown.
  Retention tears down on a separate session after the delete committed; the admin path commits
  first, pinned by `test_admin_hard_delete_commits_before_teardown`. Caveat in D-1: the outer
  policy session stays open but holds no lock on the deleted rows.
- [x] AC-7: Audit/metrics distinguish committed delete and full/partial/recovered teardown and
  never report purge for a restored/non-deleted project. `retention.rag_source_infra.torn_down`
  carries `projects_committed`/`projects_purged`/`projects_owed`; the facade emits
  `rag_source_teardown_partial` with its `source`; ids can only come from `DELETE ... RETURNING`.
- [x] AC-8: Exact project UUID isolation, focused unit/integration tests, backend lint,
  format, and type checks pass. UUID isolation is unchanged and pinned by
  `test_purge_primitive_removes_both_buckets_and_drops_collection` (a second tenant's blobs
  survive).

## 11. SRS Delta

None. This restores [R8.12], [R8.13], [R10.06], and [R11.12].

## 12. Deviation Log

- D-1: §7.1 required the outer policy transaction not to stay open across MinIO/Qdrant calls.
  `_POLICIES` passes every policy one session and `retention_sweep` owns its transaction
  (`backend/app/workers/tasks/retention.py:671-674`), so `_purge_soft_deleted_tenancy` instead
  opens its own short transaction via `get_sessionmaker()` for the destructive phase, reusing
  the pattern at `backend/app/workers/tasks/retention.py:287`. The policy session stays open
  but holds no lock on the deleted rows, since those committed on a different session. Fully
  closing it would mean reshaping the uniform policy signature; recorded as FU-8 instead.
- D-2: §7.5 said to apply the DB-first sequence to admin GDPR deletion but not where the
  commit boundary goes. `prepare_hard_delete` now returns `set[uuid.UUID]` instead of `None`
  and performs no external work; the new
  `AccountDeletionService.purge_hard_deleted_project_sources` runs the teardown, and
  `AdminService.hard_delete_user` commits between the two
  (`backend/contexts/identity/application/admin_service.py:271-277`). This is not a break with
  the transaction convention: `db_session` documents and supports the mid-request commit
  (`backend/shared_kernel/db/session.py:98-102`). The teardown stays in the tenancy context so
  identity gains no dependency on knowledge.
- D-3: the partial/failed teardown verdict was stated once per hard-delete path. A
  `check-quality` DRY finding moved it into
  `KnowledgeFacade._purge_source_infra_with_store` behind a required `source` keyword
  (`backend/contexts/knowledge/interfaces/facade.py:460-518`), so the two paths cannot drift.
  The orphan sweep gains the same partial warning. Callers keep only their catastrophic-failure
  handling, which genuinely differs (own transaction vs. request session rollback).
- D-4: not in the spec. The org-to-project mapping read and the org delete each evaluated the
  same eligibility predicate, so they were two READ COMMITTED snapshots: an org that became
  eligible in between would be deleted without appearing in the mapping, and its
  cascade-deleted projects would never be torn down. Both sites now read the doomed org ids
  once and reuse that literal list, re-checking the conditions inside the delete so a restore
  in between is still honoured. Applies to retention and to admin GDPR deletion.
- D-6: not in the spec, found in review. `prepare_hard_delete` set `owner_user_id = NULL` on
  every surviving project of the deleted user, which violates the `projects_owner_xor` check
  constraint (migration 0002) for a project with no `owner_org_id`. Admin GDPR hard-delete
  therefore aborted with `CheckViolationError`, a 500, for exactly the "cascade was incomplete"
  case the method's docstring says it handles. Reassigning the owner alone would have traded
  that for a `uq_projects_user_name` violation whenever the admin already owned a same-named
  live project, so the statement now reassigns *and* soft-deletes: the XOR is satisfied and the
  row leaves that partial unique index, which covers only live rows. Agreed with the user, who
  chose this over renaming on collision. It is also the more honest outcome, since the owner
  has been erased and the project should enter the normal recovery window rather than silently
  become the admin's.
- D-7: not in the spec, found in review. The sweep-count audit was written on the policy
  session while the deletes had moved to their own transaction, so a failure of the outer
  transaction would keep the deletions but lose the record of them. `audit.emit`'s contract
  (`backend/shared_kernel/audit.py:116-122`) is that the audit write shares the unit of work
  with the domain change; the summary now commits with the deletes, and the teardown summary
  with the per-project purge audits it describes.
- D-8: not in the spec, found in review. `_teardown_committed_projects` returned 0 when only
  the commit failed, so the audit claimed every project was still owed after its blobs had
  already been destroyed. It now reports the count actually purged.
- D-9: consequence of D-7, caught in self-audit. `audit.emit` queues a realtime tail event on
  whichever session wrote the row (`backend/shared_kernel/audit.py:137`), and `retention_sweep`
  flushes only the policy session it passed in. Moving the audits onto the sessions this policy
  opens for itself therefore dropped them from the audit stream while still writing the rows.
  Both sessions are now flushed explicitly, pinned by
  `TestTenancySweepFlushesItsAuditTail`. See FU-11 for the same latent gap elsewhere.
- D-5: not in the spec. A catastrophic teardown on the admin path left its partial audit writes
  on the request session and swallowed the error, so `db_session`'s trailing commit raised and
  turned an already-committed GDPR purge into a 500. The handler now rolls back first.

## 13. Follow-ups

- FU-1: Correct restore services that emit success audit events when their conditional update
  changes no row.
- FU-2: Introduce a platform outbox only if external teardown observability/retry policy grows
  beyond what row absence plus store enumeration can represent.
- FU-3: `RetentionService.purge_once` removes MinIO attachment blobs before the message
  `DELETE` and before commit (`backend/contexts/conversation/application/retention_service.py:77-93`).
  The victim set is chosen by an immutable `created_at < horizon` predicate, so there is no
  restore race, but a rollback between the blob removal and the commit leaves live rows whose
  objects are gone.
- FU-4: `MessageService` hard-deletes the row, then removes attachment blobs, then audits, all
  before the route commits (`backend/contexts/conversation/application/message_service.py:354-365`;
  commit at `backend/app/api/v1/messages.py:482`). Same rollback exposure as FU-3.
- FU-5: `WorkspaceService` removes the workspace-file object after the row delete but before
  commit, and its `sha256_ref_count == 0` check reads its own uncommitted transaction
  (`backend/contexts/agents/application/workspace_service.py:178-186`); the upload path's
  orphan cleanup at line 144 has the same shape.
- FU-6: `PromptStudio` reference-file delete removes the object before commit
  (`backend/contexts/prompt_studio/application/file_service.py:141-142`).
- FU-7: `_purge_source_infra_with_store` reports only a purged count, so callers infer partial
  failure arithmetically and cannot name the projects still owed
  (`backend/contexts/knowledge/interfaces/facade.py:481-483`).
- FU-8: `_purge_soft_deleted_tenancy` is ~120 lines with the destructive phase nested inside a
  table loop inside a transaction block; extracting
  `_delete_expired_tenancy_rows(sm, ...) -> tuple[int, set[uuid.UUID]]` would leave the policy
  as guards, delete, teardown, audit. Relates to D-1.
- FU-11: the audit-tail gap D-9 fixed is latent in two sibling policies that predate this task.
  `_purge_messages` (`backend/app/workers/tasks/retention.py:90-99`) runs
  `RetentionService.purge_once` on chunk sessions it opens itself, and that emits
  `message.purged_by_retention`; `_retry_pending_collection_teardowns` does the same with its
  per-pin sessions. Neither is flushed, so those rows are written but never published. A shared
  helper that opens a short-lived session and flushes it on exit would close all three.
- FU-10: D-6 soft-deletes a surviving personal project as part of reassigning it, so it now
  enters the 60-day window and retention will eventually hard-delete it and tear down its
  sources. That is the intended outcome, but nothing notifies the admin that it happened. If
  admin GDPR deletion becomes routine, the response should report the reassigned-and-deleted
  project ids so an operator can restore any that mattered.

## 14. Verification Status

Mechanical gates pass: `ruff check .`, `ruff format --check .` and `mypy .` over 802 files,
plus the full backend unit suite (5486 passed, 6 skipped). No migration, no API contract
change, no frontend change, so those gates are N/A.

The whole Regression Test Plan is covered. §8.1 at the deterministic unit seam; §8.2-§8.5 in
`tests/integration/test_retention_restore_barrier.py` (12 tests) against a real Postgres 16,
run repeatedly to confirm they leave no rows behind and do not depend on a fresh database.

Several tests were checked against the pre-fix code to confirm they are regression tests
rather than characterization tests. `test_restore_landing_mid_pass_cancels_the_teardown` and
`test_partial_teardown_failure_does_not_affect_other_projects` fail on the old ordering;
the three `admin_gdpr` tests fail with `CheckViolationError` when D-6's line is put back. The
remaining barrier tests pass either way by design: they pin the Postgres property and the
cascade bookkeeping the fix relies on, not the ordering itself.

Note on the probe: `_run_retention` patches the teardown with a callable whose `source`
keyword is optional. That is deliberate. With a required keyword the probe fails to bind
against the pre-fix call shape, the old code's blanket `except Exception` swallows the
TypeError, and the suite goes green against the very bug it exists to catch.
