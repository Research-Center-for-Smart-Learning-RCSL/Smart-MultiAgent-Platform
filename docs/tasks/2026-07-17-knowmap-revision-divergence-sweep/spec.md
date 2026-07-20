---
type: bugfix
status: approved
created: 2026-07-17
requirements: [R11.12]
---

# Knowledge Map revision finalization has no durable recovery

## 1. Summary

This dossier remediates F-4 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md`.
Knowledge Map mutations durably increment `corpus_revision`, but the post-build stamp and
follow-up enqueue run after graph commit in a logged-and-swallowed best-effort block
(`backend/app/workers/tasks/knowmap.py:420-433`). A transient DB/queue fault can therefore
leave a committed revision absent indefinitely.

- **Goal:** converge every live Knowledge Map's built revision to its committed corpus
  revision without requiring another mutation or manual rebuild.
- **Secondary goal (Q-3):** make a failure of the existing stuck-`RUNNING` reconciler
  observable, since this dossier's state policy depends on that reconciler working.
- **Non-goals:** replace revision-keyed Arq deduplication, auto-retry failed graph builds, add
  a general transactional-outbox subsystem, or change what the reconciler itself recovers.
  The stuck-`RUNNING` work here observes; it does not recover.

## 2. Observed vs Expected

- **Observed:** `_finalize_build_revision` stamps the built revision, rereads the config, and
  enqueues a follow-up in a separate post-build session
  (`backend/app/workers/tasks/knowmap.py:443-468`). Its caller swallows all failures, and the
  enqueue helper also swallows queue errors
  (`backend/contexts/knowledge/application/knowmap_triggers.py:33-63`). Worker settings have
  no revision-divergence sweep (`backend/app/workers/main.py:321-327`).
- **Expected:** [R11.12] requires a rebuild on every document-set change
  (`REQUIREMENTS.md:491`). The prior revision dossier requires eventual reflection of each
  committed mutation
  (`docs/tasks/2026-07-14-knowmap-build-dedup-revision/spec.md:57-61`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Transactional outbox or periodic convergence? | Keep finalization as the fast path and add a bounded periodic divergence sweep over existing columns. | `corpus_revision > built_corpus_revision` is already a durable, queryable statement of missing work; revision-keyed job ids make repeated enqueue safe. |
| Q-2 | Which states should the sweep enqueue? | Live `IDLE` configs only, targeting the latest revision. | In-flight configs already have a completion path; automatically retrying `FAILED` could create outage storms and changes failure policy. Verified during approval: `RUNNING` is already in the reconciler's `_STUCK_STATES` (`backend/contexts/knowledge/application/graphrag_reconciler.py:79-83`) and the knowmap loop that drives it runs every minute (`backend/app/workers/graphrag_reconciler.py:174-213`, `backend/app/workers/main.py:339`), bounded by the 10-minute lock TTL (`backend/contexts/knowledge/application/graphrag_builder.py:64`). Enqueueing a `RUNNING` config would be rejected by the state whitelist at `backend/contexts/knowledge/application/graphrag_builder.py:224-231` anyway. |
| Q-3 | Should the sweep also detect configs stuck in `RUNNING`? | Yes, but observation only: log a warning, never enqueue. | The reconciler is the recovery mechanism (Q-2); this is a net for the reconciler itself failing. Enqueueing would be rejected and would only produce noise. |
| Q-4 | What time source drives the `RUNNING` staleness check? | Add a new `build_started_at` column, stamped at the `RUNNING` transition. | `last_build_at` is unusable: the `RUNNING` transition passes neither `built_at` nor `stamp_started_at` (`backend/contexts/knowledge/application/graphrag_builder.py:255-259`), and `set_state` only writes the column when given one of them (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:198-204`), so a `RUNNING` config still carries the *previous* build's timestamp. Using it would misfire immediately. |
| Q-5 | How bounded is each sweep tick? | Page size 50, hard cap 200 configs per tick. | Deliberately tighter than the house 500-per-page convention (`backend/app/workers/tasks/graphrag.py:466-507`): those sweeps enqueue cheap work, whereas each knowmap build drives Neo4j, Qdrant, and LLM extraction. A backlog drains over several ticks instead of one thundering herd. |
| Q-6 | Which tables get `build_started_at`? | Both `knowmap_configs` and `graphrag_configs`. | `set_state` is a shared port (`backend/contexts/knowledge/application/graphrag_ports.py:277-285`) implemented by both repositories, and the `RUNNING` transition lives in the shared builder. A knowmap-only column would force an asymmetric port. Migration `0058_graphrag_build_state_text.py:80-89` is the precedent for altering both tables together. |

## 4. Reproduction

1. Start build A for target revision N.
2. Commit a mutation that increments the config to N+1 while A runs.
3. Let A commit its graph, then force `_finalize_build_revision` or its enqueue to fail.
4. Observe `corpus_revision=N+1`, `built_corpus_revision<N+1`, and no queued work. A
   concurrently queued N+1 job may also have returned busy while A held the build lock.
5. With no further mutation/manual rebuild, the gap persists.

## 5. Root Cause Analysis

The revision counter is durable, but delivery is edge-triggered only: initial enqueue and
post-build follow-up are both best-effort events. No level-triggered process scans the durable
revision gap. The earliest correction is to treat the existing gap as pending work and
periodically reconcile it.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** every Knowledge Map document add/delete/reprocess/scan transition when
  finalization or enqueue has a transient failure. Deleted/quarantined content can remain in
  the graph; newly added content can remain absent.
- **Confirmed siblings:** initial post-commit enqueue sites share the swallowing helper,
  including manual rebuild, delete, scan, and ingest
  (`backend/app/api/v1/knowmap.py:414-423,566-609`;
  `backend/contexts/knowledge/application/knowmap_ingest_service.py:262-275`). The sweep
  recovers these gaps too.
- **Cleared:** revision allocation itself is transactional
  (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:234-250`).
- **Existing debt:** current tests cover only successful post-build follow-up
  (`backend/tests/unit/test_knowmap_build_dedup.py:139-171`). Two further debts were found
  during approval and are pulled into this task rather than deferred, because both actively
  obscure the recovery guarantee this dossier depends on:
  - The knowmap heal path has no direct coverage. The entire stuck-`RUNNING` guarantee rests
    on two lines (`backend/app/workers/graphrag_reconciler.py:225-226`) that no test asserts.
  - `backend/app/workers/tasks/graphrag.py:432-435` claims the cron only heals
    `FAILED_COMPENSATING` graphrag configs. It in fact covers three states across both
    graphrag and knowmap. This stale docstring caused a false gap analysis during approval.
- **Also noted, deliberately out of scope:** no cron registration or cadence is asserted
  anywhere in the suite; nothing tests `WorkerSettings.cron_jobs`. AC-5's worker coverage
  therefore sets the precedent rather than following one.

## 7. Fix Design

1. Add a repository query for live, `IDLE` configs where
   `corpus_revision > COALESCE(built_corpus_revision, 0)`. Exclude revision zero to avoid
   empty-config work. Takes explicit `limit`/`offset` so the caller pages.
2. Add a bounded, paged worker/cron (one-minute cadence) that enqueues the latest revision
   with `enqueue_knowmap_build`. Page size 50, hard cap 200 per tick (Q-5); log when the cap
   truncates a tick. Isolate and report failures per config.
3. Retain `_finalize_build_revision` as the low-latency path. The sweep is the durable
   backstop for both finalizer and initial-enqueue failure.
4. Reuse `knowmap:build:{config_id}:{target_revision}`; repeated sweeps for the same gap are
   idempotent while Arq retains or runs the job
   (`backend/contexts/knowledge/application/knowmap_triggers.py:18-55`).
5. Record sweep counts/failures without corpus contents or prompt data.

### Stuck-`RUNNING` observation (Q-3, Q-4, Q-6)

6. Migration `0059` adds nullable `build_started_at TIMESTAMPTZ` to both `knowmap_configs`
   and `graphrag_configs`, following the both-tables precedent of
   `backend/alembic/versions/0058_graphrag_build_state_text.py:80-89`. Backfill is not
   required: the column is only read for configs that entered `RUNNING` after deploy, and a
   `NULL` is treated as "not yet observable", never as "infinitely stale".
7. Extend the `set_state` port (`backend/contexts/knowledge/application/graphrag_ports.py:277-285`)
   with `stamp_started_at: bool = False`, honored by both repository implementations, and pass
   it from the shared builder's `RUNNING` transition
   (`backend/contexts/knowledge/application/graphrag_builder.py:255-259`) so the stamp is
   durable in the same commit as the state (`:280`).
8. The sweep additionally selects live configs in `RUNNING` whose `build_started_at` is older
   than the staleness threshold and logs one warning per config. It never enqueues them.
   Threshold is 60 minutes, derived with margin from the worst legitimate case: a build may
   legitimately run to `KNOWMAP_BUILD_TIMEOUT_S` = 30 minutes
   (`backend/app/workers/tasks/knowmap.py:48`), after which reconciler recovery costs at most
   the residual 10-minute lock TTL plus one cron minute.

### Reuse inventory

- Repository state-list shape: `backend/contexts/knowledge/infrastructure/knowmap_repositories.py:209-220`.
- Paged sweep with break-on-short-page: `backend/app/workers/tasks/graphrag.py:466-507`.
- Per-item failure isolation (rollback first, then bind-and-log, then continue):
  `backend/app/workers/tasks/orchestration.py:248,268-274`.
- Terminal one-line tick log and terse return string:
  `backend/app/workers/tasks/workflow_watchdog.py:82-85`.
- Minute-cron singleton registration via arq's cron lock: `backend/app/workers/main.py:339`.
- Sweep unit-test template (fake repo/session/redis, `monkeypatch` injection, asserting
  `db.rollbacks` to pin isolation): `backend/tests/unit/test_graphrag_silence_sweep.py`.
- Per-tick cap assertion pattern: `backend/tests/unit/test_teardown_retry_sweep.py:79-88`.

### Security Considerations

The query must include live-config/project scoping and enqueue only server-derived config ids
and revisions. This closes stale-data lifecycle exposure after delete/quarantine. Logs and
metrics must not include document text.

## 8. Regression Test Plan

1. Extend `backend/tests/unit/test_knowmap_build_dedup.py`: seed an `IDLE` config with
   `corpus_revision=2`, `built_corpus_revision=1`, force the fast path to fail, run the sweep,
   and assert target 2 is enqueued.
2. Run the sweep twice and assert the same revision-keyed id causes no semantic duplicate.
3. Repository tests exclude caught-up, deleted, revision-zero, RUNNING, and FAILED configs.
4. Verify one enqueue failure does not prevent later divergent configs from being processed,
   asserting the rollback count as `test_graphrag_silence_sweep.py` does.
5. Add worker registration/cadence coverage, asserting the sweep appears in both
   `WorkerSettings.functions` and `WorkerSettings.cron_jobs`.
6. Assert the per-tick cap truncates at 200 and that paging stops on a short page.
7. Assert `build_started_at` is stamped on the `RUNNING` transition for both repository
   implementations, and that the migration's downgrade drops it from both tables.
8. Assert the stuck-`RUNNING` check warns past the threshold, stays silent within it, treats
   `NULL` `build_started_at` as not-observable, and never enqueues in any of those cases.
9. Add the missing knowmap heal coverage: `reconcile_once` runs the knowmap loop, and a
   knowmap config in `RUNNING` is reclaimed.

## 9. Risks and Rollback

A retained/running job may be offered again each tick, but the stable job id bounds work.
Existing divergent configs self-heal on the first deployed sweep, drained at up to 200 per
minute.

The `build_started_at` migration is additive and nullable, so old code runs on the new schema
(the forward-compatibility rule in `backend/CLAUDE.md`). Builds already `RUNNING` at deploy
time carry `NULL` and are never warned about, which is the intended fail-quiet direction: the
check is a net for a broken reconciler, and a false silence costs less than a false alarm.

Rollback removes the cron and query and downgrades the column; no data is lost, but the
lost-change window returns.

## 10. Acceptance Criteria

- [ ] AC-1: The finalizer-failure regression in section 8 fails before the fix and passes
  after.
- [ ] AC-2: A committed N+1 revision is enqueued within one sweep interval even when
  `_finalize_build_revision` raises or the initial enqueue failed.
- [ ] AC-3: The sweep selects only live, nonzero, `IDLE`, divergent configs and targets each
  config's latest committed revision.
- [ ] AC-4: Caught-up, deleted, revision-zero, in-flight, and FAILED configs are not enqueued.
- [ ] AC-5: Repeated ticks remain idempotent through the existing revision-keyed job id; one
  config failure does not abort the page/sweep.
- [ ] AC-6: Successful finalization still stamps the processed revision and immediately
  enqueues a newer revision.
- [ ] AC-7: Focused unit/repository/worker tests, backend lint, format, and type checks pass.
- [ ] AC-8: Each tick processes at most 200 configs across pages of 50, stops on a short
  page, and logs when the cap truncated the tick.
- [ ] AC-9: `build_started_at` is stamped durably at the `RUNNING` transition for both
  `knowmap_configs` and `graphrag_configs`, in the same commit as the state change.
- [ ] AC-10: Migration `0059` applies and downgrades cleanly against both tables.
- [ ] AC-11: A config in `RUNNING` past the 60-minute threshold produces exactly one warning
  per tick and is never enqueued; one within the threshold, or with a `NULL`
  `build_started_at`, produces neither.
- [ ] AC-12: Tests assert that `reconcile_once` drives the knowmap loop and that a knowmap
  config in `RUNNING` is reclaimed.
- [ ] AC-13: The stale cron docstring at `backend/app/workers/tasks/graphrag.py:432-435`
  states the actual coverage (three states, both graphrag and knowmap).

## 11. SRS Delta

None. This restores [R11.12]. The `build_started_at` column and the stuck-`RUNNING` warning
are operational observability, not user-visible behavior, so they define no new requirement.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- FU-1: Consider a shared transactional outbox only if other post-commit workers show the
  same level-triggered convergence gap and justify a platform-wide abstraction.
