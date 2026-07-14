---
type: bugfix
status: draft
created: 2026-07-14
requirements: []
---

# F-23: Failed tus reuploads can be suppressed by the retained worker job ID

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-23, `plausible`).

## 1. Summary

The RAG and Knowledge Map tus (large-file, >32 MB) finalizers enqueue the ingest worker with a
**document-id-only, deterministic** job ID (`rag-ingest:{document_id}` /
`knowmap-ingest:{document_id}`). Arq retains job results for 3,600 seconds and deduplicates a
re-enqueue of an already-known `_job_id` by returning `None` and scheduling nothing; the shared
enqueue wrapper discards that return. When a large-file ingest exhausts its retries and leaves the
document `FAILED`, an immediate same-SHA reupload reuses the existing document row (hence the same
job ID), so within the one-hour window Arq drops the re-enqueue silently — no worker runs — while
the tus endpoint answers `204` with a resource header pointing at the still-`FAILED` document, as
though a retry had been scheduled. The finding is `plausible` pending confirmation of Arq 0.26's
dedup behavior against the deployed stack; this spec builds that confirmation into the test plan.
The fix adds a per-document `ingest_attempt` counter, bumped on each finalizer re-enqueue and
included in the job ID, so a genuine retry always enqueues a fresh job while a truly concurrent
duplicate of the same attempt still dedups.

## 2. Observed vs Expected

- **Observed:**
  - Both tus finalizers build a document-id-only job ID:
    `_job_id=f"rag-ingest:{document_id}"` (`backend/contexts/knowledge/application/rag_tus_finalizer.py:171`,
    enqueuing `"rag_ingest_document"` at `:169`) and
    `_job_id=f"knowmap-ingest:{document_id}"` (`backend/contexts/knowledge/application/knowmap_tus_finalizer.py:131`,
    enqueuing `"knowmap_ingest_document"` at `:129`).
  - On a reupload of a non-`READY` SHA the finalizer **reuses the existing document row** (so the
    ID is identical) and re-enqueues **both** the ingest job and the scan job: RAG
    `rag_tus_finalizer.py:89-105` (`_enqueue_index(existing.id)` `:101`, `enqueue_rag_scan(
    existing.id)` `:102-104`, returns `existing` `:105`, emits a reupload audit `:94-100`); knowmap
    `knowmap_tus_finalizer.py:78-81` (`_enqueue_index(existing.id)` `:79`, `enqueue_knowmap_scan(
    existing.id)` `:80`, returns `existing` `:81`; no reupload audit). `document_id` is stable per
    `(config_id, sha256)`, and the scan job ID is document-only too (`rag-scan:{document_id}`,
    `knowmap-scan:{document_id}`), so both jobs are suppressible within the retention window.
  - The enqueue wrapper discards Arq's return: `backend/shared_kernel/queue.py:21-33` —
    `async def enqueue(...) -> None`, `await pool.enqueue_job(...)` at `:31` with the result not
    captured, so callers cannot tell a real enqueue from a deduped one.
  - Result retention: `keep_result = 3600` (`backend/app/workers/main.py:294`); `job_timeout=600`
    (`:292`), `max_jobs=50` (`:293`). Arq `0.26.*` (`backend/pyproject.toml:19`).
  - Workers leave the document `FAILED` after `max_tries=3`: `backend/app/workers/tasks/rag.py:100-112,122`;
    `backend/app/workers/tasks/knowmap.py:94-103,112`.
  - The tus endpoint answers `204` with `X-SMAP-Resource` pointing at the (still-`FAILED`)
    document (`backend/app/api/v1/tus.py:283,296-300`; via
    `backend/contexts/conversation/application/tus_service.py:292-342` and
    `backend/contexts/knowledge/interfaces/facade.py:232-299`) — indistinguishable from a fresh
    successful register.
  - The multipart (<=32 MB) path is **not** affected: it indexes synchronously on the request
    (`backend/contexts/knowledge/application/ingest_service.py:226-236`) and enqueues only a scan
    (`:235`, `rag-scan:{document_id}` `:462`; knowmap `knowmap-scan:{document_id}` `:273`); no
    ingest job is enqueued there.
- **Expected** — a genuine reupload/retry of a previously failed large-file document schedules a
  fresh ingest worker run; the API response reflects whether ingestion was actually scheduled.
  This is the tus finalizer's own documented genuine-retry behavior (`rag_tus_finalizer.py:161-166`
  acknowledges that "only a fresh manual re-upload within the result-TTL window is briefly
  deduped" — the defect is that it is *silently* deduped and reported as success).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Add an ingestion-attempt discriminator column, or capture Arq's enqueue return without a schema change? | **Add an `ingest_attempt` column** to `rag_documents` and `knowmap_documents`, bump it on each finalizer re-enqueue, and include it in the job ID. | A genuine retry always gets a distinct ID (`...:{attempt}`) so it always enqueues, independent of Arq internals; a truly concurrent duplicate of the same attempt still dedups (the desired behavior). Matches the audit's "include an ingestion-attempt/revision in job identity." The return-capture variant is subtler and relies on Arq 0.26 dedup semantics; recorded as FU-1. |
| Q-2 | Does this reuse F-12's `corpus_revision`? | **No.** | F-12's `corpus_revision` is a **config/build-scoped** counter on `knowmap_configs`, not a per-document ingestion-attempt counter, and its spec explicitly scopes F-23 out (`docs/tasks/2026-07-14-knowmap-build-dedup-revision/spec.md:127-130,230-232`). F-23 needs a distinct per-document column. |
| Q-3 | The finding is `plausible` pending Arq behavior — build now or confirm first? | **Build, with confirmation in the test plan.** | The red-first unit test asserts job-ID distinctness independent of Arq (deterministic); a wiring/integration check confirms Arq 0.26 actually dedups a retained `_job_id` on the deployed stack (§8). |

## 4. Reproduction

Preconditions: a RAG (or Knowledge Map) config; a >32 MB file that fails ingestion (e.g. a parser
error) so the document ends `FAILED`; a same-process Arq with `keep_result=3600`.

1. Upload the large file via tus; the finalizer enqueues `rag-ingest:{doc}` and the worker fails
   after `max_tries=3`, marking the document `FAILED` (`rag.py:100-112`).
2. Within the hour, reupload the identical file (same SHA) via tus. The finalizer reuses the
   existing `FAILED` row (`rag_tus_finalizer.py:89-105`) and re-enqueues the identical
   `rag-ingest:{doc}` ID.
3. Arq finds the retained result for that `_job_id` and drops the enqueue (returns `None`,
   discarded at `queue.py:31`). No worker runs.
4. The endpoint returns `204` + `X-SMAP-Resource: /api/rag-documents/{doc}` (`tus.py:296-300`),
   and the document stays `FAILED` with no scheduled retry — the client sees an apparent success.

Deterministic under a job pool that mimics Arq's dedup-on-retained-`_job_id`.

## 5. Root Cause Analysis

The causal chain:

1. The ingest job ID is derived from `document_id` alone (`rag_tus_finalizer.py:171`,
   `knowmap_tus_finalizer.py:131`), and a reupload reuses the same document row
   (`rag_tus_finalizer.py:89-105`; `knowmap_tus_finalizer.py:78-81`), so a retry produces an ID
   identical to the previous attempt.
2. Arq deduplicates a re-enqueue of a still-retained `_job_id` (`keep_result=3600`,
   `main.py:294`), and the wrapper discards the signal (`queue.py:31`). **The root cause is (1)** —
   the ID carries no per-attempt discriminator, so a genuine retry is indistinguishable from a
   duplicate. Correcting the ID to vary per attempt makes every genuine retry enqueue regardless
   of retention or wrapper behavior.
3. The `204`+resource response reporting apparent success (`tus.py:296-300`) is an aggravating
   factor that hides (2); once (1) is fixed, a genuine retry actually runs, so the response is
   truthful. Surfacing the deduped return is optional hardening (FU-1), not required.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — retry UX for >32 MB RAG and Knowledge Map tus uploads within the one-hour
  result-retention window: a failed large-file document cannot be retried by reupload and the API
  misreports the retry as scheduled.
- **Sibling suspects:**
  - **Knowmap tus finalizer (`knowmap_tus_finalizer.py:123-132,78-81`) — confirmed, same fix.**
    Identical document-only job ID and row-reuse; gets the same `ingest_attempt` column and
    ID suffix.
  - **Scan jobs (`rag-scan:{doc}`, `knowmap-scan:{doc}`) — confirmed, in scope.** The tus reupload
    branch re-enqueues the scan alongside the ingest (`rag_tus_finalizer.py:102-104`;
    `knowmap_tus_finalizer.py:80`) with the same document-only, deterministic ID
    (`ingest_service.py:462`, `knowmap_ingest_service.py:273`). If a prior scan failed/erred and
    its result is retained, the rescan on a genuine retry is suppressed too, so the same attempt
    discriminator must be applied to the scan job ID for the retry to actually re-scan (§7).
  - **Multipart ingest path — cleared for ingest; scan shares the shape (FU-2).** Multipart indexes
    synchronously and enqueues **no** ingest job (`ingest_service.py:145-167,226-236`;
    `knowmap_ingest_service.py:105-117,157-168`), so the ingest defect is absent. But its reupload
    **scan** enqueue reuses the same document-only `rag-scan:{doc}` / `knowmap-scan:{doc}`
    (`ingest_service.py:462`, `knowmap_ingest_service.py:273`) — the same latent dedup this spec
    fixes on the tus path. Multipart re-indexes synchronously regardless (only a rescan is at risk)
    and owns no tus attempt counter, so it is tracked as FU-2 rather than fixed here.
  - **F-12 build-job dedup — distinct.** Build job IDs key on `(state, epoch)`
    (`knowmap_triggers.py`, `graphrag_triggers.py`); a separate concern with its own spec.

## 7. Fix Design

1. **Add `ingest_attempt`** as `INTEGER NOT NULL DEFAULT 0` to `rag_documents`
   (`backend/contexts/knowledge/infrastructure/tables.py:51-91`) and `knowmap_documents`
   (`backend/contexts/knowledge/infrastructure/knowmap_tables.py:64-99`) via a new Alembic
   migration (next revision after current head; see §9 on ordering vs F-12).
2. **Add an atomic increment-and-return repo method (B2 plumbing).** Neither document repository
   exposes a generic update/increment today (`RagDocumentRepository`,
   `backend/contexts/knowledge/infrastructure/repositories.py:185-376`, has only
   `create/set_agents/set_status/mark_scan/get/find_by_sha/delete`; `KnowmapDocumentRepository`,
   `backend/contexts/knowledge/infrastructure/knowmap_repositories.py:252-434`, mirrors it), and the
   frozen `RagDocument`/`KnowmapDocument` domain models + `_row_to_document` mappers do not carry the
   column. Add a method like `bump_ingest_attempt(document_id) -> int` that runs
   `UPDATE … SET ingest_attempt = ingest_attempt + 1 WHERE id = :id RETURNING ingest_attempt` and
   returns the new value, so the finalizer gets the attempt for the suffix **without** adding a field
   to the frozen model on the read path. The atomic `RETURNING` also resolves the concurrency race
   in §9.
3. **Bump only on a TERMINAL non-READY state — never while `INGESTING` (B4 concurrency guard).** The
   reuse branch fires on *any* non-`READY` status, which includes `INGESTING`
   (`rag_tus_finalizer.py:85-105`; `knowmap_tus_finalizer.py:76-81`). The original deterministic ID
   deliberately deduped a "reupload while the first job is still running" so two workers never index
   one document and collide on `uq_rag_chunk_doc_idx` (`tables.py:109`; knowmap
   `uq_knowmap_chunk_doc_idx`) — `_index_document` does delete-then-insert chunk_idx 0..N
   (`ingest_service.py:312,348`), so concurrent runs on one doc collide. Therefore bump
   `ingest_attempt` and enqueue a fresh ID **only when the existing row is terminal-non-READY
   (`FAILED`/`QUARANTINED`)**; when it is `INGESTING`, keep the current-attempt ID (or skip the
   enqueue) so Arq's in-progress-key check still dedups the in-flight run. This preserves both the
   genuine-retry fix (FAILED → fresh ID) and the concurrency guard (INGESTING → dedup). New documents
   keep `ingest_attempt=0`.
4. **Include it in both job IDs the reupload branch enqueues.** Change the ingest ID to
   `rag-ingest:{document_id}:{ingest_attempt}` / `knowmap-ingest:{document_id}:{ingest_attempt}`
   (`_enqueue_index`, `rag_tus_finalizer.py:149-172`; `knowmap_tus_finalizer.py:123-132`), passing
   `ingest_attempt` into `_enqueue_index` so it composes the suffix. Apply the **same** suffix to
   the scan job ID enqueued in the same branch (`rag-scan:{document_id}:{ingest_attempt}` /
   `knowmap-scan:{document_id}:{ingest_attempt}`), threading `ingest_attempt` through
   `enqueue_rag_scan` / `enqueue_knowmap_scan` (`rag_tus_finalizer.py:102-104`;
   `knowmap_tus_finalizer.py:80`; helpers at `ingest_service.py:462`,
   `knowmap_ingest_service.py:273`), so a genuine retry re-runs both index and scan. A genuine
   retry (attempt N→N+1) always enqueues; a truly concurrent duplicate finalize of the *same*
   attempt still dedups (correct — that is the rapid double-submit guard the deterministic ID was
   meant to provide). Both attempts share one `ingest_attempt` value bumped once per reupload, so
   the ingest and scan of one retry stay aligned.

The worker signature (`rag_ingest_document(document_id)`) is unchanged — the attempt lives only in
the job ID, not the worker args. The `queue.py` wrapper is left as-is (FU-1 covers surfacing the
deduped return). No API/response change is required, because the retry now genuinely runs.

**Data repair:** none needed. Existing `FAILED` documents get `ingest_attempt=0` from the column
default; their first post-deploy reupload bumps to `1`, producing a fresh ID that enqueues.

## 8. Regression Test Plan

Backend. Mirror the build-job dedup-ID pattern in `backend/tests/unit/test_knowmap_triggers.py`
(`test_passes_dedup_job_id_to_queue`, `:58-67`) and `test_graphrag_triggers.py:102-127`.

1. **Retry produces a distinct job ID (primary red-first).** New unit test for each finalizer:
   with a stubbed enqueue capturing `_job_id`, finalize a reupload of an existing `FAILED`
   document twice; assert the two `_job_id`s differ (`...:{n}` vs `...:{n+1}`) and that
   `ingest_attempt` was incremented. Fails today — both are `rag-ingest:{doc}` /
   `knowmap-ingest:{doc}`.
2. **New document starts at attempt 0.** A first-time finalize enqueues `...:0`.
3. **Same-attempt concurrent finalize still dedups.** Two enqueues for the same
   `(document_id, ingest_attempt)` produce the same `_job_id` (the intended double-submit guard).
4. **Reupload while `INGESTING` does NOT bump (B4 concurrency guard, red-first for the guard).**
   Finalize a reupload of an existing `INGESTING` document; assert `ingest_attempt` is **not**
   incremented and the enqueued `_job_id` equals the in-flight attempt's ID (so Arq dedups the
   second run). A `FAILED`/`QUARANTINED` reupload, by contrast, bumps and enqueues a fresh ID
   (test 1). Guards against re-introducing the `uq_rag_chunk_doc_idx` collision the deterministic ID
   prevented.
5. **Arq dedup confirmation (plausibility gate).** A wiring/integration test (or a documented
   `/verify` step) against the deployed Arq `0.26.*` that enqueues a job with a fixed `_job_id`,
   lets it complete/retain, re-enqueues the same ID, and asserts the second `enqueue_job` returns
   `None` and schedules no run — confirming the suppression the fix removes. Placed with the RAG
   ingestion wiring tests (`backend/tests/wiring/test_rag_ingestion.py`), skipped in the
   host-only unit environment per the audit's environment note.

Primary red-first tests: (1) for the retry fix, (4) for the concurrency guard.

## 9. Risks and Rollback

- **Migration ordering vs F-12.** F-12 adds `corpus_revision` to `knowmap_configs`; F-23 adds
  `ingest_attempt` to `rag_documents`/`knowmap_documents` — different tables, no logical conflict,
  but whichever builds second must rebase onto the new Alembic head (`alembic heads`). Flagged so
  the second build does not fork the revision graph.
- **Concurrent same-document indexing (the guard the fix must not remove).** A per-attempt ID would,
  if bumped while a prior attempt is still `INGESTING`, let a second worker index the same document
  and collide on `uq_rag_chunk_doc_idx`. Mitigated by §7 step 3: bump/fresh-ID **only** on a terminal
  non-READY state; an `INGESTING` reupload keeps the in-flight ID and is deduped. The §8.4 test
  guards this.
- **Attempt monotonicity under concurrency.** Two simultaneous reuploads of the same failed document
  could race the bump; the §7 step 2 method uses an atomic
  `UPDATE … SET ingest_attempt = ingest_attempt + 1 … RETURNING` so each caller gets a distinct
  value and neither run is lost.
- **Job-ID length/format.** The suffixed ID stays well within Arq/Redis key limits; format change
  is backward-compatible (old retained `...:{doc}` IDs simply age out of the 3,600 s window).
- **Rollback** — revert the finalizers and drop the column (down-migration). Old-format IDs resume;
  behavior reverts to today's.

## 10. Acceptance Criteria

- [ ] AC-1: The distinct-retry-ID test (§8.1) fails before the fix and passes after, for both the
  RAG and Knowledge Map finalizers.
- [ ] AC-2: `rag_documents` and `knowmap_documents` carry `ingest_attempt INTEGER NOT NULL
  DEFAULT 0` (ORM tables and migration match, no `sa.Text`/enum mismatch), and both document
  repositories expose an atomic increment-and-return method (`bump_ingest_attempt`) — the frozen
  domain models are not modified on the read path.
- [ ] AC-3: A reupload of a terminal non-`READY` (`FAILED`/`QUARANTINED`) document increments
  `ingest_attempt` and enqueues both the ingest and scan jobs with IDs that include the new attempt,
  so a genuine retry always schedules a fresh index and rescan.
- [ ] AC-4: A first-time finalize enqueues attempt 0; a same-attempt concurrent finalize dedups; and
  a reupload of an `INGESTING` document does **not** bump — it keeps the in-flight ID so the run is
  deduped (no second concurrent worker), while a `FAILED`/`QUARANTINED` reupload bumps to a fresh ID.
- [ ] AC-5: The Arq-dedup confirmation check (§8.5) documents/verifies that Arq `0.26.*` suppresses
  a retained-`_job_id` re-enqueue on the deployed stack.
- [ ] AC-6: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`, and `alembic upgrade
  head` pass in `backend/`.

## 11. SRS Delta

None. The SRS does not define ingest-job identity; this restores the tus finalizer's documented
genuine-retry behavior.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (surface the deduped enqueue):** as complementary hardening, change
  `shared_kernel/queue.py:enqueue` to return Arq's `enqueue_job` result (`Job | None`) so callers
  can detect and surface a suppressed duplicate. Not required once the attempt discriminator makes
  genuine retries always enqueue.
- **FU-2 (multipart reupload scan dedup):** the multipart (<=32 MB) reupload path re-enqueues
  `rag-scan:{doc}` / `knowmap-scan:{doc}` with the same document-only ID (`ingest_service.py:462`,
  `knowmap_ingest_service.py:273`), so a retained failed scan result can suppress a rescan there too.
  Lower impact (multipart re-indexes synchronously; only the rescan is at risk) and it owns no tus
  attempt counter, so it is deferred; a fix would give multipart its own scan-attempt discriminator.
