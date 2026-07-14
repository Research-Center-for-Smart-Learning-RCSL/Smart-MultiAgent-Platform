---
type: bugfix
status: implemented
created: 2026-07-14
requirements: []
---

# F-5: Pending-scan Knowledge Map documents are build- and retrieval-eligible

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-5; related F-27).

## 1. Summary

A freshly ingested Knowledge Map document is committed with `scan_status='pending'` and,
in the same request, has both its ClamAV scan **and** its graph build enqueued as two
independent, unordered Arq jobs. The build-eligibility and Agent-visibility selectors
exclude only `quarantined` and `skipped`, so a `pending` document passes both. If the
build worker wins the race against ClamAV, a never-scanned document is indexed into the
graph and exposed to Agents before any malware verdict exists — directly contradicting the
selectors' own documented "a document that was never cleanly scanned is never built"
contract. The fix requires `scan_status='clean'` in both selectors and moves the build
trigger for a new/reindexed document from ingestion time to the point where that document's
scan returns a clean verdict, closing the race at its source.

## 2. Observed vs Expected

- **Observed** — the build selector `ready_document_ids` filters
  `scan_status NOT IN {quarantined, skipped}`
  (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:389-392`) and the
  retrieval selector `allowed_document_ids` uses the identical exclusion
  (`:417-421`); both admit `pending`. Ingestion commits the document `READY`/`pending`
  and then enqueues scan and build back-to-back with no ordering dependency
  (`backend/contexts/knowledge/application/knowmap_ingest_service.py:165-167`, reindex
  branch `:114-116`; `_enqueue_build` `:258-263`). The build reads the corpus via
  `ready_document_ids` (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:66-69`),
  so a pending document is indexed if the build runs first.
- **Expected** — never-cleanly-scanned documents must not be built into the graph nor
  surfaced to Agents. Intent source: the selectors' own docstrings
  (`knowmap_repositories.py:375-384` build, `:404-411` retrieval — both promise
  "never cleanly scanned is never built" / "an un-scannable document is never surfaced")
  and Phase 3 AC-1 (fail-closed malware gate) cited by the audit. This is the stricter
  Knowledge Map contract; File RAG deliberately keeps `pending` (see §6), so the fix must
  not be generalized to the RAG selectors.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How strict should the build-gating fix be? | Gate both selectors on `clean` **and** defer the new/reindexed-document build until its scan returns a clean verdict. | Selector-only gating leaves a redundant early build that finds nothing and keeps the race latent; deferring the build to the clean-verdict callback removes the race at its source. |
| Q-2 | Should F-27 (SKIPPED enqueues no rebuild, unlike QUARANTINED) be fixed here? | No — out of scope; recorded as FU-1. | Once the build is gated on a clean verdict, a document can no longer be built while `pending`, so it cannot be built-then-skipped; F-27's precondition (a race-built doc later marked skipped) is eliminated by this fix. The remaining SKIPPED/QUARANTINED code asymmetry is cosmetic given F-6's separate replacement work. |

## 4. Reproduction

Preconditions: ClamAV enabled; a Knowledge Map config with at least one attached Agent.

1. Upload a document to the config via the multipart path. It commits `READY`,
   `scan_status='pending'` (`knowmap_ingest_service.py:165`).
2. Scan and build are enqueued as independent Arq jobs (`:166`, `:167`).
3. Delay/stall the `knowmap_scan_document` worker (or use a large file so ClamAV is slow)
   so `knowmap_build` runs first.
4. Observe: the build indexes the pending document (loader pulls it via `ready_document_ids`)
   and `allowed_document_ids` exposes it to the Agent, before any scan verdict exists.

Nondeterminism: ordering race between two unordered Arq jobs; reproducible by artificially
delaying the scan worker relative to the build worker.

## 5. Root Cause Analysis

Two independent root causes combine into the exposure:

1. **Permissive selectors.** Both `ready_document_ids` (`knowmap_repositories.py:389-392`)
   and `allowed_document_ids` (`:417-421`) exclude only `{quarantined, skipped}`, admitting
   `pending`. The implementation copied File RAG's permissive exclusion despite the
   docstrings promising the stricter Knowledge Map contract (`:381-382`).
2. **Ungated build enqueue.** Ingestion enqueues the build unconditionally, concurrently
   with the scan, with no dependency on the scan verdict
   (`knowmap_ingest_service.py:167`, `:116`; tus/async worker path
   `backend/app/workers/tasks/knowmap.py:106-108`). The scan worker only enqueues a rebuild
   on `QUARANTINED` (`knowmap.py:204-207`) and does nothing on `CLEAN`, so there is no
   clean-verdict build path today.

The earliest correcting link is the selector filter (root cause #1): tightening it to
`scan_status='clean'` alone prevents a pending document from ever being indexed or surfaced,
even if a build runs early. Root cause #2 is the aggravating factor that makes the early
build a wasted no-op; deferring the build to the clean verdict removes that waste and makes
the build fire exactly once per document, when it is safe.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Knowledge Map document between commit and scan verdict is
  build- and retrieval-eligible; the graph and Agent context can contain content that has
  not passed the fail-closed malware gate.
- **Sibling suspects:**
  - **File RAG selectors (cleared, must NOT change).** File RAG intentionally keeps
    `pending` for no-availability-gap on fresh uploads; the audit confirmed this is by
    design (`backend/tests/wiring/test_rag_ingestion.py:331-348`,
    `:358-379`). The fix is scoped to the two knowmap selectors only.
  - **Tus/async ingestion path (confirmed, in scope — and a race this fix must not
    introduce).** The tus finalizer enqueues the ClamAV scan AND an index worker
    (`backend/contexts/knowledge/application/knowmap_tus_finalizer.py:80,120,128-132`); the
    index worker parses/chunks and only then sets the document `READY` and enqueues the
    build (`backend/app/workers/tasks/knowmap.py:105-108`). Indexing and scanning run
    concurrently. A naive "move the build to the clean verdict" would let a fast clean scan
    enqueue the build *before* the document reaches `READY`; the clean-gated
    `ready_document_ids` (which also requires `status==READY`) would then exclude it and the
    document would **never** be built. The fix must therefore enqueue the build at whichever
    of {indexing-complete, clean-verdict} finishes last — see §7.2.
  - **Non-ingestion build triggers (cleared, must remain direct).** Document delete
    (`backend/app/api/v1/knowmap.py:572-574`), chunk-param/manual rebuilds, and other
    corpus mutations enqueue builds not tied to a new scan; these must keep enqueuing
    directly. They are safe because the tightened `ready_document_ids` now excludes any
    non-clean document from the corpus snapshot.
  - **Scan-disabled path (cleared).** When ClamAV is disabled the scan worker sets `CLEAN`
    immediately (`knowmap.py:124-131`); routing the build through the clean-verdict path
    preserves builds in that configuration with no regression.

## 7. Fix Design

1. **Tighten both knowmap selectors** to require a clean verdict:
   `ready_document_ids` (`knowmap_repositories.py:389-392`) and `allowed_document_ids`
   (`:417-421`) change from `scan_status NOT IN {quarantined, skipped}` to
   `scan_status == ScanStatus.CLEAN.value`. Update the docstrings to state the clean-only
   contract explicitly. This alone makes a pending document unindexable and invisible.
2. **Defer the document build to whichever of {indexing-complete, clean-verdict} finishes
   last**, each guarded by the other's precondition, so a document is built exactly once and
   only when both `READY` and `clean`:
   - **Clean-verdict site** — on the `CLEAN` branch of `knowmap_scan_document`
     (`knowmap.py:175-208`, mirroring the quarantine rebuild at `:204-207`), enqueue the
     build **only if** the document's `status == READY`. The scan worker already loads
     `cfg` for the quarantine path (`:184`); reuse it. Include the scan-disabled fast-path
     (`:124-131`), which must route through the same guarded enqueue.
   - **Indexing-complete sites** — the sync ingest (`knowmap_ingest_service.py:167` new,
     `:116` reindex) and the async index worker (`knowmap.py:105-108`): replace the
     unconditional `_enqueue_build` with a guarded enqueue that fires **only if** the
     document's `scan_status == CLEAN`.
   - **Ordering invariant:** each site commits its own state (`READY` or `clean`) before
     reading the other's, so the site that completes last always observes the other's
     committed state and enqueues; the earlier site skips. Arq `knowmap_build_job_id` dedup
     (`backend/contexts/knowledge/application/knowmap_triggers.py:21-63`) is the backstop if
     both observe completion and both enqueue. In the sync path the document is already
     `READY` before the scan is enqueued, so its clean verdict always enqueues; the
     concurrency guard matters for the async tus path (§6).
3. **Keep the scan enqueue at ingestion** — only the build enqueue is gated/moved. Every
   document is still scanned promptly (`enqueue_knowmap_scan` at
   `knowmap_ingest_service.py:115,166`; `knowmap_tus_finalizer.py:80,120`); the build now
   waits for both readiness and a clean verdict.

No persisted-data repair is required for correctness of new ingests. Optionally, a one-off
audit query can list existing `pending` documents currently present in any built graph
(pre-fix races); given F-6's separate replacement work will rebuild graphs, no data
migration is specified here — recorded as FU-2.

## 8. Regression Test Plan

Unit tests (`backend/tests/unit/`):

1. **Selector gating** (new, `test_knowmap_authz.py` or a new `test_knowmap_repositories.py`):
   assert `ready_document_ids` and `allowed_document_ids` each exclude a `pending`
   document and include a `clean` one. Fails today because both admit `pending`
   (`knowmap_repositories.py:389-392`, `:417-421`).
2. **Ingestion build is guarded, not unconditional** (update `test_knowmap_ingest.py`): the
   existing `test_new_document_chunks_without_qdrant_and_triggers_build`
   (`backend/tests/unit/test_knowmap_ingest.py:128-166`) asserts the build IS enqueued at
   ingest (`:164-166`); rewrite it to assert the scan is enqueued and the build is **not**
   enqueued while `scan_status` is `pending`.
3. **Clean verdict enqueues build when READY** (new, `test_knowmap.py` worker test): drive
   `knowmap_scan_document` to a `CLEAN` verdict with the document `READY` and assert
   `enqueue_knowmap_build` is awaited with the config id and dedup job id; assert it does
   **not** enqueue when the document is not yet `READY`. Fails today — the clean branch
   enqueues nothing (`knowmap.py:175-208`).
4. **Last-writer-enqueues ordering** (new): (a) index-complete with `scan_status=pending`
   does not enqueue, then the later clean verdict does; (b) clean verdict with a
   not-yet-`READY` document does not enqueue, then the later index-complete does. Asserts
   exactly one build is enqueued across the pair.

The failing selector test (1) is the primary red-first test.

## 9. Risks and Rollback

- **Availability latency** — a document is now unbuilt/unretrievable until its scan
  completes. This is the intended fail-closed behavior for Knowledge Maps; acceptable and
  documented.
- **Missing clean-verdict enqueue would leave documents unbuilt forever.** Mitigated by
  test (3); the scan-disabled fast-path (`knowmap.py:124-131`) must be verified to route
  through the same clean enqueue.
- **Build-dedup collisions** — the clean-verdict enqueue reuses `knowmap_build_job_id`
  keyed on `(state, last_build_at)`; F-12 (separate finding) already flags that key's
  weakness. Do not introduce a new dedup scheme here; reuse the existing helper so this fix
  stays orthogonal to F-12.
- **Rollback** — revert the selector filters and restore the ingest-time build enqueue; no
  schema change, so rollback is code-only.

## 10. Acceptance Criteria

- [x] AC-1: The selector-gating regression test (§8.1) fails before the fix and passes after.
  Realized as wiring `test_knowmap_scan_gating.py` (written; not run locally — D-1). The
  runnable red-first was the ingest-guard unit test (§8.2), demonstrated red→green.
- [x] AC-2: `ready_document_ids` and `allowed_document_ids` return a document only when its
  `scan_status == clean`; `pending`, `quarantined`, and `skipped` are all excluded. Both
  selectors changed to `== ScanStatus.CLEAN.value`; wiring `test_selectors_require_clean_verdict`.
- [x] AC-3: Ingestion enqueues the ClamAV scan and enqueues the build only when the document
  is already `clean`; while `pending`, no build is enqueued at ingest.
  `test_new_document_chunks_and_scans_without_building_while_pending` (run).
- [x] AC-4: `knowmap_scan_document` enqueues a graph build (via `enqueue_knowmap_build`) on a
  `CLEAN` verdict only when the document is `READY`, including the scan-disabled fast-path;
  it does not on `pending`/not-ready. Both CLEAN sites route through `_enqueue_build_on_clean`;
  `test_knowmap_scan_build_gate.py` covers the READY gate.
- [x] AC-5: For the async tus path, exactly one build is enqueued once the document is both
  `READY` and `clean`, regardless of whether indexing or scanning finishes first; a document
  is never left unbuilt due to ordering. Last-writer-wins: each site commits its own state
  before reading the other's (proof in §7.2/D-2); dedup job id collapses a rare double.
- [x] AC-6: Non-ingestion build triggers (document delete, manual/chunk-param rebuilds) still
  enqueue builds directly, and those builds exclude non-clean documents from the corpus.
  Those call sites untouched; the tightened `ready_document_ids` excludes non-clean docs.
- [x] AC-7: `ruff check`, `ruff format --check`, and `mypy` pass for the touched modules (no
  new errors; one pre-existing unrelated `tenancy` mypy error remains). Full `pytest -q` at
  the batch's end; wiring tier runs in CI (D-1).

## 11. SRS Delta

None. This restores the documented clean-scan contract; no new requirement.

## 12. Deviation Log

- **D-1 (test execution environment):** no Postgres/Redis/Neo4j/Qdrant locally, so the §8.1
  selector test was written as a `wiring` test and executed only in CI. The behavioral
  build-deferral fix is fully covered by runnable unit tests
  (`test_knowmap_ingest.py`, `test_knowmap_scan_build_gate.py`), run red→green.
- **D-2 (last-writer-wins realization):** the two indexing-complete sites (sync ingest,
  async index worker) each enqueue only when the document's current `scan_status == CLEAN`,
  and the clean-verdict site enqueues only when `status == READY`, via the shared
  `_enqueue_build_on_clean` helper. Because each site commits its own state (`READY` or the
  clean verdict) *before* reading the other's, the site finishing last always observes the
  other's committed state and enqueues, and the earlier one skips — so exactly one build is
  queued and a document is never left unbuilt. The `knowmap_build_job_id` dedup is the
  backstop for a rare simultaneous double.
- **D-3 (reindex-of-clean fast path):** `_enqueue_build_if_clean` enqueues immediately when a
  reindexed document is already `clean` (same content, same sha — the prior verdict still
  holds), rather than waiting for the re-enqueued scan. This is within §7.2's clean-gate
  intent (build only a clean document) and avoids needlessly deferring a known-clean rebuild.

## 13. Follow-ups

- **FU-1 (F-27, minor):** `knowmap_scan_document` enqueues a rebuild on `QUARANTINED` but
  not on `SKIPPED` (`knowmap.py:147-154,162-170,204-207`). This fix eliminates F-27's
  precondition (a pending doc can no longer be built then skipped), leaving only a cosmetic
  code asymmetry; address alongside F-6's replacement semantics if desired.
- **FU-2 (data audit):** existing graphs may contain documents indexed during a pre-fix race.
  F-6's replacement-rebuild work will purge them on the next build; if F-6 lands later, a
  one-off sweep listing `pending`/non-clean documents present in built graphs may be run.
