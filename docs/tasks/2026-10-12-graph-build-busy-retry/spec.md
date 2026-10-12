---
type: bugfix
status: in-progress
created: 2026-10-12
requirements: [R11.12, R11.15, R11a.01]
depends_on: []
---

# A graph build that finds the config busy waits for its turn instead of failing

## 1. Summary

A build job that starts while another build of the same config holds the per-config lock
fails at once with `GraphRagBuildBusy` and is never retried. For a Knowledge Map that loses
a committed document change for up to an hour: when two documents are uploaded close
together, the second upload's build job fails busy, and the follow-up build the first
build enqueues when it finishes carries the same arq job id, so arq drops it as a duplicate
of the failed job's retained result. The second document stays `ready` but absent from
the graph until the result expires (3600 s) or someone rebuilds by hand. A Concept Map
build that loses the lock the same way is recorded as a failed job and flips the build-state
metric to `failed` while the config's real build is still running. Found during the live
verification of `docs/tasks/2026-07-07-graphrag-two-axis-redesign` (its FU-10, D-6).

## 2. Observed vs Expected

- **Observed** (2026-10-12, compose test stack, real Postgres/Neo4j/Qdrant/MinIO/Redis):
  two `.txt` documents uploaded back to back to one Knowledge Map. One build ran and
  covered only the first document. The worker logged
  `knowmap:build:<cfg>:2:knowmap_build failed, GraphRagBuildBusy`. Afterwards
  `corpus_revision` was 2 and `built_corpus_revision` 1, Redis held
  `arq:result:knowmap:build:<cfg>:2` with a TTL counting down from 3600 s, the
  `knowmap_revision_sweep` cron (every minute) did not produce a build, and the graph
  held no relation from the second document until a manual rebuild (which bumps the
  revision to a fresh job id) built it.
- **Expected** — [R11.12]: a Knowledge Map "rebuilds on document-set change (upload,
  delete, reprocess)". `2026-07-14-knowmap-build-dedup-revision` AC-2: "A document
  committed while a build is running is built into the graph without an unrelated later
  trigger." That dossier's D-7 also states that two rebuilds run as "two builds,
  serialized by the per-config Redis lock" (`:295-297`), and [R11a.01] that only one
  build per config runs at a time. The lock is meant to order builds, not to discard
  the second one.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should a build that finds the lock held be handled? | Raise arq `Retry(defer=...)`, so the same job stays queued and runs after the holder finishes. | Requester's choice. The follow-up and sweep enqueues for that revision then collapse onto the live, queued job instead of a retained failure, which is the serialization D-7 assumed; no extra builds are spent. Rejected: `keep_result=0` for build jobs, which makes any re-enqueue of an already-built revision run (and bill a BYO key for) a second build; and both together, which adds a second mechanism for the same gap. |
| Q-2 | How long may a busy job keep waiting? | Long enough to outlast the longest build: `defer` 30 s and `max_tries` such that the total wait is at least the build function timeout (`LOCK_TTL_S * 3` = 1800 s, `knowmap.py:61`, `graphrag.py:88`). | Requester's choice. A holder that runs to its timeout still lets the waiter in. Exhausting the tries falls back to today's failed result, which the revision sweep re-offers once `keep_result` lapses. Rejected: covering only the 600 s lock TTL, which still loses the second build behind any build longer than ten minutes. |
| Q-3 | Does the Concept Map build (`graphrag_build`) get the same handling? | Yes. | Requester's choice. Its job id is state- and time-keyed (`graphrag_triggers.py:31-49`), so its follow-up cannot collide with a busy job's id the way a Knowledge Map's does, but a busy Concept Map job is still recorded as a failure, drops the trigger that enqueued it, and flips the `failed` metric (`graphrag.py:421-424`) while the real build runs. One rule for both consumers of the shared lock. |

## 4. Reproduction

1. Bring up the compose test stack (`deploy/compose/docker-compose.yml` +
   `compose.test.yml`) with a Knowledge Map whose builder key group holds a working key
   (or the provider stub described in the blueprint's D-6).
2. Upload document A to the map, then upload document B before A's build finishes
   (back-to-back multipart uploads are enough; the build takes about a second with a stub
   and much longer with a real model).
3. Observe in the worker log that `knowmap:build:<cfg>:<n>` fails with
   `GraphRagBuildBusy`, and in Postgres that `corpus_revision > built_corpus_revision`
   after the first build finishes.
4. Wait several sweep ticks: no further build runs; `GET /api/knowmap-configs/{id}/graph`
   has no relation evidenced by document B. `redis-cli -n 1 ttl
   arq:result:knowmap:build:<cfg>:<n>` shows the blocking result.

Deterministic at the unit level by making the builder raise `GraphRagBuildBusy`
(`FakeLock(busy=True)` already exists, `backend/tests/unit/test_graphrag_builder.py:181-189`).

## 5. Root Cause Analysis

1. Document B's commit bumps the revision and enqueues `knowmap:build:{cfg}:{rev}`
   (`knowmap_ingest_service.py:504-517`; job id `knowmap_triggers.py:20-32`).
2. Job B starts while build A holds `graphrag:lock:{cfg}` (`redis_lock.py:44-45, 78-83`);
   `GraphRagBuilder.run` raises `GraphRagBuildBusy` before touching state
   (`graphrag_builder.py:189-190`).
3. **Root cause:** the worker treats a busy lock as a failure. `knowmap_build` logs and
   re-raises (`app/workers/tasks/knowmap.py:909-911`); arq stores any exception other
   than `Retry` as a failed result for `keep_result` = 3600 s (`app/workers/main.py:368`;
   arq `worker.py:623-629, 708-721` in 0.26.3, the local venv; the image runs 0.28.0,
   `backend/requirements.lock:22`, where `ArqRedis.enqueue_job` performs the same
   job-key-or-result-key existence check, read from the running container).
4. Build A finishes and `_finalize_build_revision` enqueues the follow-up for the current
   revision (`knowmap.py:963-964`), the same job id as B. arq's `enqueue_job` returns
   `None` when the job key or the result key exists (arq `connections.py:158-160`), which
   `enqueue_knowmap_build` reports as `DEDUPED` at debug level (`knowmap_triggers.py:86-92`).
5. Aggravating: `knowmap_revision_sweep` re-offers the same revision every minute
   (`knowmap.py:263, 357-360`) and is deduped the same way; its docstring assumes a lost
   follow-up's job id "has never been used" (`knowmap.py:317-320`). The F-12 tests model
   the enqueue as an `AsyncMock`, so arq's retained-result dedup was never exercised
   (`test_knowmap_build_dedup.py:141, 181-190`).

Correcting step 3, so a busy job waits instead of failing, removes the retained failure
that steps 4 and 5 collide with.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Knowledge Map document mutation that commits while a build of
  the same map runs: upload, reprocess, delete, and scan verdicts all enqueue a
  revision-keyed build (enqueue sites listed in the analysis: `knowmap_ingest_service.py:225,
  288`; `knowmap.py:412, 421, 555`; `api/v1/knowmap.py:600, 617, 639, 449`). A missed
  delete leaves a removed document's relations in the graph for the same hour. No data is
  persisted wrongly; the graph is stale until the next build.
- **Sibling suspects**:
  - `graphrag_build` (Concept Map) — **confirmed** same handling, different consequence:
    re-raise and `_set_state("failed")` on busy (`app/workers/tasks/graphrag.py:404-424`).
    In scope (Q-3).
  - The builder's second busy raise, lock acquired but state not resumable
    (`graphrag_builder.py:224-229`) — same exception type, so the fix applies to it too;
    retrying is correct there (the reconciler heals a stuck state).
  - The lock-lost raise during phase 1 (`graphrag_builder.py:354-355`) — **cleared**: it is
    caught inside the builder and becomes a `FAILED` build result
    (`graphrag_builder.py:370-379`), not a worker exception.
  - Admin reset with `force=false` (`graph_admin_reset.py:209-211`) — **cleared**: a
    synchronous API call that answers 409 to a person, not a queued job.
  - The reconciler skipping a held lock (`graphrag_reconciler.py:185-187`) — **cleared**:
    it only heals stuck states and runs again next tick.
  - Other arq jobs with deterministic `_job_id` that can fail transiently and later be
    re-enqueued under the same id — not audited here; FU-1.

## 7. Fix Design

1. In both `knowmap_build` (`app/workers/tasks/knowmap.py:901-911`) and `_run_build`
   for `graphrag_build` (`app/workers/tasks/graphrag.py:405-424`), catch
   `GraphRagBuildBusy` ahead of the generic `except Exception` and raise
   `arq.Retry(defer=BUILD_BUSY_RETRY_DEFER_S)` (30 s), logging at info level that the
   config is busy and the job will retry. The Concept Map path does not set the `failed`
   metric for a busy job; the config's real build owns the metric.
2. Register both functions with `max_tries` derived from the timeout and the defer,
   `ceil(timeout / defer) + 1`, in `app/workers/main.py:345, 355`, so the retry budget
   covers the longest build (Q-2). The constant lives beside `LOCK_TTL_S` so both workers
   share it.
3. Correct the premise in `knowmap_revision_sweep`'s docstring (`knowmap.py:317-324`)
   and in `knowmap_triggers.py`'s dedup note to say a revision's job id may have run and
   be waiting on a retry, which the sweep now collapses onto, and that a job which
   exhausted its retries is recovered once `keep_result` lapses.

No data repair: a map left stale by this defect is rebuilt by its next mutation, by the
sweep once the result lapses, or by a manual rebuild.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/unit/test_knowmap_build_busy.py`: `knowmap_build` whose builder raises
  `GraphRagBuildBusy` raises `arq.Retry` with the configured defer (fails today: the busy
  error propagates); a builder raising any other exception still propagates it unchanged;
  `_finalize_build_revision` is not called for a busy job.
- `backend/tests/unit/test_graphrag_build_busy.py` (or beside existing `graphrag_build`
  worker tests): the same for `graphrag_build`, plus the build-state metric is not set
  to `failed` for a busy job (fails today: `graphrag.py:422`).
- A registration test: `knowmap_build` and `graphrag_build` are registered with
  `max_tries * defer >= timeout`.
- `backend/tests/integration/` (db tier, real Redis and arq): enqueue
  `knowmap:build:{cfg}:2` while a test-held `graphrag:lock:{cfg}` exists, run the worker
  for one job, release the lock, enqueue the same id again (the finalize follow-up), and
  assert the second enqueue collapses onto the retrying job and the job then completes.
  Fails today: the first run stores a failed result and the second enqueue returns `None`
  with nothing queued.

## 9. Risks and Rollback

- A busy job now occupies a queue slot for up to the build timeout instead of failing;
  `max_jobs` is 50 (`main.py:367`) and only one waiter per revision can exist, since a
  duplicate enqueue collapses onto it.
- The Concept Map job id is state- and time-keyed, so a waiting Concept Map job runs
  after the holder as a delta build. That is an extra build compared with today's
  dropped trigger, which is the intent.
- Exhausted retries behave exactly as today.
- Rollback: revert the commit; no schema or data change.

## 10. Acceptance Criteria

- [x] AC-1: the regression tests from §8 fail before the fix and pass after.
  Before `3388bd80`, the unit busy tests failed with `GraphRagBuildBusy` propagating and
  the registration test could not import the retry budget; the db-tier test failed with
  arq `FailedJobs: 1 job failed GraphRagBuildBusy` (arq 0.28.0, the image version, in
  `backend-web` on the compose stack). All pass after.
- [x] AC-2: a `knowmap_build` or `graphrag_build` job that finds the config busy is retried
  by arq after a defer instead of recorded as failed, and a different exception is still
  recorded as a failure. `test_knowmap_build_busy.py`, `test_graphrag_build_busy.py`.
- [x] AC-3: while a busy Knowledge Map job waits, the follow-up enqueue for the same
  revision collapses onto it, and the second document of two back-to-back uploads is in
  the graph after both builds finish, with no manual rebuild (db-tier test, plus the §4
  reproduction re-run on the compose stack). `test_knowmap_build_busy_retry.py`; live
  re-run in D-3.
- [x] AC-4: the retry budget covers the build timeout for both functions (registration
  test). `test_busy_retry_budget_outlasts_the_build_timeout`; see D-1.
- [x] AC-5: a busy Concept Map job does not set the `failed` build-state metric.
  `test_busy_concept_map_build_does_not_report_failed`.
- [ ] AC-6: backend lint, typecheck and tests (db tier included) pass in CI.

## 11. SRS Delta

None. [R11.12] and [R11a.01] already require the rebuild on document change and one build
per config at a time.

## 12. Deviation Log

- **D-1. The registration test asserts `(max_tries - 1) * defer >= timeout`, not
  `max_tries * defer`.** arq runs a try only while its number is at most `max_tries`
  (arq 0.26.3 `worker.py:544-545`), so a job waits through `max_tries - 1` defers. The stricter
  form is what §7 step 2's `ceil(timeout / defer) + 1` was chosen to satisfy:
  61 tries, 60 defers of 30 s, 1800 s, equal to both build timeouts. D-4 later raised
  the budget to 65 tries to hold the crash allowance.
- **D-2. The db-tier test skips the remaining defer by re-scoring the queued job.** It
  sets the job's score in the queue's sorted set to now after the lock is released,
  rather than waiting 30 s or patching the defer constant. The length of the defer plays
  no part in what the test checks, and patching the constant would have made the test
  error with `AttributeError` before the fix instead of failing for the documented reason.
- **D-3. The §4 re-run's graph check does not discriminate on its own.** On the compose
  stack (branch code, real arq worker, OpenAI boundary stubbed as in the blueprint's D-6),
  two back-to-back uploads produced: job `knowmap:build:<cfg>:2` logged `busy, retrying
  in 30s` and arq `retrying job in 30.00s`; build 1 finished and its follow-up for
  revision 2 collapsed onto the waiting job (job `:2` ran exactly twice, `try=2` the
  second time); `built_corpus_revision` reached 2 with no manual rebuild, and Redis held
  only success results for both ids. In that run build 1's delta read already included
  the second document, so the graph held its relation after build 1 as well. The evidence
  for AC-3 is therefore the revision and arq-key trace plus the db-tier test, not the
  graph contents.
- **D-4. Busy tries are counted apart from crash re-runs (requester's choice after
  `/code-review`).** arq applies one `max_tries` to `Retry`, to a job cancelled at
  shutdown, and to a job whose worker died so its in-progress key expired (arq 0.26.3
  `worker.py:445-458, 608-620`). §7 step 2's budget of 61 would have let a build that
  kills its worker (an OOM, for example) re-run 61 times instead of the worker default
  of 5, each run spending the tenant's provider key. `app/workers/tasks/build_retry.py`
  now counts busy tries per job id in Redis (`graphrag:busy_tries:<job_id>`, 48 h TTL),
  and a job that has run more than `BUILD_CRASH_MAX_TRIES` (5) times without being busy
  fails with `BuildCrashLoopError` before building. `max_tries` is
  `ceil(timeout / defer) + 5` (65), defined once in that module rather than in each
  worker. A Redis error while counting fails open to the busy budget, the pre-fix bound.
  For `graphrag_build`, `_run_build` now re-raises the busy error and the job entry
  point turns it into `Retry`, since only the entry point has arq's `ctx`. Tests:
  `test_build_retry.py`, and the db-tier test asserts the counter on real Redis.
- **D-5. Other `/code-review` findings.** Three went into follow-ups: the gauge on a
  waiter's replica (FU-4, extended), budget exhaustion with several waiters or a
  crash-held lock (FU-5), and the per-retry setup cost (FU-6). The db-tier test now
  closes its arq workers. One finding proposed fixing the dedup at the job id rather
  than retrying; that reopens Q-1 and was not taken. Another noted that busy exhaustion
  has no WARNING. arq already logs `max retries ... exceeded` at warning level
  (0.26.3 `worker.py:547`), so that note is covered by FU-5. The test fakes shared by
  the three new test files were left duplicated, since each file is self-contained.

## 13. Follow-ups

- **FU-1.** Other arq jobs that use a deterministic `_job_id` and can fail transiently are
  exposed to the same retained-result dedup for `keep_result` (3600 s). Not audited here.
- **FU-2.** A job that exhausts its busy retries is still recovered only after
  `keep_result` lapses. If that turns out to matter, the sweep could detect a failed
  retained result for the revision it is about to offer and clear it.
- **FU-3.** `2026-07-14-knowmap-build-dedup-revision` D-7 states builds are "serialized by
  the per-config Redis lock"; that becomes true with this fix, and its AC-2 gains the live
  coverage its FU-3 deferred.
- **FU-4.** `_run_build` sets the Concept Map gauge to `running` before the builder
  reports busy (`app/workers/tasks/graphrag.py:418`), so a waiting job writes `running`
  over the label. That is right while another build holds the lock, but when the busy
  cause is a non-resumable state with no holder (`graphrag_builder.py:228-233`), the
  gauge reads `running` instead of the state the config is stuck in until a build
  settles it. Before this fix it read `failed`, which was equally wrong. The gauge is
  per process, so with several worker replicas a waiter on a replica other than the
  holder's leaves that replica's series at `running` until a later build runs there
  (pre-fix: `failed`). Setting `running` only after the lock is acquired, which needs a
  hook from the builder, would fix both.
- **FU-5.** The busy budget covers one holder's timeout, counted from the waiter's first
  try. Several waiters on one config, or a lock that outlives a crashed holder by up to
  `LOCK_TTL_S`, can exhaust it, and the job then stores a failed result as before
  (Knowledge Map: harmless when a newer revision's job builds the corpus, as the
  replacement build covers it; otherwise recovered when `keep_result` lapses, FU-2).
  arq logs the exhaustion as `max retries exceeded` at warning level, but no metric
  records it.
- **FU-6.** Each busy try builds the Neo4j driver, the Qdrant client and a DB session,
  and for a Concept Map runs the delta-scope query, before the builder finds the lock
  held: up to 64 times per waiting job. A cheap lock probe before that setup would
  avoid it, but the builder's own acquire stays the authority.
