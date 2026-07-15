---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.12]
---

# F-12: Knowledge Map job deduplication can lose a committed corpus change

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-12).

## 1. Summary

Knowledge Map build jobs are deduplicated by an Arq job ID derived from the config's
`(last_build_state, last_build_at)`:
`knowmap:build:{config_id}:{state}:{int(last_build_at.timestamp())}`
(`backend/contexts/knowledge/application/knowmap_triggers.py:36-37`). That snapshot is read
into memory *before* the slow parse/chunk/scan work and used to enqueue *after* the commit
(`backend/contexts/knowledge/application/knowmap_ingest_service.py:97` → `:261-262`;
worker `backend/app/workers/tasks/knowmap.py:69` → `:107-108`). Arq keeps completed job
results for one hour (`backend/app/workers/main.py:294`, `keep_result = 3600`) and returns
`None` for a duplicate job ID, which the queue wrapper silently discards
(`backend/shared_kernel/queue.py:31`; `knowmap_triggers.py:52-63`). So when an upload B commits
while build A (triggered by an earlier upload) is still running — or within the hour after it
finished — B computes the same `(state, epoch)` job ID that A already used, Arq drops B's
enqueue as a duplicate, and B's documents are never built into the graph until an unrelated
future mutation or a manual rebuild. Integer-second timestamp resolution
(`knowmap_triggers.py:36`) makes distinct sub-second builds collide identically, and there is no
monotonic corpus-revision fallback (no such column exists,
`backend/alembic/versions/0048_knowmap.py:37-90`). The fix introduces a monotonic
`corpus_revision` bumped transactionally with every document mutation, deduplicates builds by
`(config_id, target_revision)`, and re-checks the revision at build completion to enqueue a
follow-up when the corpus advanced during the build.

## 2. Observed vs Expected

- **Observed** —
  - *Job ID from a coarse, mutable snapshot.* `knowmap_build_job_id` returns
    `f"knowmap:build:{config_id}:{last_build_state.value}:{epoch}"` where
    `epoch = int(last_build_at.timestamp())` (`knowmap_triggers.py:36-37`) — 1-second resolution,
    `0` when never built (`:34`).
  - *Snapshot read before the slow work.* Multipart ingest loads `cfg` at
    `knowmap_ingest_service.py:97`, runs `_index_document` (parse/chunk) at `:157-164`, commits at
    `:165`, then enqueues from the stale `cfg` via `_enqueue_build(cfg)` (`:167`, `:258-263`,
    job ID built at `:261-262`). The tus/worker path loads `cfg` at
    `backend/app/workers/tasks/knowmap.py:69`, processes at `:76,:91`, commits at `:92`, and
    enqueues from that snapshot at `:106-108`.
  - *Arq retains and silently drops duplicates.* `keep_result = 3600`
    (`backend/app/workers/main.py:294`); Arq 0.26 (`backend/pyproject.toml:19`) returns `None`
    for an existing `_job_id`; `enqueue` discards the return
    (`backend/shared_kernel/queue.py:21-32`, `:31`) and `enqueue_knowmap_build` only logs
    (`knowmap_triggers.py:52-63`), so a suppressed enqueue is indistinguishable from success.
  - *No self-healing after a build.* The shared builder never enqueues a follow-up (no `enqueue`
    call in `backend/contexts/knowledge/application/graphrag_builder.py`); `last_build_at` is
    stamped with the build's *start* watermark (`graphrag_builder.py:182,448-454`), and the delta
    loader snapshots ready documents once at build start
    (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:66-70`).
- **Expected** — [R11.12]: a Knowledge Map "rebuilds on document-set change (upload, delete,
  reprocess)." Every committed corpus change must eventually be reflected in the graph; a build
  in flight when a change commits must not cause that change to be permanently skipped. The
  existing dedup intent is only to collapse *redundant* builds of the *same* corpus state, not to
  drop a build of a *newer* state (`knowmap_triggers.py:30-34` docstring).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How thoroughly to fix the dedup race? | **Corpus-revision model** — add a monotonic `corpus_revision`, bump it transactionally on every document mutation, dedup builds by `(config_id, target_revision)`, and re-check + re-enqueue at build completion. | The minimal alternative (reload the config fresh before enqueue + detect Arq's `None` return) does not close the core race: while build A is running, A's state has not yet advanced, so an upload B committed during A computes the same nonce and is dropped, and A's start-of-build snapshot never included B. Only a revision that advances on B's commit — combined with a completion-time re-check — guarantees B is built. |
| Q-2 | Dedup key: `(config_id, target_revision)` or keep the state/timestamp nonce? | **`(config_id, target_revision)`**, where `target_revision` is the corpus revision current at enqueue time. | A revision advances exactly once per committed mutation and never collides within a second, so two builds for genuinely different corpus states get distinct job IDs while two enqueues for the *same* revision (the legitimate dedup case) still collapse. |
| Q-3 | Where does the re-check happen? | At **build completion**, before stamping terminal state: compare the config's current `corpus_revision` to the `target_revision` the build processed; if the current is greater, enqueue the next build. | Self-heals the case where a mutation commits after the build's delta snapshot but before it finishes, without polling. Bounded — it enqueues at most one follow-up per completion, which itself deduplicates by the new revision. |

## 4. Reproduction

Preconditions: a Knowledge Map config; a fake queue recording enqueued `_job_id`s and honoring
Arq's "existing ID returns None" semantics with a retention window; a way to hold build A
mid-flight.

1. Upload document D1 → build A enqueues with `job_id = knowmap:build:{C}:idle:0` (never built),
   runs, and snapshots the ready corpus `{D1}` at start (`knowmap_delta_loader.py:66-70`).
2. While A runs, upload D2. The ingest path loaded `cfg` before A completed, so
   `cfg.last_build_state` is still `idle`/`last_build_at` still `None`
   (`knowmap_ingest_service.py:97`); D2 enqueues `job_id = knowmap:build:{C}:idle:0` — identical
   to A's retained ID. Arq returns `None`; the wrapper discards it (`queue.py:31`).
3. A finishes having built only `{D1}`. No follow-up is enqueued (`graphrag_builder.py` has no
   `enqueue`). D2 is absent from the graph.
4. D2 remains unbuilt until an unrelated upload/delete or a manual rebuild.

Deterministic under the fake. A second reproduction: two uploads committing in the same wall-clock
second after a prior build produce the same `epoch` (`knowmap_triggers.py:36`) and collide even
without overlap.

## 5. Root Cause Analysis

The causal chain:

1. The dedup nonce is `(state, last_build_at@1s)` read from a `cfg` snapshot taken before the
   slow work and the commit (`knowmap_ingest_service.py:97` → `:261-262`;
   `app/workers/tasks/knowmap.py:69` → `:107`). **This is the root cause** — the nonce does not
   advance on the committed corpus change it is supposed to represent, so a newer corpus state can
   reuse an older state's job ID.
2. 1-second timestamp resolution (`knowmap_triggers.py:36`) and the absence of any monotonic
   corpus-revision column (`0048_knowmap.py:37-90`) leave no fallback that distinguishes distinct
   states.
3. Arq's 3600s retention (`app/workers/main.py:294`) plus the discarded `None` return
   (`queue.py:31`; `knowmap_triggers.py:52-63`) make the suppression silent and long-lived —
   aggravating factors that widen the window and hide it.
4. No completion-time re-check (`graphrag_builder.py`, no `enqueue`; start-watermark
   `last_build_at`, `:182,453`) removes the last chance to self-heal.

Replacing the nonce with a monotonic per-config `corpus_revision` (root cause) plus a
completion-time re-check (link 4) guarantees every committed change is eventually built.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — concurrent or multi-file Knowledge Map uploads, and slow tus/parser paths,
  can silently leave documents out of the graph for up to the retention window (and effectively
  until the next unrelated mutation).
- **Sibling suspects:**
  - **All knowmap build enqueue sites — confirmed, all in scope.** Multipart ingest
    (`knowmap_ingest_service.py:167`), multipart reprocess (`:116`), tus/worker
    (`app/workers/tasks/knowmap.py:106-108`), quarantine rebuild (`:205-207`), explicit rebuild
    (`backend/app/api/v1/knowmap.py:385-389`), and document-delete rebuild (`:572-574`) all route
    through `enqueue_knowmap_build` and must switch to the revision-based job ID.
  - **Concept Map / GraphRAG builds — cleared.** Concept Map builds are message-triggered with
    their own trigger evaluation (F-3/F-4 scope); they do not use `knowmap_build_job_id` and are
    not part of this corpus-revision change.
  - **F-23 (tus retry job ID) — related, separate.** The tus finalizer reuses a document-scoped
    job ID for *ingestion* retries (`findings.md` F-23); that is the ingestion job, distinct from
    the *build* job this fix addresses. The revision approach here does not fix F-23 and vice
    versa.
  - **Delete/reprocess as mutations — confirmed in scope.** Document delete and reprocess must
    also bump `corpus_revision` so their builds get fresh job IDs; otherwise a delete could be
    deduplicated against a prior build the same way.

## 7. Fix Design

1. **Migration (next available number; coordinate with F-11's `0052` if both land together — use
   the next free number).** Add `corpus_revision INTEGER NOT NULL DEFAULT 0` to `knowmap_configs`
   (`backend/contexts/knowledge/infrastructure/knowmap_tables.py`; migration mirrors
   `0048_knowmap.py`). Also add a nullable `built_corpus_revision INTEGER` to record the revision
   the last build actually processed (for the completion re-check).
2. **Bump on every mutation, in the mutation's transaction.** Wherever a document is added,
   reprocessed, or deleted (multipart ingest `knowmap_ingest_service.py:157-165`; tus worker
   `app/workers/tasks/knowmap.py:91-92`; delete `backend/app/api/v1/knowmap.py` delete path),
   increment `knowmap_configs.corpus_revision` in the same commit that persists the document
   change — e.g. `UPDATE knowmap_configs SET corpus_revision = corpus_revision + 1 WHERE id = :c`
   with the row locked (`SELECT ... FOR UPDATE`) or via an atomic SQL increment. Re-read the new
   value after commit for the enqueue.
3. **Revision-based job ID.** Change `knowmap_build_job_id` (`knowmap_triggers.py:21-61`) to
   `f"knowmap:build:{config_id}:{target_revision}"`, dropping the `(state, epoch)` inputs.
   `enqueue_knowmap_build` takes `target_revision` (the post-commit `corpus_revision`) and passes
   it as both the job ID discriminator and a `target_revision` build argument.
4. **Build reads/records the target.** `knowmap_build` (`app/workers/tasks/knowmap.py:264-277`)
   receives `target_revision`; the builder stamps `built_corpus_revision = target_revision` at
   terminal success alongside the existing state stamp (`graphrag_builder.py:448-454`).
5. **Completion re-check.** After a successful build, compare the config's current
   `corpus_revision` (re-read) to `target_revision`; if greater, enqueue a follow-up
   `knowmap_build` with the current revision. At most one follow-up per completion; it
   deduplicates on its own revision. Implement in the knowmap worker wrapper
   (`app/workers/tasks/knowmap.py`) so the shared `graphrag_builder` stays enqueue-free and layer
   boundaries hold.
6. **Surface suppressed enqueues (hardening).** Have `enqueue` return Arq's result and
   `enqueue_knowmap_build` log at a distinguishable level when it is `None`
   (`queue.py:21-32`; `knowmap_triggers.py:52-63`) so a genuine same-revision dedup is observable
   and not confused with the bug. Optional but cheap.

**Data repair:** existing configs default `corpus_revision = 0`. The first post-deploy mutation
bumps to 1 and enqueues a fresh job ID, so no manual repair is needed; documents dropped by the
pre-fix bug are recovered by their config's next mutation or a manual rebuild (unchanged from
today, now reliable going forward).

## 8. Regression Test Plan

Unit tests (fake queue honoring Arq retention + `None`-on-duplicate; in-memory config store):

1. **Concurrent-upload drop (primary red-first):** simulate the §4 sequence — build A for
   revision 1 enqueued and "running"; upload B commits (revision → 2) from a pre-A snapshot; assert
   B enqueues a *distinct* job ID and is not dropped. Fails today — both compute
   `knowmap:build:{C}:idle:0`.
2. **Same-revision dedup preserved:** two enqueues for the same `target_revision` collapse to one
   job (legitimate dedup still works).
3. **Completion re-check:** build for revision 1 finishes while `corpus_revision` is already 2;
   assert a follow-up build for revision 2 is enqueued. Fails today — no follow-up path exists.
4. **Sub-second distinct builds:** two mutations within one wall-clock second get distinct job IDs
   (revision-based), where the old `epoch` nonce would collide.
5. **Mutation bumps revision transactionally:** add, reprocess, and delete each increment
   `corpus_revision` exactly once per committed change.

## 9. Risks and Rollback

- **Revision bump contention** — a per-config `UPDATE ... SET corpus_revision = corpus_revision +
  1` (or `FOR UPDATE`) serializes concurrent mutations of the *same* config; that is the intended
  ordering and the contention is bounded to one config's uploads. Different configs do not contend.
- **Follow-up enqueue loop** — a follow-up is enqueued only when `corpus_revision >
  target_revision`; each follow-up processes up to the current revision and records it, so the
  chain terminates once the built revision catches up. Assert termination in test 3.
- **Migration on a large table** — adding a defaulted integer column is a fast metadata-only
  operation on Postgres 11+; no data backfill needed.
- **Interaction with F-11 migration** — if both land together, assign sequential migration numbers
  and a single linear `down_revision` chain; do not fork.
- **Rollback** — revert the code and drop the two columns; the job ID reverts to the
  `(state, epoch)` nonce. In-flight jobs enqueued under revision-based IDs complete normally
  (Arq treats the ID as opaque).

## 10. Acceptance Criteria

- [x] AC-1: The concurrent-upload regression test (§8.1) fails before the fix and passes after.
  `test_concurrent_upload_gets_distinct_job_id` asserts revisions 1 and 2 yield distinct job IDs;
  the pre-fix formula computed `knowmap:build:{C}:idle:0` for both (identical) and would fail — the
  revision-based job ID satisfies it.
- [x] AC-2: A document committed while a build is running is built into the graph without an
  unrelated later trigger — covered by `test_finalize_enqueues_follow_up_when_corpus_advanced`
  (completion re-check enqueues the newer revision) plus the distinct revision-based job ID
  (§8.1, §8.3). The two adjacent silent-drop edges the quality gate uncovered — a document
  entering `ready∧clean` via a scan verdict, and a manual rebuild after a build — are closed by
  D-6/D-7 and covered by `test_scan_verdict_rebuild_advances_and_targets_new_revision` and
  `test_rebuild_endpoint_bumps_and_targets_new_revision`. (End-to-end reproduction through a live
  queue is host-gated — FU-3.)
- [x] AC-3: `corpus_revision` increments exactly once per committed add, reprocess, or delete,
  within the mutation's transaction (§8.5). `test_index_document_bumps_corpus_revision_once`
  covers the add/reprocess path; `test_bump_corpus_revision_returns_incremented_value` /
  `_missing_config_returns_zero` cover the atomic `UPDATE … RETURNING`. The delete site
  (`app/api/v1/knowmap.py`) calls the same `bump_corpus_revision` before its commit.
- [x] AC-4: Build job IDs are `knowmap:build:{config_id}:{target_revision}` and two enqueues for
  the same revision still deduplicate (§8.2), while sub-second distinct builds do not collide
  (§8.4). Covered by `test_job_id_format_is_revision_based`, `test_same_revision_dedups_to_one_job_id`,
  `test_sub_second_distinct_revisions_do_not_collide`, `test_distinct_configs_never_collide`.
- [x] AC-5: `ruff check . && ruff format --check .` pass on the touched files; `mypy` introduces
  zero net-new errors (the 8 F-12 source files are clean — the 19 remaining errors are the
  pre-existing untouched-file baseline); `pytest tests/unit/` green (the runnable gate — see D-4
  for the host-gated `alembic upgrade` portion).

## 11. SRS Delta

None. This restores [R11.12]'s "rebuilds on document-set change" guarantee; the corpus-revision
column is an implementation mechanism, not new documented behavior.

## 12. Deviation Log

- **D-1 — §7.6 observability implemented, not deferred.** The "optional but cheap"
  hardening was built: `shared_kernel/queue.py::enqueue` now returns arq's
  `enqueue_job` result (typed `Any`, backward compatible — existing callers that
  ignored the return still do), and `enqueue_knowmap_build` logs a `debug` line when
  the return is `None` (a legitimate same-revision dedup) so it is distinguishable from
  the pre-fix silent suppression. This resolves FU-2 inline; FU-2 no longer applies.
- **D-2 — completion re-check as a named worker helper.** §7.5's re-check + §7.4's
  `built_corpus_revision` stamp are implemented together in a single worker-layer helper
  `_finalize_build_revision(sm, config_id, target_revision, *, succeeded)` in
  `app/workers/tasks/knowmap.py`, called from `knowmap_build` after commit. The shared
  `graphrag_builder.py` is left entirely untouched (not stamping `built_corpus_revision`
  itself, contrary to §7.4's loose wording) so it stays enqueue-free and Concept Map
  builds are unaffected — honouring the SoC constraint §7.5 flagged.
- **D-3 — migration number `0053`.** F-11 landed first and took `0052`, so this
  migration is `0053_knowmap_corpus_revision` with `down_revision =
  "0052_project_embedding_pins"` — a single linear chain, exactly as §9 requires ("assign
  sequential migration numbers … do not fork").
- **D-4 — host-gated contract verification.** This environment has no
  Postgres/Redis/Neo4j, so `alembic upgrade head` + live downgrade and an end-to-end
  concurrent-upload reproduction through a real Arq queue could not be executed here. The
  downgrade path was sanity-checked by reading the migration (drops both columns, mirrors
  `0048`); the revision/dedup/completion-recheck logic is fully covered by unit tests with
  a fake session and queue. Live migration apply remains a deploy-time gate (see FU-3).
- **D-5 — obsolete/updated tests for the removed job-id signature.** §7.3 removes the
  `knowmap_build_job_id(config, last_build_state=, last_build_at=)` signature.
  `tests/unit/test_knowmap_triggers.py` tested *only* that removed signature, so it was
  deleted and its coverage moved to `tests/unit/test_knowmap_build_dedup.py` (the F-12
  test artifact), including its one unique case (`test_distinct_configs_never_collide`).
  Three pre-existing sibling tests that asserted the old enqueue kwargs
  (`test_knowmap_ingest.py`, `test_knowmap_scan_build_gate.py`,
  `test_knowmap_scan_worker.py`) were updated to the `target_revision=` contract. No
  coverage was weakened — the assertions now encode the new, stricter invariant.
- **D-6 — extend the bump beyond §7.2's add/reprocess/delete to close two adjacent
  silent-drop paths (user-approved scope extension).** The `check-quality` gate found that
  keying the build target on `corpus_revision` while only advancing it on document
  add/reprocess/delete leaves two events that change *what a build processes* without a
  fresh target: **(W1)** a scan verdict flipping a document into the buildable
  `ready∧clean` set — two files uploaded together defer their builds to scan-clean and
  both target the same post-upload revision, so the second is dropped as a duplicate,
  reopening the exact race for the scan edge; and the sibling QUARANTINED/SKIPPED removal
  rebuilds share the root. Fix: a shared worker helper `_bump_and_enqueue_build(sm,
  config_id)` advances the revision (atomic `UPDATE … RETURNING`, 0 ⇒ config concurrently
  deleted ⇒ no build) and enqueues the bumped target; `_enqueue_build_on_clean`,
  `_enqueue_rebuild_for_config`, and the inline QUARANTINED enqueue now route through it.
  The reindex-of-clean and index-worker-clean paths already target their own
  index-time-bumped revision, so they were left unchanged. Approved by the user
  ("Fix both now") rather than deferred.
- **D-7 — advance the revision on an explicit rebuild (user-approved; fixes W2
  regression).** `check-quality` found (and I confirmed against `graphrag_reconciler.py`
  `_STUCK_STATES`) that the rebuild endpoint keyed the job id on the *unchanged*
  `cfg.corpus_revision`, so within arq's `keep_result` (3600s) it collided with the
  previous build's retained result — a **regression** vs the pre-F-12 `(state, epoch)`
  nonce, which advanced after each run. Because the reconciler does not heal a terminal
  `FAILED`, an operator's manual rebuild (the only recovery path) silently no-op'd for up
  to an hour. Fix: `rebuild_knowmap_config` now bumps `corpus_revision` in the request
  transaction and targets the bumped value, so an explicit rebuild always produces a fresh
  build generation. Trade-off: two rapid rebuild clicks now enqueue two revisions (two
  builds, serialized by the per-config Redis lock) instead of collapsing — acceptable for
  a deliberate, rare operator action, and strictly safer than dropping the retry. New
  regression tests: `test_scan_verdict_rebuild_advances_and_targets_new_revision`,
  `test_scan_verdict_rebuild_skips_concurrently_deleted_config`,
  `test_rebuild_endpoint_bumps_and_targets_new_revision`.
- **D-8 (code-review W3+W6 — membership-change revision model, user-approved).** A high-recall
  code review found two consequences of D-6's unconditional scan-verdict bump: **(W3)** a reindex
  of an already-clean document double-built — the ingest-side immediate clean-enqueue *and* the
  rescan's `_enqueue_build_on_clean` computed different (bumped) revisions, so arq no longer
  deduplicated them; and **(W6)** a QUARANTINED/SKIPPED verdict on a document that was never CLEAN
  triggered a full graph rebuild even though its triples were never in the graph. Both trace to the
  bump not distinguishing a real membership change from a no-op. Fix: the scan worker now computes
  `prior_clean` from the document's status before the verdict and advances the revision / rebuilds
  **only on a real membership change** — a document *entering* `ready∧clean` (`entered = not
  prior_clean`, passed to `_enqueue_build_on_clean`) or a previously-CLEAN document *leaving* it
  (quarantine/skip gated on `prior_clean`). A CLEAN→CLEAN reconfirm no longer bumps (dedups with
  the ingest-side enqueue, closing W3); a never-CLEAN quarantine/skip no longer rebuilds (W6).
  New/updated tests in `test_knowmap_scan_worker.py` (prior-clean → rebuild vs never-clean → no
  rebuild) and `test_knowmap_scan_build_gate.py` (`entered` flag, reconfirm skip).
- **D-9 (code-review W4 — best-effort post-build finalize, user-approved).** `_finalize_build_revision`
  ran inside `knowmap_build`'s try *after* the build committed its terminal state, so a transient
  DB error in the revision bookkeeping propagated and made arq re-run the entire successful build.
  Fix: the finalize call is wrapped in a logged best-effort `try/except`; a missed follow-up is
  recovered by the next document change or a manual rebuild.

## 13. Follow-ups

- **FU-1 (F-23 tus ingestion retry):** the document-scoped ingestion job ID reuse is a distinct
  dedup defect on the ingestion (not build) job; address separately after confirming Arq behavior
  in the deployed environment.
- **FU-2 (observability) — RESOLVED inline (D-1).** `enqueue_knowmap_build` now logs the
  suppressed-enqueue (`None`) case at `debug`. A Prometheus counter on suppressed enqueues is
  still a worthwhile future addition but is no longer needed to distinguish the dedup from the bug.
- **FU-3 (deploy-time migration + integration gate, D-4):** on a host with the datastores, run
  `alembic upgrade head` (then `downgrade -1` to confirm the drop) and an end-to-end
  concurrent-upload reproduction through a real Arq queue to close the last mile of AC-1/AC-2 that
  could not run in this environment.
- **FU-4 (pre-existing full-suite flake, unrelated to F-12):**
  `tests/unit/test_sel_evaluator.py::TestFuncLen::test_dict` (workflow context — untouched by this
  task) passed in isolation both at `HEAD` and with this change, but failed once during a full
  `pytest tests/unit/` run — a test-ordering/isolation issue independent of F-12. Flag for a
  separate investigation of `test_sel_evaluator` global-state leakage.
- **FU-5 (DRY — pin-clear/collection-drop duplicated across three services):** the drop-empty
  skeleton is duplicated across `config_service.py`, `knowmap_config_service.py`, and
  `graphrag_config_service.py` — now as the `clear_pin_if_last_config` + `drop_orphan_collection`
  pair per service (F-11 code-review split, D-5 on the F-11 dossier). Extract one helper
  parameterized by `(PinKind, collection-deleter)`. Warning-level, non-blocking; still deferred.
- **FU-6 (defensive dead branch in `EmbeddingPinRepository.ensure`, F-11) — RESOLVED.** The
  `except IntegrityError` re-read is now correct: the race insert is wrapped in `begin_nested()`, so
  a unique-violation rolls back only the savepoint and the follow-up SELECT no longer risks
  `PendingRollbackError` (F-11 code-review D-6). This closes the finding rather than deferring it.

## 14. Post-implementation audit

- **`check-quality`** (12 dimensions over the combined F-11/F-13/F-12 diff, 23 files): 0
  Introduced-Critical. Two Introduced-Warning correctness findings (W1 scan-verdict revision gap,
  W2 rebuild-after-failure suppression) were **fixed** this task per user approval (D-6, D-7); one
  Warning (DRY) → FU-5 and one Info (dead branch) → FU-6, both pre-existing F-11 surfaces.
- **`check-security`** (13 dimensions over the same diff, AuthZ traced for 8 endpoints): 0
  CRITICAL/HIGH/MEDIUM. All pin/collection state and the build job id derive from resolved
  `cfg.project_id` / DB-sourced integers, never client-supplied; migration `0052` backfill binds
  all parameters; no key-exfiltration path. Three defense-in-depth notes only (unconditional
  rebuild enqueue — now moot after D-7; advisory-lock hash over-locking; no per-endpoint rate
  limit, matching the pre-existing RAG/GraphRAG surface).
